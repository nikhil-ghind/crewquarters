import { RunDetailPage } from './pages';
import { expect, installViaApi, listRuns, login, test } from './support/fixtures';

test.beforeEach(async ({ mock, page }) => {
  await mock.reset('ready');
  await login(page);
});

async function timelineTexts(run: RunDetailPage): Promise<string[]> {
  return run.timelineItems().allInnerTexts();
}

function expectNoDuplicates(texts: string[]): void {
  const dupes = texts.filter((t, i) => texts.indexOf(t) !== i);
  expect(dupes, 'duplicate timeline entries').toEqual([]);
}

test('SSE reconnect resumes from the last sequence without duplicates, then falls back to polling', async ({ page, mock }) => {
  const id = await installViaApi(page, 'hello-crew', { fakeScenario: 'slow', fakeStepSeconds: 0.2 });
  await page.goto(`/agents/${id}`);
  await page.getByRole('button', { name: /Run now/ }).click();
  await page.waitForURL(/\/runs\//);
  const runId = new URL(page.url()).pathname.split('/').pop() ?? '';
  const run = new RunDetailPage(page);
  const runsBefore = (await listRuns(page)).length;

  await expect(page.getByText('Live', { exact: true })).toBeVisible();
  await expect.poll(async () => (await timelineTexts(run)).filter((t) => t.includes('Still working')).length).toBeGreaterThan(2);

  // 1. Drop the stream: the browser reconnects with Last-Event-ID.
  const before = (await mock.state()).runs.find((r) => r.id === runId)?.events ?? 0;
  await mock.sseDrop();
  await expect(page.getByText('Live', { exact: true })).toBeVisible();
  await expect
    .poll(async () => (await mock.sseLog()).filter((e) => e.runId === runId).length, { message: 'a reconnect was logged' })
    .toBeGreaterThan(1);
  const log = (await mock.sseLog()).filter((e) => e.runId === runId);
  const reconnect = log[log.length - 1];
  const cursor = Number(reconnect?.lastEventId ?? reconnect?.after ?? 0);
  expect(cursor).toBeGreaterThan(0);
  expect(cursor).toBeGreaterThanOrEqual(before - 3);
  expect(cursor).toBeLessThanOrEqual(before + 3);

  const countAfterReconnect = (await timelineTexts(run)).length;
  await expect.poll(async () => (await timelineTexts(run)).length).toBeGreaterThan(countAfterReconnect);
  expectNoDuplicates(await timelineTexts(run));

  // 2. SSE unavailable: bounded polling of the history endpoint keeps the timeline moving.
  await mock.sseFail(5);
  await mock.sseDrop();
  await expect(page.getByText('Live updates paused—checking every few seconds')).toBeVisible();
  await expect(page.getByText('Live updates paused—reconnecting').first()).toBeVisible();
  const countPolling = (await timelineTexts(run)).length;
  await expect.poll(async () => (await timelineTexts(run)).length, { timeout: 20_000 }).toBeGreaterThan(countPolling);
  expectNoDuplicates(await timelineTexts(run));

  // Reconnecting never starts the run again.
  expect((await listRuns(page)).length).toBe(runsBefore);
  expect((await mock.state()).runs.filter((r) => r.agentId === 'hello-crew')).toHaveLength(1);
});
