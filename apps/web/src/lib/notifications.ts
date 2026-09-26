/**
 * Optional browser notifications for Crew Requests (PLAN.md section 13.7, WAITING_INPUT:
 * "optional browser notification").
 *
 * - Opt-in per browser: the choice is a non-sensitive preference in localStorage, and
 *   `Notification.requestPermission()` is only ever called from an explicit click.
 * - Detection reuses the pending input-request query (TanStack Query). The first result
 *   a tab sees seeds its "seen" set, so requests that already existed never notify.
 * - One notification per new request, only while the tab is hidden or not focused.
 *   Multiple tabs coordinate in two layers: a BroadcastChannel tells the other tabs
 *   that a request was already notified (or seen in a focused tab), and the
 *   notification `tag` is the request id, so if two tabs race the browser still shows
 *   a single notification (the later one silently replaces the earlier).
 * - A tab closes the notifications it created when their request leaves the pending
 *   list (answered on another device, cancelled or expired).
 */
import { useCallback, useSyncExternalStore } from 'react';
import type { InputRequestOut } from '../api/schema';
import { readPref, writePref } from './storage';

export type NotificationPermissionState =
  /** The browser has no Notification API. */
  | 'unsupported'
  /** Not a secure context (e.g. http://<LAN IP>:8080): browsers refuse notifications. */
  | 'insecure'
  | 'default'
  | 'granted'
  | 'denied';

const PREF_KEY = 'notify.inputRequests';
export const PROMPT_DISMISSED_KEY = 'notify.promptDismissed';
export const CHANNEL_NAME = 'cq.input-request-notifications';
export const NOTIFICATION_TITLE = 'Needs your input';
const BODY_MAX = 120;

/**
 * Set when `new Notification()` throws even though permission is granted: Chrome on
 * Android (and iOS outside a home-screen app) only shows notifications from a service
 * worker, which Crewquarters does not register.
 */
const REFUSED_KEY = 'notify.constructorRefused';
let constructorRefused = readPref<boolean>(REFUSED_KEY, false);

export function notificationPermission(): NotificationPermissionState {
  if (typeof window === 'undefined') return 'unsupported';
  if (window.isSecureContext === false) return 'insecure';
  if (typeof window.Notification !== 'function' || constructorRefused) return 'unsupported';
  const p = window.Notification.permission;
  return p === 'granted' || p === 'denied' ? p : 'default';
}

// --- Preference store (shared by Settings, the Crew Requests prompt and the shell) ------

const listeners = new Set<() => void>();
let listening = false;

function emit(): void {
  listeners.forEach((l) => l());
}

function startListening(): void {
  if (listening || typeof window === 'undefined') return;
  listening = true;
  // Another tab changed the preference, or the owner changed site settings and came back.
  window.addEventListener('storage', emit);
  window.addEventListener('focus', emit);
  document.addEventListener('visibilitychange', emit);
  try {
    void navigator.permissions
      ?.query({ name: 'notifications' })
      .then((status) => {
        status.onchange = emit;
      })
      .catch(() => undefined);
  } catch {
    // The Permissions API is optional.
  }
}

function subscribe(listener: () => void): () => void {
  startListening();
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function snapshot(): string {
  return `${notificationPermission()}|${readPref<boolean>(PREF_KEY, false) ? 1 : 0}`;
}

export interface NotificationSettings {
  permission: NotificationPermissionState;
  /** The owner asked for notifications in this browser. */
  wanted: boolean;
  /** Wanted and allowed: notifications will be shown. */
  active: boolean;
  /** Must be called from a click: may show the browser's permission prompt. */
  enable: () => Promise<NotificationPermissionState>;
  disable: () => void;
}

export function useNotificationSettings(): NotificationSettings {
  const snap = useSyncExternalStore(subscribe, snapshot, () => 'unsupported|0');
  const [permission, wantedFlag] = snap.split('|') as [NotificationPermissionState, string];
  const wanted = wantedFlag === '1';

  const enable = useCallback(async (): Promise<NotificationPermissionState> => {
    let state = notificationPermission();
    if (state === 'default') {
      try {
        // Older Safari returns undefined and only calls back; re-read the permission then.
        const result = (await window.Notification.requestPermission()) as NotificationPermission | undefined;
        state = result === 'granted' || result === 'denied' ? result : notificationPermission();
      } catch {
        state = notificationPermission();
      }
    }
    if (state === 'granted') writePref(PREF_KEY, true);
    emit();
    return state;
  }, []);

  const disable = useCallback(() => {
    writePref(PREF_KEY, false);
    emit();
  }, []);

  return { permission, wanted, active: wanted && permission === 'granted', enable, disable };
}

/** Re-read permission and preference (tests, and after external changes). */
export function refreshNotificationSettings(): void {
  constructorRefused = readPref<boolean>(REFUSED_KEY, false);
  emit();
}

// --- Notification content ----------------------------------------------------------------

/** Plain, single-line text: control characters and runs of whitespace become one space. */
export function plainText(value: string, max = BODY_MAX): string {
  // eslint-disable-next-line no-control-regex
  const flat = value.replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]+/g, ' ').replace(/\s+/g, ' ').trim();
  const chars = Array.from(flat);
  return chars.length > max ? `${chars.slice(0, max - 1).join('').trimEnd()}…` : flat;
}

