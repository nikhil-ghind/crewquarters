import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { keys } from '../api/queries';
import type { InputRequestOut } from '../api/schema';
import ApprovalsPage from '../features/activity/ApprovalsPage';
import { NotificationSettingsCard } from '../features/common/NotificationControls';
import { useInputRequestAlerts } from '../shell/useInputRequestAlerts';
import * as f from '../test/fixtures';
import { renderWithProviders } from '../test/render';
import { server } from '../test/server';
import { setBaseTitle, setTitleCount } from './documentTitle';
import {
  InputRequestNotifier,
  notificationBody,
  notificationPermission,
  plainText,
  refreshNotificationSettings,
  showBrowserNotification,
  type ChannelMessage,
} from './notifications';

/** A stand-in for window.Notification that records what the app does with it. */
class FakeNotification {
  static permission: NotificationPermission = 'default';
  /** What the next permission prompt answers. */
  static answer: NotificationPermission = 'granted';
  static prompts = 0;
  static instances: FakeNotification[] = [];
  static async requestPermission(): Promise<NotificationPermission> {
    FakeNotification.prompts += 1;
    FakeNotification.permission = FakeNotification.answer;
    return FakeNotification.permission;
  }
  readonly title: string;
  readonly options: NotificationOptions;
  onclick: ((ev: Event) => unknown) | null = null;
  closed = false;
  constructor(title: string, options: NotificationOptions = {}) {
    this.title = title;
    this.options = options;
    FakeNotification.instances.push(this);
  }
  close(): void {
    this.closed = true;
  }
  click(): void {
    this.onclick?.(new Event('click', { cancelable: true }));
  }
}

function install(permission: NotificationPermission, { secure = true }: { secure?: boolean } = {}) {
  FakeNotification.permission = permission;
  FakeNotification.answer = 'granted';
  FakeNotification.prompts = 0;
  FakeNotification.instances = [];
  Object.defineProperty(window, 'Notification', { value: FakeNotification, configurable: true, writable: true });
  Object.defineProperty(window, 'isSecureContext', { value: secure, configurable: true });
  refreshNotificationSettings();
}

function uninstall() {
  Reflect.deleteProperty(window, 'Notification');
  Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });
  refreshNotificationSettings();
}

const second: InputRequestOut = {
  ...f.approvalRequest,
  id: 'ir-2',
  runId: 'run-2',
  agentName: 'Daily Gmail Digest',
  title: 'Which label should the digest use?',
  prompt: 'Pick one label.',
};

function req(id: string, extra: Partial<InputRequestOut> = {}): InputRequestOut {
  return { ...f.approvalRequest, id, runId: `run-${id}`, ...extra };
}

function makeNotifier(opts: { active?: boolean; background?: boolean } = {}) {
  const shown: FakeNotification[] = [];
  const opened: string[] = [];
  const posted: ChannelMessage[] = [];
  const state = { active: opts.active ?? true, background: opts.background ?? true };
  const notifier = new InputRequestNotifier({
    active: () => state.active,
    inBackground: () => state.background,
    show: (title, options) => {
      const n = new FakeNotification(title, options);
      shown.push(n);
      return n;
    },
    open: (r) => opened.push(r.id),
    channel: { postMessage: (m) => posted.push(m) },
  });
  return { notifier, shown, opened, posted, state };
}

afterEach(() => {
  uninstall();
  setTitleCount(0);
});

describe('notification permission states', () => {
  it('reports unsupported, insecure, default, granted and denied', () => {
    uninstall();
    expect(notificationPermission()).toBe('unsupported');
    install('granted', { secure: false });
    expect(notificationPermission()).toBe('insecure');
    install('default');
    expect(notificationPermission()).toBe('default');
    install('granted');
    expect(notificationPermission()).toBe('granted');
    install('denied');
    expect(notificationPermission()).toBe('denied');
  });

  it('treats a browser that refuses `new Notification()` as unsupported, and remembers it', () => {
    install('granted');
    class Refusing extends FakeNotification {
      constructor(title: string) {
        super(title);
        throw new TypeError('Illegal constructor. Use ServiceWorkerRegistration.showNotification() instead.');
      }
    }
    Object.defineProperty(window, 'Notification', { value: Refusing, configurable: true, writable: true });
    expect(showBrowserNotification('Needs your input', { tag: 'x' })).toBeNull();
    expect(notificationPermission()).toBe('unsupported');
    refreshNotificationSettings();
    expect(notificationPermission()).toBe('unsupported');
    window.localStorage.removeItem('cq.notify.constructorRefused');
    refreshNotificationSettings();
    expect(notificationPermission()).toBe('granted');
  });
});

