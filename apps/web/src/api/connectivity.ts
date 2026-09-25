/**
 * Global degraded/offline signals (PLAN.md section 13.13):
 * - `api`: flips to "offline" when a request cannot reach the device and back to
 *   "online" on the next successful response;
 * - `streams`: how many live SSE subscriptions are currently reconnecting, which drives
 *   the amber "Live updates paused—reconnecting" banner.
 */
import { useSyncExternalStore } from 'react';

interface State {
  api: 'online' | 'offline';
  lastOkAt: number | null;
  reconnecting: number;
}

let state: State = { api: 'online', lastOkAt: null, reconnecting: 0 };
const listeners = new Set<() => void>();
const reconnectingStreams = new Set<symbol>();

function emit(next: State): void {
  if (
    next.api === state.api &&
    next.reconnecting === state.reconnecting &&
    next.lastOkAt === state.lastOkAt
  ) {
    return;
  }
  state = next;
  for (const l of listeners) l();
}

export const connectivity = {
  get(): State {
    return state;
  },
  ok(): void {
    // Only record the transition; avoid re-rendering on every response.
    if (state.api !== 'online' || state.lastOkAt === null) {
      emit({ ...state, api: 'online', lastOkAt: Date.now() });
    }
  },
  failed(): void {
    emit({ ...state, api: 'offline' });
  },
  streamReconnecting(id: symbol, reconnecting: boolean): void {
    if (reconnecting) reconnectingStreams.add(id);
    else reconnectingStreams.delete(id);
    emit({ ...state, reconnecting: reconnectingStreams.size });
  },
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  reset(): void {
    reconnectingStreams.clear();
    emit({ api: 'online', lastOkAt: null, reconnecting: 0 });
  },
};

export function useConnectivity(): State {
  return useSyncExternalStore((l) => connectivity.subscribe(l), () => state);
}