/**
 * Body: "<agent>: <title>". Only fields the API exposes for display (agentName, title,
 * or the prompt when there is no title). Never the preview, schema or answer.
 */
export function notificationBody(request: Pick<InputRequestOut, 'agentName' | 'title' | 'prompt'>): string {
  const agent = plainText(request.agentName ?? '', 40) || 'An agent';
  const what = request.title.trim() ? request.title : request.prompt;
  return plainText(`${agent}: ${what}`);
}

export function requestPath(request: Pick<InputRequestOut, 'runId'>): string {
  return `/runs/${encodeURIComponent(request.runId)}`;
}

// --- Detection ---------------------------------------------------------------------------

export interface NotificationHandle {
  close(): void;
  onclick: ((ev: Event) => unknown) | null;
}

export type ChannelMessage = { type: 'notified' | 'seen'; id: string };

export interface ChannelLike {
  postMessage(message: ChannelMessage): void;
}

export interface NotifierDeps {
  /** Whether notifications are wanted and allowed right now. */
  active: () => boolean;
  /** True when the owner is not looking at this tab (hidden or not focused). */
  inBackground: () => boolean;
  /** Create a notification; return null when the browser refuses. */
  show: (title: string, options: NotificationOptions) => NotificationHandle | null;
  /** Clicked: focus the window and go to the request. */
  open: (request: InputRequestOut) => void;
  channel?: ChannelLike | null;
}

export class InputRequestNotifier {
  private seen: Set<string> | null = null;
  /** Requests another tab already notified or showed in the foreground. */
  private readonly handled = new Set<string>();
  private readonly open = new Map<string, NotificationHandle>();
  private readonly deps: NotifierDeps;
  private channel: ChannelLike | null;

  constructor(deps: NotifierDeps) {
    this.deps = deps;
    this.channel = deps.channel ?? null;
  }

  setChannel(channel: ChannelLike | null): void {
    this.channel = channel;
  }

  /** Feed the latest pending list. Returns the ids that were notified. */
  update(pending: InputRequestOut[]): string[] {
    const ids = new Set(pending.map((r) => r.id));
    for (const [id, handle] of this.open) {
      if (!ids.has(id)) {
        this.open.delete(id);
        handle.close();
      }
    }
    if (this.seen === null) {
      // First load: whatever is already pending was there before this tab looked.
      this.seen = ids;
      return [];
    }
    const notified: string[] = [];
    for (const request of pending) {
      if (this.seen.has(request.id)) continue;
      this.seen.add(request.id);
      if (this.handled.has(request.id) || !this.deps.active()) continue;
      if (!this.deps.inBackground()) {
        // The owner is looking at a Crewquarters tab: the badge and title are enough.
        this.channel?.postMessage({ type: 'seen', id: request.id });
        continue;
      }
      const handle = this.deps.show(NOTIFICATION_TITLE, {
        body: notificationBody(request),
        tag: request.id,
        lang: 'en',
      });
      if (!handle) continue;
      handle.onclick = (event: Event) => {
        event.preventDefault();
        this.deps.open(request);
        this.open.delete(request.id);
        handle.close();
      };
      this.open.set(request.id, handle);
      this.channel?.postMessage({ type: 'notified', id: request.id });
      notified.push(request.id);
    }
    return notified;
  }

  /** A message from another tab. */
  receive(message: unknown): void {
    const m = message as Partial<ChannelMessage> | null;
    if (m && (m.type === 'notified' || m.type === 'seen') && typeof m.id === 'string') this.handled.add(m.id);
  }

  closeAll(): void {
    for (const handle of this.open.values()) handle.close();
    this.open.clear();
  }

  get openIds(): string[] {
    return [...this.open.keys()];
  }
}

export function inBackground(): boolean {
  if (typeof document === 'undefined') return false;
  if (document.visibilityState === 'hidden') return true;
  try {
    return !document.hasFocus();
  } catch {
    return false;
  }
}

export function showBrowserNotification(title: string, options: NotificationOptions): NotificationHandle | null {
  if (notificationPermission() !== 'granted') return null;
  try {
    return new window.Notification(title, options);
  } catch {
    // Chrome on Android only allows notifications from a service worker.
    constructorRefused = true;
    writePref(REFUSED_KEY, true);
    emit();
    return null;
  }
}