describe('notification content', () => {
  it('is plain, single-line text: agent name and short title, truncated', () => {
    expect(notificationBody(f.approvalRequest)).toBe('Caller: Approve 3 automated calls');
    expect(notificationBody({ agentName: null, title: '', prompt: 'Line one\nline\u0007 two' })).toBe('An agent: Line one line two');
    const long = notificationBody({ agentName: 'Caller', title: 'x'.repeat(300), prompt: '' });
    expect(Array.from(long)).toHaveLength(120);
    expect(long.endsWith('…')).toBe(true);
    expect(plainText('  <b>tag</b>  ')).toBe('<b>tag</b>');
  });
});

describe('InputRequestNotifier', () => {
  it('seeds from the first list, so requests that already existed never notify', () => {
    const { notifier, shown } = makeNotifier();
    expect(notifier.update([req('a'), req('b')])).toEqual([]);
    expect(notifier.update([req('a'), req('b')])).toEqual([]);
    expect(shown).toHaveLength(0);
  });

  it('shows one notification per new request id, tagged with the id', () => {
    const { notifier, shown, posted } = makeNotifier();
    notifier.update([req('a')]);
    expect(notifier.update([req('b'), req('c'), req('a')])).toEqual(['b', 'c']);
    // Seen ids never notify again, even across more polls.
    expect(notifier.update([req('b'), req('c'), req('a')])).toEqual([]);
    expect(shown.map((n) => [n.title, n.options.tag])).toEqual([
      ['Needs your input', 'b'],
      ['Needs your input', 'c'],
    ]);
    expect(shown[0]?.options.body).toBe('Caller: Approve 3 automated calls');
    expect(posted).toEqual([
      { type: 'notified', id: 'b' },
      { type: 'notified', id: 'c' },
    ]);
  });

  it('does not notify while the owner is looking at the tab, or while turned off', () => {
    const { notifier, shown, posted, state } = makeNotifier({ background: false });
    notifier.update([]);
    notifier.update([req('a')]);
    expect(shown).toHaveLength(0);
    expect(posted).toEqual([{ type: 'seen', id: 'a' }]);
    state.background = true;
    state.active = false;
    notifier.update([req('a'), req('b')]);
    expect(shown).toHaveLength(0);
    // Turning it on later does not replay requests that arrived while it was off.
    state.active = true;
    notifier.update([req('a'), req('b')]);
    expect(shown).toHaveLength(0);
  });

  it('skips requests another tab already notified', () => {
    const { notifier, shown } = makeNotifier();
    notifier.update([]);
    notifier.receive({ type: 'notified', id: 'a' });
    notifier.receive({ type: 'bogus', id: 'b' });
    notifier.update([req('a'), req('b')]);
    expect(shown.map((n) => n.options.tag)).toEqual(['b']);
  });

  it('closes a notification when its request leaves the pending list', () => {
    const { notifier, shown } = makeNotifier();
    notifier.update([]);
    notifier.update([req('a'), req('b')]);
    expect(notifier.openIds).toEqual(['a', 'b']);
    notifier.update([req('b')]);
    expect(shown[0]?.closed).toBe(true);
    expect(shown[1]?.closed).toBe(false);
    expect(notifier.openIds).toEqual(['b']);
    notifier.closeAll();
    expect(shown[1]?.closed).toBe(true);
  });

  it('opens the request and closes the notification on click', () => {
    const { notifier, shown, opened } = makeNotifier();
    notifier.update([]);
    notifier.update([req('a')]);
    shown[0]?.click();
    expect(opened).toEqual(['a']);
    expect(shown[0]?.closed).toBe(true);
    expect(notifier.openIds).toEqual([]);
  });
});

function AlertsProbe() {
  useInputRequestAlerts();
  return <p>Shell</p>;
}

