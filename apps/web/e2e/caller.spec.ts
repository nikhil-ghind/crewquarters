import { InputRequestCard, InstallWizardPage, RunDetailPage } from './pages';
import { expect, login, test } from './support/fixtures';

test.beforeEach(async ({ mock, page }) => {
  await mock.reset('ready');
  await login(page);
});

async function installCaller(page: import('@playwright/test').Page): Promise<void> {
  const wizard = new InstallWizardPage(page);
  await wizard.open('Caller');
  await wizard.continue();
  await wizard.approveAll();
  // Phone calls get the stronger treatment.
  await expect(page.locator('.permission-row[data-emphasis="phone"]')).toBeVisible();
  await wizard.continue();
  // spreadsheetId is required: an empty submit shows the error next to the field and in a summary.
  await wizard.continue();
  await expect(page.getByRole('alert').filter({ hasText: 'Fix 1 problem' })).toBeVisible();
  await expect(page.getByText('Spreadsheet id is required.').first()).toBeVisible();
  await page.getByLabel('Spreadsheet id').fill('1AbCdEfGhIjKlMnOp');
  await wizard.continue();
  await wizard.continue(); // requirements
  await wizard.continue(); // schedule (manual-only agent)
  await wizard.install();
}

async function startRun(page: import('@playwright/test').Page): Promise<string> {
  await page.getByRole('button', { name: /Run now/ }).click();
  await page.waitForURL(/\/runs\//);
  return page.url();
}

test('caller approval appears in three places, cancel places no calls, approve completes', async ({ page }) => {
  await installCaller(page);
  const agentUrl = page.url();
  const runUrl = await startRun(page);

  // Run detail: the approval card.
  const card = new InputRequestCard(page);
  await expect(card.root).toBeVisible({ timeout: 20_000 });
  await expect(card.root.getByText(/••••\d\d/).first()).toBeVisible();
  await expect(card.root.getByText(/Disclosure:/)).toBeVisible();
  await expect(card.root.getByText(/Script:/)).toBeVisible();
  await expect(card.root.getByText('If you approve')).toBeVisible();
  await expect(card.button('Approve 3 calls')).toBeVisible();

  // The same request on Activity › Crew Requests and on Home.
  await page.goto('/activity/approvals');
  await expect(new InputRequestCard(page).button('Approve 3 calls')).toBeVisible();
  await page.goto('/');
  await expect(new InputRequestCard(page).button('Approve 3 calls')).toBeVisible();

  // Cancel from run detail: no calls are placed.
  await page.goto(runUrl);
  await new InputRequestCard(page).button('Cancel run').click();
  await expect(page.getByText(/Answer submitted/).first()).toBeVisible();
  const run = new RunDetailPage(page);
  await run.expectState('Completed');
  await expect(page.getByText('You cancelled this run before any calls')).toBeVisible();
  await expect(page.getByRole('group', { name: 'Call summary' }).getByText('0').first()).toBeVisible();

  // Second run, approved from the Approvals page.
  await page.goto(agentUrl);
  const secondRun = await startRun(page);
  await expect(new InputRequestCard(page).root).toBeVisible({ timeout: 20_000 });
  await page.goto('/activity/approvals');
  await new InputRequestCard(page).button('Approve 3 calls').click();
  await expect(page.getByText(/Answer submitted/).first()).toBeVisible();

  await page.goto(secondRun);
  await run.expectState('Completed', 45_000);
  const summary = page.getByRole('group', { name: 'Call summary' });
  for (const label of ['Called', 'Answered', 'Responses captured', 'Skipped', 'Failed']) {
    await expect(summary.getByText(label, { exact: true })).toBeVisible();
  }
  const table = page.getByRole('table', { name: 'Calls and responses' });
  await expect(table.getByText('No answer').first()).toBeVisible();
  // No-answer is a call outcome, not an agent failure.
  const failed = await summary.locator('.stat').filter({ hasText: 'Failed' }).locator('.stat-value').innerText();
  expect(Number(failed)).toBe(0);
  await expect(page.getByText('Answer submitted').first()).toBeVisible();
});

test('a Crew Request cannot be answered twice from two tabs', async ({ page, context }) => {
  await installCaller(page);
  const runUrl = await startRun(page);
  await expect(new InputRequestCard(page).root).toBeVisible({ timeout: 20_000 });

  const other = await context.newPage();
  await other.goto('/activity/approvals');
  const otherCard = new InputRequestCard(other);
  await expect(otherCard.button('Approve 3 calls')).toBeVisible();

  await page.goto(runUrl);
  await new InputRequestCard(page).button('Approve 3 calls').click();
  await expect(page.getByText(/Answer submitted/).first()).toBeVisible();

  // The stale tab still shows the button; answering again is rejected by the server.
  await otherCard.button('Cancel run').click();
  await expect(other.getByText(/already answered|answered somewhere else/)).toBeVisible();
  await new RunDetailPage(page).expectState('Completed', 45_000);
  await expect(page.getByText('You cancelled this run before any calls')).toHaveCount(0);
});
