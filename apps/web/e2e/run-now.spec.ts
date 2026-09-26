/** Run now outside the installed-agent page: marketplace detail and the install wizard's finish step. */
import type { Page } from '@playwright/test';
import { InstallWizardPage, RunDetailPage } from './pages';
import { expect, expectNoSeriousA11yViolations, listRuns, login, test } from './support/fixtures';

async function settle(page: Page): Promise<void> {
  await expect(page.locator('h1').first()).toBeVisible();
  await expect(page.locator('.skeleton')).toHaveCount(0, { timeout: 15_000 });
}

async function noHorizontalOverflow(page: Page, label: string): Promise<void> {
  const { scroll, inner } = await page.evaluate(() => ({ scroll: document.scrollingElement?.scrollWidth ?? 0, inner: window.innerWidth }));
  expect(scroll, `${label}: page must not scroll horizontally`).toBeLessThanOrEqual(inner + 1);
}

test('an installed agent starts from its marketplace page', async ({ page, mock }) => {
  await mock.reset('populated');
  await login(page);
  const before = (await listRuns(page)).length;
  await page.goto('/agents/marketplace/daily-gmail-digest');
  await settle(page);
  const header = page.locator('.page-header');
  const run = header.getByRole('button', { name: 'Run now Daily Gmail Digest' });
  await expect(run).toBeEnabled();
  await expect(header.getByRole('link', { name: 'Open in your crew' })).toBeVisible();
  await expectNoSeriousA11yViolations(page, 'marketplace detail (installed)');

  // Phone width: both actions fit without horizontal scrolling.
  await page.setViewportSize({ width: 390, height: 844 });
  await noHorizontalOverflow(page, 'marketplace detail@390');
  await expect(run).toBeVisible();

  await run.click();
  await page.waitForURL(/\/runs\//);
  await expect(page.getByRole('heading', { level: 1, name: 'Daily Gmail Digest' })).toBeVisible();
  await new RunDetailPage(page).expectState('Completed', 45_000);
  expect((await listRuns(page)).length).toBe(before + 1);
});

test('the install wizard finish step offers Run now and starts the new agent', async ({ page, mock }) => {
  await mock.reset('ready');
  await login(page);
  const wizard = new InstallWizardPage(page);
  await wizard.open('Daily Gmail Digest');
  await wizard.continue(); // compatibility
  await wizard.approveAll();
  await wizard.continue(); // permissions
  await wizard.continue(); // configuration (defaults)
  await wizard.continue(); // requirements
  await wizard.continue(); // schedule (none)
  await wizard.install('Daily Gmail Digest');

  const run = page.getByRole('button', { name: 'Run now Daily Gmail Digest' });
  await expect(run).toBeEnabled();
  await expect(run).toHaveClass(/btn-primary/);
  await expect(page.getByRole('link', { name: 'Open Daily Gmail Digest', exact: true })).toBeVisible();
  await expectNoSeriousA11yViolations(page, 'install wizard finish step');

  await page.setViewportSize({ width: 390, height: 844 });
  await noHorizontalOverflow(page, 'install finish@390');

  await run.click();
  await page.waitForURL(/\/runs\//);
  await expect(page.getByRole('heading', { level: 1, name: 'Daily Gmail Digest' })).toBeVisible();
  const runs = await listRuns(page);
  expect(runs.filter((r) => r.agentId === 'daily-gmail-digest')).toHaveLength(1);
});

test('Home and Schedules offer Run now per scheduled agent and fit a phone', async ({ page, mock }) => {
  await mock.reset('populated');
  await login(page);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');
    await settle(page);
    const nextUp = page.locator('.card').filter({ has: page.getByRole('heading', { name: 'Next up' }) });
    await expect(nextUp.getByRole('button', { name: /^Run now / }).first()).toBeVisible();
    await noHorizontalOverflow(page, `home@${width}`);
    await page.goto('/schedules');
    await settle(page);
    await expect(page.getByRole('button', { name: /^Run now / }).first()).toBeVisible();
    await noHorizontalOverflow(page, `schedules@${width}`);
  }
  await expectNoSeriousA11yViolations(page, 'schedules@390');
  await page.getByRole('button', { name: /^Run now / }).first().click();
  await page.waitForURL(/\/runs\//);
});
