import { InputRequestCard } from './pages';
import { api, expect, expectNoSeriousA11yViolations, listRuns, login, test } from './support/fixtures';

/** Wait until the page has rendered its main content (no initial skeletons). */
async function settle(page: import('@playwright/test').Page): Promise<void> {
  await expect(page.locator('h1').first()).toBeVisible();
  // The gallery shows skeletons on purpose.
  if (new URL(page.url()).pathname !== '/__gallery') {
    await expect(page.locator('.skeleton')).toHaveCount(0, { timeout: 15_000 });
  }
}

test('anonymous pages have no serious or critical axe violations', async ({ page, mock }) => {
  await mock.reset('fresh');
  await page.goto('/login');
  await settle(page);
  await expectNoSeriousA11yViolations(page, '/login');
  await page.goto('/setup/welcome');
  await settle(page);
  await expectNoSeriousA11yViolations(page, '/setup/welcome');
});

test('every main route has no serious or critical axe violations', async ({ page, mock }) => {
  test.setTimeout(180_000);
  await mock.reset('populated');
  await login(page);

  const installations = await api<{ items: { id: string; agentId: string }[] }>(page, 'GET', '/api/v1/agent-installations');
  const digest = installations.items.find((i) => i.agentId === 'daily-gmail-digest');
  const runs = await listRuns(page);
  const waiting = runs.find((r) => r.state === 'WAITING_INPUT');
  const completed = runs.find((r) => r.state === 'SUCCEEDED');
  const kbs = await api<{ items: { id: string }[] }>(page, 'GET', '/api/v1/knowledge-bases');
  const session = await api<{ id: string }>(page, 'POST', '/api/v1/chat/sessions', {
    modelProfile: 'local.general.small',
    knowledgeBaseId: kbs.items[0]?.id,
    retrievalMode: 'when_relevant',
    title: 'Accessibility check',
  });
  await api(page, 'POST', `/api/v1/chat/sessions/${session.id}/enable`);

  const routes = [
    '/',
    '/agents/installed',
    '/agents/marketplace',
    '/agents/marketplace/daily-gmail-digest',
    '/agents/marketplace/caller/install',
    `/agents/${digest?.id ?? ''}`,
    `/agents/${digest?.id ?? ''}/permissions`,
    '/activity/runs',
    '/activity/approvals',
    `/runs/${waiting?.id ?? ''}`,
    `/runs/${completed?.id ?? ''}`,
    '/models',
    '/models/local.general.small',
    '/knowledge',
    `/knowledge/${kbs.items[0]?.id ?? ''}`,
    '/chat',
    `/chat/${session.id}`,
    '/connections',
    '/connections/twilio',
    '/schedules',
    '/system/status',
    '/system/audit',
    '/system/settings',
    '/system/backups',
    '/__gallery',
  ];
  for (const route of routes) {
    await page.goto(route);
    await settle(page);
    await expectNoSeriousA11yViolations(page, route);
  }
});

test('keyboard: skip link, reaching the approval, and dialog focus handling', async ({ page, mock }) => {
  await mock.reset('populated');
  await login(page);
  const waiting = (await listRuns(page)).find((r) => r.state === 'WAITING_INPUT');
  await page.goto(`/runs/${waiting?.id ?? ''}`);
  await settle(page);

  // The skip link is the first Tab stop and moves focus to main.
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('main#main')).toBeFocused();

  // The approve button is reachable by keyboard.
  const approve = new InputRequestCard(page).button('Approve 3 calls');
  let reached = false;
  for (let i = 0; i < 60 && !reached; i++) {
    await page.keyboard.press('Tab');
    reached = await approve.evaluate((el) => el === document.activeElement);
  }
  expect(reached, 'Approve 3 calls reachable with Tab').toBe(true);

  // A confirmation dialog traps focus, closes on Escape and restores focus.
  const cancel = page.locator('.page-header').getByRole('button', { name: 'Cancel run' });
  await cancel.focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog', { name: 'Cancel this run?' });
  await expect(dialog).toBeVisible();
  for (let i = 0; i < 6; i++) {
    await page.keyboard.press('Tab');
    const inside = await dialog.evaluate((d) => d.contains(document.activeElement));
    expect(inside, 'focus stays in the dialog').toBe(true);
  }
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(cancel).toBeFocused();
});