describe('useInputRequestAlerts (app shell)', () => {
  let pending: InputRequestOut[];
  beforeEach(() => {
    pending = [f.approvalRequest];
    server.use(http.get('/api/v1/input-requests', () => HttpResponse.json({ items: pending, nextCursor: null })));
    setBaseTitle('Overview · Crewquarters');
  });

  it('prefixes the tab title with the pending count and removes it at zero', async () => {
    install('default');
    const { client, unmount } = renderWithProviders(<AlertsProbe />);
    await waitFor(() => expect(document.title).toBe('(1) Overview · Crewquarters'));
    pending = [second, f.approvalRequest];
    await act(() => client.invalidateQueries({ queryKey: keys.inputRequestsAll }));
    await waitFor(() => expect(document.title).toBe('(2) Overview · Crewquarters'));
    pending = [];
    await act(() => client.invalidateQueries({ queryKey: keys.inputRequestsAll }));
    await waitFor(() => expect(document.title).toBe('Overview · Crewquarters'));
    // Not turned on: nothing was shown and the browser was never asked.
    expect(FakeNotification.instances).toHaveLength(0);
    expect(FakeNotification.prompts).toBe(0);
    pending = [f.approvalRequest];
    await act(() => client.invalidateQueries({ queryKey: keys.inputRequestsAll }));
    await waitFor(() => expect(document.title).toBe('(1) Overview · Crewquarters'));
    unmount();
    expect(document.title).toBe('Overview · Crewquarters');
  });

  it('notifies once for a new request in a background tab, opens it on click, closes it when answered elsewhere', async () => {
    install('granted');
    window.localStorage.setItem('cq.notify.inputRequests', 'true');
    refreshNotificationSettings();
    vi.spyOn(document, 'hasFocus').mockReturnValue(false);
    const { client } = renderWithProviders(<AlertsProbe />, {
      extraRoutes: [{ path: '/runs/:runId', element: <p>Run page</p> }],
    });
    await waitFor(() => expect(document.title).toBe('(1) Overview · Crewquarters'));
    // The request that was already pending on load does not notify.
    expect(FakeNotification.instances).toHaveLength(0);

    pending = [second, f.approvalRequest];
    await act(() => client.invalidateQueries({ queryKey: keys.inputRequestsAll }));
    await waitFor(() => expect(FakeNotification.instances).toHaveLength(1));
    const n = FakeNotification.instances[0]!;
    expect(n.title).toBe('Needs your input');
    expect(n.options).toMatchObject({ tag: 'ir-2', body: 'Daily Gmail Digest: Which label should the digest use?' });

    // Another poll with the same list does not notify again.
    await act(() => client.invalidateQueries({ queryKey: keys.inputRequestsAll }));
    expect(FakeNotification.instances).toHaveLength(1);

    // Answered on another device: gone from the pending list, so the notification closes.
    pending = [f.approvalRequest];
    await act(() => client.invalidateQueries({ queryKey: keys.inputRequestsAll }));
    await waitFor(() => expect(n.closed).toBe(true));
    expect(document.title).toBe('(1) Overview · Crewquarters');

    // A third request; clicking its notification goes to its run.
    const third = req('ir-3');
    pending = [third, f.approvalRequest];
    const focus = vi.spyOn(window, 'focus').mockImplementation(() => undefined);
    await act(() => client.invalidateQueries({ queryKey: keys.inputRequestsAll }));
    await waitFor(() => expect(FakeNotification.instances).toHaveLength(2));
    act(() => FakeNotification.instances[1]!.click());
    expect(focus).toHaveBeenCalled();
    expect(await screen.findByText('Run page')).toBeInTheDocument();
    expect(FakeNotification.instances[1]!.closed).toBe(true);
  });
});

