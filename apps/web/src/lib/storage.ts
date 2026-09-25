/**
 * Browser persistence for non-sensitive preferences and unsaved non-secret drafts only
 * (section 13.19). Secrets, tokens and answers to agent questions are never stored.
 */
import { useCallback, useState } from 'react';

const PREFIX = 'cq.';

export function readPref<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(PREFIX + key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export function writePref(key: string, value: unknown): void {
  try {
    if (value === undefined) window.localStorage.removeItem(PREFIX + key);
    else window.localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    // Storage may be unavailable (private mode); preferences are best effort.
  }
}

export function usePref<T>(key: string, fallback: T): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => readPref(key, fallback));
  const update = useCallback(
    (next: T) => {
      setValue(next);
      writePref(key, next);
    },
    [key],
  );
  return [value, update];
}

/** Session-scoped draft of a non-secret form (survives refresh and OAuth redirects). */
export function readDraft<T>(key: string): T | null {
  try {
    const raw = window.sessionStorage.getItem(PREFIX + 'draft.' + key);
    return raw === null ? null : (JSON.parse(raw) as T);
  } catch {
    return null;
  }
}

export function writeDraft(key: string, value: unknown): void {
  try {
    if (value === null || value === undefined) {
      window.sessionStorage.removeItem(PREFIX + 'draft.' + key);
    } else {
      window.sessionStorage.setItem(PREFIX + 'draft.' + key, JSON.stringify(value));
    }
  } catch {
    // best effort
  }
}
