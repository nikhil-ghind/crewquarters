/**
 * Live run and model updates on top of the resilient SSE helper. Events are merged
 * into the TanStack Query cache, so every component reading the run, its timeline or
 * the model list sees the same data, and a reload simply resumes from history.
 */
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { api, unwrap } from './client';
import { keys } from './queries';
import { isTerminal, type ModelOut, type RunEventOut, type RunOut, type RunState } from './schema';
import { subscribe, type StreamStatus } from './sse';

/** Event types from packages/contracts/events/run-event.schema.json. */
export const RUN_EVENT_TYPES = [
  'run.state_changed',
  'run.log',
  'run.progress',
  'run.metric',
  'run.artifact',
  'run.input_requested',
  'run.input_answered',
  'run.input_closed',
  'run.result',
  'run.error',
] as const;

export function parseRunEvent(data: string): RunEventOut | null {
  try {
    const value = JSON.parse(data) as Partial<RunEventOut>;
    if (typeof value.sequence !== 'number' || typeof value.type !== 'string') return null;
    return {
      runId: String(value.runId ?? ''),
      sequence: value.sequence,
      attempt: typeof value.attempt === 'number' ? value.attempt : 0,
      type: value.type,
      payload: (value.payload ?? {}),
      createdAt: String(value.createdAt ?? new Date().toISOString()),
    };
  } catch {
    return null;
  }
}

async function loadHistory(runId: string): Promise<RunEventOut[]> {
  const out: RunEventOut[] = [];
  let after = 0;
  for (let page = 0; page < 20; page++) {
    const batch = await unwrap(
      api.GET('/api/v1/runs/{run_id}/events/history', {
        params: { path: { run_id: runId }, query: { after, limit: 500 } },
      }),
    );
    out.push(...batch);
    if (batch.length < 500) break;
    after = batch[batch.length - 1]?.sequence ?? after;
  }
  return out;
}

/** Append events in sequence order, ignoring any sequence already present. */
export function mergeEvents(existing: RunEventOut[] | undefined, incoming: RunEventOut[]): RunEventOut[] {
  const list = existing ? [...existing] : [];
  const seen = new Set(list.map((e) => e.sequence));
  let changed = false;
  for (const event of incoming) {
    if (seen.has(event.sequence)) continue;
    seen.add(event.sequence);
    list.push(event);
    changed = true;
  }
  if (!changed) return existing ?? list;
  return list.sort((a, b) => a.sequence - b.sequence);
}

function applyEvent(client: QueryClient, runId: string, event: RunEventOut): void {
  client.setQueryData<RunEventOut[]>(keys.runEvents(runId), (old) => mergeEvents(old, [event]));
  if (event.type === 'run.state_changed') {
    const to = event.payload.to as RunState | undefined;
    if (to) {
      client.setQueryData<RunOut>(keys.run(runId), (old) => (old ? { ...old, state: to } : old));
      // Re-read the authoritative record (result, error, timestamps).
      void client.invalidateQueries({ queryKey: keys.run(runId) });
      void client.invalidateQueries({ queryKey: keys.runsAll });
      if (to === 'WAITING_INPUT' || isTerminal(to)) {
        void client.invalidateQueries({ queryKey: keys.attention });
      }
    }
  }
  if (event.type.startsWith('run.input_')) {
    void client.invalidateQueries({ queryKey: keys.inputRequestsAll });
    void client.invalidateQueries({ queryKey: keys.attention });
    void client.invalidateQueries({ queryKey: keys.run(runId) });
  }
  if (event.type === 'run.result' || event.type === 'run.error') {
    void client.invalidateQueries({ queryKey: keys.run(runId) });
  }
}

/**
 * Run timeline: history first, then SSE from the last sequence. Reconnects resume
 * with Last-Event-ID / ?after= and duplicates are dropped, so nothing is shown or
 * started twice. Falls back to bounded polling of /events/history.
 */
export function useRunEvents(runId: string, runState: RunState | undefined) {
  const client = useQueryClient();
  const [status, setStatus] = useState<StreamStatus>('connecting');
  const history = useQuery({
    queryKey: keys.runEvents(runId),
    queryFn: () => loadHistory(runId),
    staleTime: Infinity,
  });
  const terminal = runState ? isTerminal(runState) : false;
  const known = runState !== undefined;
  const loaded = history.isSuccess;
  const terminalSeen = useRef(false);

  useEffect(() => {
    if (!loaded || !known || terminal) {
      if (terminal) setStatus('ended');
      return;
    }
    terminalSeen.current = false;
    const events = client.getQueryData<RunEventOut[]>(keys.runEvents(runId)) ?? [];
    const cursor = events.length > 0 ? (events[events.length - 1]?.sequence ?? 0) : 0;
    const sub = subscribe<RunEventOut>({
      url: (after) => `/api/v1/runs/${encodeURIComponent(runId)}/events?after=${after}`,
      eventTypes: RUN_EVENT_TYPES,
      parse: parseRunEvent,
      sequence: (e) => e.sequence,
      initialCursor: cursor,
      poll: (after) =>
        unwrap(
          api.GET('/api/v1/runs/{run_id}/events/history', {
            params: { path: { run_id: runId }, query: { after, limit: 200 } },
          }),
        ),
      onItem: (event) => {
        if (event.type === 'run.state_changed' && isTerminal(event.payload.to as RunState)) {
          terminalSeen.current = true;
        }
        applyEvent(client, runId, event);
      },
      doneWhenIdle: () => terminalSeen.current,
      onStatus: (s) => {
        setStatus(s);
        if (s === 'ended') {
          void client.invalidateQueries({ queryKey: keys.run(runId) });
        }
      },
    });
    return () => sub.close();
    // Re-subscribe when a retry takes a terminal run back to QUEUED.
  }, [client, runId, loaded, terminal, known]);

  return { events: history.data ?? [], history, status };
}

function parseModel(data: string): ModelOut | null {
  try {
    const value = JSON.parse(data) as ModelOut;
    return typeof value.id === 'string' ? value : null;
  } catch {
    return null;
  }
}

export function storeModel(client: QueryClient, model: ModelOut): void {
  client.setQueryData<ModelOut>(keys.model(model.id), model);
  client.setQueryData<ModelOut[]>(keys.models, (old) =>
    old ? old.map((m) => (m.id === model.id ? model : m)) : old,
  );
}

/**
 * Download/load progress for one model while it is changing. Progress survives
 * navigation and refresh because the state lives on the server; this only speeds up
 * updates between polls.
 */
export function useModelEvents(modelId: string | undefined, active: boolean) {
  const client = useQueryClient();
  const [status, setStatus] = useState<StreamStatus>('closed');
  useEffect(() => {
    if (!modelId || !active) {
      setStatus('closed');
      return;
    }
    const sub = subscribe<ModelOut>({
      url: () => `/api/v1/models/${encodeURIComponent(modelId)}/events`,
      eventTypes: ['model.state'],
      parse: parseModel,
      sequence: () => null,
      poll: async () => [
        await unwrap(api.GET('/api/v1/models/{model_id}', { params: { path: { model_id: modelId } } })),
      ],
      onItem: (model) => storeModel(client, model),
      onStatus: setStatus,
      pollsBeforeRetry: 10,
    });
    return () => sub.close();
  }, [client, modelId, active]);
  return status;
}
