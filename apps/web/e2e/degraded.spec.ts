import { expect, login, test } from './support/fixtures';

test.beforeEach(async ({ mock, page }) => {
  await mock.reset('populated');
  await login(page);
});

async function openDigestAgent(page: import('@playwright/test').Page): Promise<void> {
  await page.goto('/agents/installed');
  await page.getByRole('link', { name: 'Daily Gmail Digest', exact: true }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Daily Gmail Digest' })).toBeVisible();
}

test('API outage keeps cached history readable and disables actions', async ({ page, mock }) => {
  await page.goto('/activity/runs');
  const table = page.getByRole('table', { name: 'Runs, newest first' });
  await expect(table.getByRole('link').first()).toBeVisible();
  const rows = await table.getByRole('row').count();

  // Client-side navigation keeps the in-memory cache (a full reload would drop it).
  await page.getByRole('link', { name: 'Crew', exact: true }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Your Crew' })).toBeVisible();
  await page.getByRole('link', { name: 'Daily Gmail Digest', exact: true }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Daily Gmail Digest' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Run now/ })).toBeEnabled();

  await mock.offline(true);
  await page.getByRole('navigation', { name: 'Primary' }).getByRole('link', { name: /^Activity/ }).click();
  await expect(page.getByText('Crewquarters is not responding')).toBeVisible({ timeout: 20_000 });
  // Cached history stays readable.
  await expect(table.getByRole('row')).toHaveCount(rows);

  await page.getByRole('link', { name: 'Crew', exact: true }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Your Crew' })).toBeVisible();
  await page.getByRole('link', { name: 'Daily Gmail Digest', exact: true }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Daily Gmail Digest' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Run now/ })).toBeDisabled();
  await expect(page.getByText('The device is not responding. Actions resume when it reconnects.')).toBeVisible();

  await mock.offline(false);
  await page.getByRole('button', { name: 'Retry now' }).click();
  await expect(page.getByText('Crewquarters is not responding')).toHaveCount(0, { timeout: 20_000 });
});

test('runtime unavailable and low disk show global warnings; runs are disabled with a reason', async ({ page, mock }) => {
  await mock.runtimeDown(true);
  await mock.lowDisk(true);
  await openDigestAgent(page);
  await page.reload();
  await expect(page.getByText('Agent runtime unavailable')).toBeVisible();
  await expect(page.getByText('Storage is running low')).toBeVisible();
  await expect(page.getByRole('button', { name: /Run now/ })).toBeDisabled();
  await expect(page.getByText(/agent runtime is unavailable, so runs and models cannot start/)).toBeVisible();
});

test('an expired session returns to sign-in and back to the same page', async ({ page, context }) => {
  await page.goto('/schedules');
  await expect(page.getByRole('heading', { level: 1, name: 'Schedules' })).toBeVisible();
  await context.clearCookies();
  await page.getByRole('link', { name: 'Models', exact: true }).click();
  await page.waitForURL(/\/login\?.*expired=1/);
  expect(new URL(page.url()).searchParams.get('next')).toBe('/models');
  await expect(page.getByText('Your session expired')).toBeVisible();
  await page.getByLabel('Username').fill('owner');
  await page.getByLabel('Password').fill('correct-horse-battery');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL(/\/models$/);
});

test('a permission-changing update cannot run until reapproved', async ({ page, mock }) => {
  await mock.newVersion('daily-gmail-digest');
  await openDigestAgent(page);
  await expect(page.getByText('Needs reapproval').first()).toBeVisible();
  await expect(page.getByRole('button', { name: /Run now/ })).toBeDisabled();

  await page.getByRole('link', { name: 'Permissions' }).click();
  await expect(page.getByText('New in this version')).toBeVisible();
  await expect(page.locator('.permission-row[data-emphasis="cloud"][data-changed="true"]')).toBeVisible();
  const boxes = page.getByRole('checkbox', { name: /^Approve/ });
  for (let i = 0; i < (await boxes.count()); i++) await boxes.nth(i).check();
  await page.getByRole('button', { name: 'Approve and update' }).click();
  await expect(page.getByText('Needs reapproval')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Run now/ })).toBeEnabled();
});
