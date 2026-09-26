import { InstallWizardPage, RunDetailPage } from './pages';
import { expect, login, test } from './support/fixtures';

test.beforeEach(async ({ mock, page }) => {
  await mock.reset('ready');
  await login(page);
});

test('install Daily Gmail Digest, run it with a cold model start, and read the digest safely', async ({ page, mock }) => {
  const wizard = new InstallWizardPage(page);
  await wizard.open('Daily Gmail Digest');

  // Compatibility
  await expect(page.getByText('Image architecture', { exact: false })).toBeVisible();
  await wizard.continue();
  // Permissions: continue is blocked until each one is approved.
  await expect(page.getByRole('button', { name: 'Continue', exact: true })).toBeDisabled();
  await wizard.approveAll();
  await wizard.continue();
  // Configuration: timezone
  await page.getByLabel('Timezone').selectOption('Europe/London');
  await wizard.continue();
  // Requirements
  await expect(page.getByRole('heading', { name: 'Requirements' })).toBeVisible();
  await wizard.continue();
  // Schedule: daily 10:00 with a preview of the next three runs
  await page.getByLabel('Run this agent on a schedule').check();
  await page.getByLabel('Daily at a selected time').check();
  await page.getByRole('textbox', { name: 'Time', exact: true }).fill('10:00');
  await expect(page.getByText('Next three runs')).toBeVisible();
  await expect(page.getByText('Next three runs').locator('..').getByRole('listitem')).toHaveCount(3);
  await wizard.continue();
  // Review
  await expect(page.getByText('Local on this device')).toBeVisible();
  await wizard.install('Daily Gmail Digest');
  await wizard.openInstalled('Daily Gmail Digest');

  await expect(page.getByRole('heading', { level: 1, name: 'Daily Gmail Digest' })).toBeVisible();
  // Slow the simulation so the cold start is observable.
  await mock.speed(600);
  await page.getByRole('button', { name: /Run now/ }).click();
  await page.waitForURL(/\/runs\//);

  const run = new RunDetailPage(page);
  // Cold start: the run waits for the local model and shows its load stages.
  await expect(page.getByRole('heading', { name: 'Loading local model' })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole('list', { name: 'Load stages' })).toBeVisible();
  await expect(page.getByText(/Loading weights|Allocating cache|Health check/).first()).toBeVisible();
  await run.expectState('Completed', 45_000);

  const result = run.result();
  await expect(result.getByText('Urgent', { exact: true })).toBeVisible();
  await expect(result.getByText('Important', { exact: true })).toBeVisible();
  await expect(result.getByText('Low priority', { exact: true })).toBeVisible();
  await expect(result.getByText('Needs review').first()).toBeVisible();

  const links = result.getByRole('link', { name: /Open in Gmail/ });
  expect(await links.count()).toBeGreaterThan(0);
  for (const href of await links.evaluateAll((els) => els.map((e) => e.getAttribute('href') ?? ''))) {
    expect(new URL(href).hostname).toBe('mail.google.com');
  }

  // The malicious subject is plain text, never markup.
  await expect(result.getByText('Password reset required <img src=x onerror=alert(1)>', { exact: true })).toBeVisible();
  expect(await result.locator('img[onerror], img').count()).toBe(0);
  await expect(run.timelineItems().first()).toBeVisible();
});
