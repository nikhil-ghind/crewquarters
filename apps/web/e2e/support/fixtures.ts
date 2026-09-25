import AxeBuilder from '@axe-core/playwright';
import { test as base, expect, type APIRequestContext, type Page } from '@playwright/test';

export type Scenario = 'fresh' | 'ready' | 'populated';

export const OWNER = { username: 'owner', password: 'correct-horse-battery' };
export const SETUP_CODE = 'CQ-DEMO-SETUP';

/** Test controls of mock/server.ts (no auth). */
export class MockControls {
  private readonly request: APIRequestContext;
  constructor(request: APIRequestContext) {
    this.request = request;
  }

  private async post(path: string, data: Record<string, unknown> = {}): Promise<unknown> {
    const res = await this.request.post(`/__mock/${path}`, { data });
    expect(res.ok(), `POST /__mock/${path}`).toBeTruthy();
    return res.json();
  }

  reset(scenario: Scenario) {
    return this.post('reset', { scenario });
  }
  speed(ms: number) {
    return this.post('speed', { ms });
  }
  offline(on: boolean) {
    return this.post('offline', { on });
  }
  runtimeDown(on: boolean) {
    return this.post('runtime-down', { on });
  }
  lowDisk(on: boolean) {
    return this.post('low-disk', { on });
  }
  googleExpired() {
    return this.post('google-expired');
  }
  googleDeny(on: boolean) {
    return this.post('google-deny', { on });
  }
  sseDrop() {
    return this.post('sse', { mode: 'drop' });
  }
  sseFail(count: number) {
    return this.post('sse', { mode: 'fail', count });
  }
  newVersion(agentId: string) {
    return this.post('new-version', { agentId });
  }
  modelError(modelId: string) {
    return this.post('model-error', { modelId });
  }
  async sseLog(): Promise<{ runId: string; lastEventId: string | null; after: string | null; at: string }[]> {
    const res = await this.request.get('/__mock/sse-log');
    return (await res.json()) as { runId: string; lastEventId: string | null; after: string | null; at: string }[];
  }
  async state(): Promise<{ runs: { id: string; agentId: string; state: string; events: number }[] }> {
    const res = await this.request.get('/__mock/state');
    return (await res.json()) as { runs: { id: string; agentId: string; state: string; events: number }[] };
  }
}

export async function login(page: Page, next = '/'): Promise<void> {
  await page.goto(`/login?next=${encodeURIComponent(next)}`);
  await page.getByLabel('Username').fill(OWNER.username);
  await page.getByLabel('Password').fill(OWNER.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.waitForURL((u) => !u.pathname.startsWith('/login'));
}

/** Same-origin API call from the signed-in page (session cookie + CSRF header). */
export async function api<T = unknown>(page: Page, method: string, path: string, body?: unknown): Promise<T> {
  return page.evaluate(
    async ({ method, path, body }) => {
      const csrf = document.cookie
        .split(';')
        .map((c) => c.trim().split('='))
        .find(([k]) => k === 'cq_csrf')?.[1];
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (method !== 'GET') {
        headers['X-CSRF-Token'] = decodeURIComponent(csrf ?? '');
        headers['Idempotency-Key'] = crypto.randomUUID();
      }
      const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: 'same-origin' });
      const text = await res.text();
      if (!res.ok) throw new Error(`${method} ${path} → ${res.status} ${text}`);
      return (text ? JSON.parse(text) : null) as T;
    },
    { method, path, body },
  );
}

interface Catalog {
  items: { agentId: string; latest: { version: string; permissions: Record<string, unknown> } }[];
}

/** Install an agent through the API (for tests that are not about installation). */
export async function installViaApi(page: Page, agentId: string, config: Record<string, unknown>): Promise<string> {
  const catalog = await api<Catalog>(page, 'GET', '/api/v1/catalog/agents');
  const agent = catalog.items.find((a) => a.agentId === agentId);
  if (!agent) throw new Error(`no catalog agent ${agentId}`);
  const inst = await api<{ id: string }>(page, 'POST', '/api/v1/agent-installations', {
    agentId,
    version: agent.latest.version,
    config,
    approvedPermissions: agent.latest.permissions,
    enabled: true,
  });
  return inst.id;
}

export async function listRuns(page: Page): Promise<{ id: string; agentId: string; state: string }[]> {
  return (await api<{ items: { id: string; agentId: string; state: string }[] }>(page, 'GET', '/api/v1/runs?limit=200')).items;
}

/** axe with WCAG 2.x A/AA tags; fails on serious or critical violations. */
export async function expectNoSeriousA11yViolations(page: Page, label: string): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze();
  const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
  const summary = results.violations
    .map((v) => `  [${v.impact ?? '?'}] ${v.id}: ${v.help}\n${v.nodes.slice(0, 3).map((n) => `      ${n.target.join(' ')}`).join('\n')}`)
    .join('\n');
  if (results.violations.length > 0) console.log(`axe ${label}: ${results.violations.length} violation(s)\n${summary}`);
  expect(serious, `serious/critical axe violations on ${label}:\n${summary}`).toEqual([]);
}

export const test = base.extend<{ mock: MockControls; failOnDialog: void }>({
  mock: async ({ request }, provide) => {
    const mock = new MockControls(request);
    await mock.speed(150);
    await provide(mock);
    await mock.offline(false);
    await mock.runtimeDown(false);
    await mock.lowDisk(false);
    await mock.googleDeny(false);
  },
  // Agent/provider content must never execute: any alert/confirm is a failure.
  failOnDialog: [
    async ({ page }, provide) => {
      const dialogs: string[] = [];
      page.on('dialog', (d) => {
        dialogs.push(d.message());
        void d.dismiss();
      });
      await provide();
      expect(dialogs, 'unexpected browser dialogs (script injection?)').toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };
