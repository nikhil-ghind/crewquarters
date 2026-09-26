/**
 * Optional browser notifications for Crew Requests (PLAN.md 13.7), across two
 * "devices" signed in as the owner: a desktop browser with notifications turned on and
 * a phone (iPhone 13 viewport). Headless Chromium may not display notifications, so
 * the pages record every `new Notification()` through an init script. The permission is
 * granted to the origin for real (context.grantPermissions); headless Chromium still
 * reports `Notification.permission === 'denied'`, so the recorder mirrors the
 * Permissions API state, which does reflect the grant.
 */
import { devices, type Browser, type Page } from '@playwright/test';
import { InputRequestCard } from './pages';
import { api, expect, expectNoSeriousA11yViolations, login, test } from './support/fixtures';

interface RecordedNotification {
  title: string;
  tag: string | undefined;
  body: string | undefined;
  closed: boolean;
}

declare global {
  interface Window {
    __notes: { title: string; options: NotificationOptions; closed: boolean; onclick: ((ev: Event) => unknown) | null }[];
    __setBackground: (background: boolean) => void;
  }
}

/**
 * Runs before the app in every document: records notifications instead of showing
 * them, and lets the test put the tab "in the background" (hidden and unfocused).
 */
function instrument(): void {
  const w = window;
  let background = false;
  let permission: NotificationPermission = 'default';
  const fromState = (state: PermissionState): NotificationPermission =>
    state === 'granted' ? 'granted' : state === 'denied' ? 'denied' : 'default';
  void navigator.permissions.query({ name: 'notifications' }).then((status) => {
    permission = fromState(status.state);
    // The app re-reads the permission on focus (as after changing site settings).
    w.dispatchEvent(new Event('focus'));
  });
  w.__notes = [];
  class RecordingNotification {
    title: string;
    options: NotificationOptions;
    closed = false;
    onclick: ((ev: Event) => unknown) | null = null;
    static get permission(): NotificationPermission {
      return permission;
    }
    static requestPermission(): Promise<NotificationPermission> {
      // The prompt is answered by the context's granted permissions.
      return navigator.permissions.query({ name: 'notifications' }).then((status) => {
        permission = fromState(status.state) === 'default' ? 'denied' : fromState(status.state);
        return permission;
      });
    }
    constructor(title: string, options: NotificationOptions = {}) {
      this.title = title;
      this.options = options;
      w.__notes.push(this);
    }
    close(): void {
      this.closed = true;
    }
  }
  Object.defineProperty(w, 'Notification', { value: RecordingNotification, configurable: true, writable: true });
  Object.defineProperty(Document.prototype, 'visibilityState', { get: () => (background ? 'hidden' : 'visible'), configurable: true });
  Object.defineProperty(Document.prototype, 'hidden', { get: () => background, configurable: true });
  Document.prototype.hasFocus = () => !background;
  w.__setBackground = (value: boolean) => {
    background = value;
    document.dispatchEvent(new Event('visibilitychange'));
    w.dispatchEvent(new Event(value ? 'blur' : 'focus'));
  };
}

async function notes(page: Page): Promise<RecordedNotification[]> {
  return page.evaluate(() =>
    window.__notes.map((n) => ({ title: n.title, tag: n.options.tag, body: n.options.body, closed: n.closed })),
  );
}

async function phone(browser: Browser, baseURL: string): Promise<Page> {
  const { defaultBrowserType: _ignored, ...iPhone } = devices['iPhone 13'];
  const context = await browser.newContext({ ...iPhone, baseURL });
  const page = await context.newPage();
  await page.addInitScript(instrument);
  return page;
}