describe('Settings: browser notifications card', () => {
  it('asks the browser only on click, then shows On and can be turned off', async () => {
    install('default');
    renderWithProviders(<NotificationSettingsCard />);
    expect(screen.getByText('Off')).toBeInTheDocument();
    expect(FakeNotification.prompts).toBe(0);
    await userEvent.click(screen.getByRole('button', { name: 'Notify me when an agent needs input' }));
    expect(FakeNotification.prompts).toBe(1);
    expect(await screen.findByText('On')).toBeInTheDocument();
    expect(window.localStorage.getItem('cq.notify.inputRequests')).toBe('true');
    await userEvent.click(screen.getByRole('button', { name: 'Turn off notifications' }));
    expect(screen.getByText('Off')).toBeInTheDocument();
    expect(window.localStorage.getItem('cq.notify.inputRequests')).toBe('false');
    // Already granted: turning it back on does not prompt again.
    await userEvent.click(screen.getByRole('button', { name: 'Notify me when an agent needs input' }));
    expect(await screen.findByText('On')).toBeInTheDocument();
    expect(FakeNotification.prompts).toBe(1);
  });

  it('explains a browser block, including after the prompt is refused', async () => {
    install('default');
    FakeNotification.answer = 'denied';
    renderWithProviders(<NotificationSettingsCard />);
    await userEvent.click(screen.getByRole('button', { name: 'Notify me when an agent needs input' }));
    expect(await screen.findByText('Notifications are blocked for this site')).toBeInTheDocument();
    expect(screen.getByText(/set\s+Notifications to Allow/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Notify me/ })).not.toBeInTheDocument();
  });

  it('explains that plain HTTP on the LAN cannot notify, and what still works', () => {
    install('default', { secure: false });
    renderWithProviders(<NotificationSettingsCard />);
    expect(screen.getByText('Notifications need HTTPS or localhost')).toBeInTheDocument();
    expect(screen.getByText(/Activity badge, the count in the tab title and Crew Requests still/)).toBeInTheDocument();
    expect(screen.getByText('Needs HTTPS')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Notify me/ })).not.toBeInTheDocument();
  });

  it('says when the browser has no notifications at all', () => {
    uninstall();
    renderWithProviders(<NotificationSettingsCard />);
    expect(screen.getByText("This browser can't show notifications for Crewquarters")).toBeInTheDocument();
    expect(screen.getByText('Not supported')).toBeInTheDocument();
  });

  it('keeps the preference but shows Off when permission was reset in the browser', () => {
    install('default');
    window.localStorage.setItem('cq.notify.inputRequests', 'true');
    refreshNotificationSettings();
    renderWithProviders(<NotificationSettingsCard />);
    expect(screen.getByText('Off')).toBeInTheDocument();
  });
});

describe('Crew Requests: inline notification prompt', () => {
  it('offers notifications while requests are pending and the browser has not been asked', async () => {
    install('default');
    renderWithProviders(<ApprovalsPage />);
    expect(await screen.findByText('Get notified when an agent needs input')).toBeInTheDocument();
    expect(FakeNotification.prompts).toBe(0);
    await userEvent.click(screen.getByRole('button', { name: 'Notify me' }));
    expect(FakeNotification.prompts).toBe(1);
    await waitFor(() => expect(screen.queryByText('Get notified when an agent needs input')).not.toBeInTheDocument());
  });

  it('can be dismissed, and stays dismissed in this browser', async () => {
    install('default');
    const { unmount } = renderWithProviders(<ApprovalsPage />);
    await userEvent.click(await screen.findByRole('button', { name: 'Not now' }));
    expect(screen.queryByText('Get notified when an agent needs input')).not.toBeInTheDocument();
    expect(FakeNotification.prompts).toBe(0);
    unmount();
    renderWithProviders(<ApprovalsPage />);
    await screen.findByRole('heading', { name: 'Approve 3 automated calls' });
    expect(screen.queryByText('Get notified when an agent needs input')).not.toBeInTheDocument();
  });

  it('is not shown when nothing is pending, when already decided, or on plain HTTP', async () => {
    server.use(http.get('/api/v1/input-requests', () => HttpResponse.json({ items: [], nextCursor: null })));
    install('default');
    const empty = renderWithProviders(<ApprovalsPage />);
    await screen.findByText('Nothing needs you');
    expect(screen.queryByText('Get notified when an agent needs input')).not.toBeInTheDocument();
    empty.unmount();
    server.resetHandlers();
    for (const [permission, secure] of [
      ['granted', true],
      ['denied', true],
      ['default', false],
    ] as const) {
      install(permission, { secure });
      const view = renderWithProviders(<ApprovalsPage />);
      await screen.findByRole('heading', { name: 'Approve 3 automated calls' });
      expect(screen.queryByText('Get notified when an agent needs input')).not.toBeInTheDocument();
      view.unmount();
    }
  });
});
