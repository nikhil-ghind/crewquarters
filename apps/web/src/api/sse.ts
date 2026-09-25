/**
 * Resilient server-sent-event subscriptions (PLAN.md sections 13.7, 13.19).
 *
 * - Uses the browser's EventSource (same origin, session cookie sent automatically;
 *   GET streams need no CSRF header).
 * - Stores the latest sequence id. EventSource itself resends it as `Last-Event-ID`
 *   when it reconnects; if we have to open a new EventSource we pass `?after=<seq>`.
 * - Deduplicates by sequence, so a reconnect or overlapping poll never repeats a
 *   timeline entry and never re-triggers anything.
 * - After repeated failures it falls back to bounded polling of the JSON history
 *   endpoint (growing intervals, a fixed number of polls), then tries SSE again.
 * - Reports "reconnecting"/"polling" to the connectivity store for the amber banner.
 */
import { connectivity } from './connectivity';

export type StreamStatus = 'connecting' | 'live' | 'reconnecting' | 'polling' | 'ended' | 'closed';

export interface StreamOptions<T> {
  /** SSE URL for a given cursor (0 = from the start). */
  url: (cursor: number) => string;
  /** Named SSE event types to listen to (named events do not fire `onmessage`). */
  eventTypes: readonly string[];
  /** Decode one SSE `data` payload; return null to ignore it. */
  parse: (data: string) => T | null;
  /** Monotonic sequence for dedupe/cursor, or null if the stream has none. */
  sequence: (item: T) => number | null;
  /** JSON fallback: items after the cursor. */
  poll: (cursor: number) => Promise<T[]>;
  /** True if this item means nothing more will arrive. */
  isEnd?: (item: T) => boolean;
  /** Polling mode: after an empty poll, true means the stream is complete. */
  doneWhenIdle?: () => boolean;
  onItem: (item: T) => void;
  onStatus?: (status: StreamStatus) => void;
  initialCursor?: number;
  maxErrorsBeforePolling?: number;
  pollIntervalsMs?: readonly number[];
  pollsBeforeRetry?: number;
}

export interface Subscription {
  close(): void;
  /** Latest sequence delivered. */
  cursor(): number;
}

export const DEFAULT_POLL_INTERVALS_MS = [2000, 3000, 5000, 8000, 10000] as const;

export function subscribe<T>(options: StreamOptions<T>): Subscription {
  const {
    maxErrorsBeforePolling = 3,
    pollIntervalsMs = DEFAULT_POLL_INTERVALS_MS,
    pollsBeforeRetry = 6,
  } = options;
  const id = Symbol('stream');
  let cursor = options.initialCursor ?? 0;
  let source: EventSource | null = null;
  let status: StreamStatus = 'connecting';
  let errors = 0;
  let closed = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const seen = new Set<number>();

  const setStatus = (next: StreamStatus) => {
    if (status === next) return;
    status = next;
    connectivity.streamReconnecting(id, next === 'reconnecting' || next === 'polling');
    options.onStatus?.(next);
  };

  const deliver = (item: T): boolean => {
    const seq = options.sequence(item);
    if (seq !== null) {
      if (seq <= cursor || seen.has(seq)) return false;
      seen.add(seq);
      cursor = seq;
    }
    options.onItem(item);
    if (options.isEnd?.(item)) {
      finish('ended');
      return false;
    }
    return true;
  };

  const finish = (final: 'ended' | 'closed') => {
    if (closed) return;
    closed = true;
    source?.close();
    source = null;
    if (timer) clearTimeout(timer);
    timer = null;
    setStatus(final);
    connectivity.streamReconnecting(id, false);
  };

  const handle = (event: MessageEvent<string>) => {
    if (closed) return;
    errors = 0;
    setStatus('live');
    const item = options.parse(event.data);
    if (item !== null) deliver(item);
  };

  const open = () => {
    if (closed) return;
    const es = new EventSource(options.url(cursor));
    source = es;
    es.onopen = () => {
      errors = 0;
      setStatus('live');
    };
    for (const type of options.eventTypes) {
      es.addEventListener(type, handle as EventListener);
    }
    es.addEventListener('end', () => finish('ended'));
    es.onerror = () => {
      if (closed) return;
      errors += 1;
      // CLOSED means the browser gave up (e.g. non-200 response); CONNECTING means it
      // is retrying with Last-Event-ID on its own.
      if (es.readyState === EventSource.CLOSED || errors >= maxErrorsBeforePolling) {
        es.close();
        source = null;
        startPolling();
      } else {
        setStatus('reconnecting');
      }
    };
  };

  const startPolling = () => {
    setStatus('polling');
    let polls = 0;
    const tick = async () => {
      if (closed) return;
      try {
        const items = await options.poll(cursor);
        for (const item of items) {
          if (!deliver(item)) break;
        }
        if (items.length === 0 && options.doneWhenIdle?.()) {
          finish('ended');
          return;
        }
      } catch {
        // Keep the bounded schedule; the global banner shows the outage.
      }
      if (closed) return;
      polls += 1;
      if (polls >= pollsBeforeRetry) {
        errors = 0;
        setStatus('reconnecting');
        open();
        return;
      }
      const delay = pollIntervalsMs[Math.min(polls, pollIntervalsMs.length - 1)] ?? 10_000;
      timer = setTimeout(() => void tick(), delay);
    };
    void tick();
  };

  open();

  return {
    close: () => finish('closed'),
    cursor: () => cursor,
  };
}

/**
 * Parse a text/event-stream body from fetch (used for POST streams such as chat
 * messages, which EventSource cannot send).
 */
export async function* readEventStream(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<{ event: string; data: string; id: string | null }> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n?/g, '\n');
      let index = buffer.indexOf('\n\n');
      while (index !== -1) {
        const block = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        const parsed = parseBlock(block);
        if (parsed) yield parsed;
        index = buffer.indexOf('\n\n');
      }
    }
    const tail = parseBlock(buffer);
    if (tail) yield tail;
  } finally {
    reader.releaseLock();
  }
}

function parseBlock(block: string): { event: string; data: string; id: string | null } | null {
  let event = 'message';
  let id: string | null = null;
  const data: string[] = [];
  for (const line of block.split('\n')) {
    if (!line || line.startsWith(':')) continue;
    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    const value = colon === -1 ? '' : line.slice(colon + 1).replace(/^ /, '');
    if (field === 'event') event = value;
    else if (field === 'data') data.push(value);
    else if (field === 'id') id = value;
  }
  if (data.length === 0) return null;
  return { event, data: data.join('\n'), id };
}
