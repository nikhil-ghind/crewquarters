import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { connectivity } from './connectivity';
import { readEventStream, subscribe } from './sse';

/** Minimal controllable EventSource. */
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;
  readonly CONNECTING = 0;
  readonly OPEN = 1;
  readonly CLOSED = 2;
  readyState = 0;
  url: string;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, ((e: MessageEvent<string>) => void)[]>();
  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (e: MessageEvent<string>) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
  }
  close() {
    this.readyState = 2;
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  emit(type: string, data: unknown, id?: string) {
    for (const fn of this.listeners.get(type) ?? []) fn(new MessageEvent(type, { data: JSON.stringify(data), lastEventId: id }));
  }
  fail(closed = false) {
    this.readyState = closed ? 2 : 0;
    this.onerror?.();
  }
}

interface Item {
  sequence: number;
  type: string;
}

function setup(poll = vi.fn(async (_cursor: number) => [] as Item[])) {
  const items: Item[] = [];
  const statuses: string[] = [];
  const sub = subscribe<Item>({
    url: (after) => `/events?after=${after}`,
    eventTypes: ['run.progress', 'run.state_changed'],
    parse: (d) => JSON.parse(d) as Item,
    sequence: (i) => i.sequence,
    poll,
    onItem: (i) => items.push(i),
    onStatus: (s) => statuses.push(s),
    initialCursor: 2,
    pollIntervalsMs: [10, 10],
    pollsBeforeRetry: 3,
  });
  return { sub, items, statuses, poll };
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('subscribe', () => {
  it('starts after the known cursor and drops duplicate or old sequences', () => {
    const { items, sub } = setup();
    const es = FakeEventSource.instances[0]!;
    expect(es.url).toBe('/events?after=2');
    es.open();
    es.emit('run.progress', { sequence: 2, type: 'run.progress' });
    es.emit('run.progress', { sequence: 3, type: 'run.progress' });
    es.emit('run.progress', { sequence: 3, type: 'run.progress' });
    es.emit('run.state_changed', { sequence: 4, type: 'run.state_changed' });
    expect(items.map((i) => i.sequence)).toEqual([3, 4]);
    expect(sub.cursor()).toBe(4);
    sub.close();
  });

  it('reports reconnecting while the browser retries, and live again after', () => {
    const { statuses, sub } = setup();
    const es = FakeEventSource.instances[0]!;
    es.open();
    es.fail();
    expect(statuses).toContain('reconnecting');
    expect(connectivity.get().reconnecting).toBe(1);
    es.open();
    expect(statuses[statuses.length - 1]).toBe('live');
    expect(connectivity.get().reconnecting).toBe(0);
    sub.close();
  });

  it('falls back to bounded polling from the last cursor, then retries SSE', async () => {
    const poll = vi.fn(async (cursor: number) => (cursor < 5 ? [{ sequence: 5, type: 'run.progress' }] : []));
    const { items, statuses, sub } = setup(poll);
    const es = FakeEventSource.instances[0]!;
    es.open();
    es.emit('run.progress', { sequence: 4, type: 'run.progress' });
    es.fail(true); // the browser gave up (e.g. 503)
    expect(statuses).toContain('polling');
    await vi.waitFor(() => expect(poll).toHaveBeenCalledTimes(3));
    expect(poll.mock.calls[0]?.[0]).toBe(4);
    expect(items.map((i) => i.sequence)).toEqual([4, 5]);
    await vi.waitFor(() => expect(FakeEventSource.instances).toHaveLength(2));
    // The new EventSource resumes after the last delivered sequence.
    expect(FakeEventSource.instances[1]!.url).toBe('/events?after=5');
    sub.close();
    expect(connectivity.get().reconnecting).toBe(0);
  });

  it('ends on the server "end" event', () => {
    const { statuses, sub } = setup();
    const es = FakeEventSource.instances[0]!;
    es.open();
    es.emit('end', { state: 'SUCCEEDED' });
    expect(statuses[statuses.length - 1]).toBe('ended');
    expect(es.readyState).toBe(2);
    sub.close();
  });
});

describe('readEventStream', () => {
  it('parses SSE blocks split across chunks, ignoring comments', async () => {
    const encoder = new TextEncoder();
    const chunks = ['retry: 2000\n\n: keepalive\n\nevent: message\ndata: {"a"', ':1}\n\nevent: delta\ndata: {"text":"Hi"}\n\n'];
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const c of chunks) controller.enqueue(encoder.encode(c));
        controller.close();
      },
    });
    const events = [];
    for await (const e of readEventStream(body)) events.push(e);
    expect(events).toEqual([
      { event: 'message', data: '{"a":1}', id: null },
      { event: 'delta', data: '{"text":"Hi"}', id: null },
    ]);
  });
});