test('a new Crew Request notifies the desktop, shows on both devices, and an answer from the phone closes it', async ({
  page: desktop,
  context,
  browser,
  mock,
  baseURL,
}) => {
  test.setTimeout(120_000);
  const origin = baseURL ?? 'http://127.0.0.1:4173';
  await mock.reset('ready');
  await context.grantPermissions(['notifications'], { origin });
  await desktop.addInitScript(instrument);
  await login(desktop, '/system/settings');

  // Desktop: turn notifications on from Settings (the only place the browser is asked).
  const card = desktop.locator('section.card', { hasText: 'Browser notifications' });
  await expect(card.getByText('Off', { exact: true })).toBeVisible();
  await expectNoSeriousA11yViolations(desktop, '/system/settings (notifications off)');
  await card.getByRole('button', { name: 'Notify me when an agent needs input' }).click();
  await expect(card.getByText('On', { exact: true })).toBeVisible();
  await expect(card.getByRole('button', { name: 'Turn off notifications' })).toBeVisible();
  await expectNoSeriousA11yViolations(desktop, '/system/settings (notifications on)');

  // Desktop waits on Crew Requests, in the background.
  await desktop.goto('/activity/approvals');
  await expect(desktop.getByText('Nothing needs you')).toBeVisible();
  await expect(desktop).toHaveTitle('Crew Requests · Crewquarters');
  await desktop.evaluate(() => window.__setBackground(true));

  // The phone signs in too.
  const mobile = await phone(browser, origin);
  await login(mobile, '/activity/approvals');
  await expect(mobile.getByText('Nothing needs you')).toBeVisible();

  // An agent asks for approval.
  const created = await mock.inputRequest();

  // Desktop (background tab, 5 s polling): exactly one notification for it.
  await expect.poll(() => notes(desktop), { timeout: 15_000 }).toEqual([
    { title: 'Needs your input', tag: created.inputRequestId, body: `${created.agentName ?? 'Caller'}: ${created.title}`, closed: false },
  ]);
  await expect(desktop).toHaveTitle('(1) Crew Requests · Crewquarters');

  // Phone: the same request, the badge and the title count, without notifications.
  await expect(new InputRequestCard(mobile).button('Approve 3 calls')).toBeVisible({ timeout: 15_000 });
  await expect(mobile).toHaveTitle('(1) Crew Requests · Crewquarters');
  await expect(mobile.getByRole('link', { name: '1 Crew Request needs you' })).toBeVisible({ timeout: 15_000 });
  // The phone was never asked, so Crew Requests offers notifications inline.
  const prompt = mobile.locator('.notify-prompt');
  await expect(prompt.getByText('Get notified when an agent needs input')).toBeVisible();
  await expectNoSeriousA11yViolations(mobile, '/activity/approvals (phone, with notification prompt)');

  // Desktop comes back to the tab; it still shows the request (its next poll has not
  // happened yet: hold its list as it was, so the race is deterministic).
  await desktop.evaluate(() => window.__setBackground(false));
  const desktopCard = new InputRequestCard(desktop);
  await expect(desktopCard.button('Cancel run')).toBeVisible();
  // Refetched on focus: the Activity badge counts it too.
  await expect(desktop.getByRole('link', { name: /Activity\s*1 item needs attention/ })).toBeVisible();
  await expectNoSeriousA11yViolations(desktop, '/activity/approvals (desktop, request pending)');
  const stale = await api(desktop, 'GET', '/api/v1/input-requests?state=pending&limit=100');
  const isList = (url: URL) => url.pathname === '/api/v1/input-requests';
  await desktop.route(isList, (route) => route.fulfill({ json: stale }));

  // Answer from the phone.
  await new InputRequestCard(mobile).button('Approve 3 calls').click();
  await expect(mobile.getByText(/Answer submitted/).first()).toBeVisible();

  // A second answer from the desktop is refused (409 INPUT_ALREADY_CLOSED) and says so.
  await desktopCard.button('Cancel run').click();
  await expect(desktop.getByText('This request was already answered, so your answer was not sent again.')).toBeVisible();

  // Once the desktop polls again the request is gone, the count clears, and the
  // notification it showed is closed.
  await desktop.unroute(isList);
  await expect(desktop.getByText('Nothing needs you')).toBeVisible({ timeout: 15_000 });
  await expect(desktop).toHaveTitle('Crew Requests · Crewquarters');
  await expect.poll(async () => (await notes(desktop)).map((n) => n.closed)).toEqual([true]);
  await expect(mobile).toHaveTitle('Crew Requests · Crewquarters', { timeout: 15_000 });

  // Another request while the desktop is in the background; its notification opens the run.
  await desktop.evaluate(() => window.__setBackground(true));
  const next = await mock.inputRequest();
  await expect.poll(async () => (await notes(desktop)).length, { timeout: 15_000 }).toBe(2);
  await desktop.evaluate(() => {
    const n = window.__notes[1];
    n?.onclick?.(new Event('click', { cancelable: true }));
  });
  await desktop.waitForURL(new RegExp(`/runs/${next.runId}$`));
  await expect(new InputRequestCard(desktop).button('Approve 3 calls')).toBeVisible();
  expect((await notes(desktop))[1]?.closed).toBe(true);

  await mobile.context().close();
});

test('a tab that is in front of the owner does not notify, and nothing is asked on load', async ({ page, context, mock, baseURL }) => {
  await mock.reset('ready');
  await context.grantPermissions(['notifications'], { origin: baseURL ?? 'http://127.0.0.1:4173' });
  await page.addInitScript(instrument);
  await login(page, '/system/settings');
  await page.getByRole('button', { name: 'Notify me when an agent needs input' }).click();
  await page.goto('/');
  const created = await mock.inputRequest();
  // Visible and focused: the badge and title update, no notification.
  await expect(page).toHaveTitle(/^\(1\) /, { timeout: 15_000 });
  expect(await notes(page)).toEqual([]);
  // A request that already existed when a tab loads never notifies in that tab.
  await page.reload();
  await page.evaluate(() => window.__setBackground(true));
  await page.waitForTimeout(6_000);
  expect(await notes(page)).toEqual([]);
  expect(created.inputRequestId).toBeTruthy();
});
