/**
 * Browser-side session state. The session itself is the HttpOnly `cq_session` cookie
 * set by the control API; the browser only keeps the CSRF token (from SessionOut or
 * the readable `cq_csrf` cookie) and whether it believes it is signed in. Auth
 * decisions stay on the server: a 401 from any call flips this store to "expired" and
 * the router sends the operator to /login.
 */
import { useSyncExternalStore } from 'react';

export type SessionStatus = 'unknown' | 'authenticated' | 'anonymous' | 'expired';

interface State {
  status: SessionStatus;
  csrfToken: string | null;
}

let state: State = { status: 'unknown', csrfToken: null };
const listeners = new Set<() => void>();

function emit(next: State): void {
  state = next;
  for (const listener of listeners) listener();
}

export const CSRF_COOKIE = 'cq_csrf';
export const CSRF_HEADER = 'X-CSRF-Token';

function readCookie(name: string): string | null {
  if (typeof document === 'undefined') return null;
  for (const part of document.cookie.split(';')) {
    const [key, ...rest] = part.trim().split('=');
    if (key === name) return decodeURIComponent(rest.join('='));
  }
  return null;
}

export const session = {
  get(): State {
    return state;
  },
  csrfToken(): string | null {
    return state.csrfToken ?? readCookie(CSRF_COOKIE);
  },
  signedIn(csrfToken: string): void {
    emit({ status: 'authenticated', csrfToken });
  },
  anonymous(): void {
    emit({ status: 'anonymous', csrfToken: null });
  },
  expired(): void {
    if (state.status === 'anonymous') return;
    emit({ status: 'expired', csrfToken: null });
  },
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  /** Test helper. */
  reset(): void {
    emit({ status: 'unknown', csrfToken: null });
  },
};

export function useSessionStatus(): SessionStatus {
  return useSyncExternalStore((l) => session.subscribe(l), () => state.status);
}
