import { SetupWizardPage } from './pages';
import { expect, SETUP_CODE, test } from './support/fixtures';

test.beforeEach(async ({ mock }) => {
  await mock.reset('fresh');
});

test('first-run setup completes without a shell and resumes after refresh and Google OAuth', async ({ page }) => {
  test.setTimeout(120_000);
  const wizard = new SetupWizardPage(page);

  await page.goto('/');
  await expect(page).toHaveURL(/\/login/);
  await page.getByRole('link', { name: 'Set up Crewquarters' }).click();

  await wizard.expectStep('Welcome to Crewquarters');
  await wizard.continue();

  await wizard.expectStep('System preflight');
  await expect(page.getByRole('list', { name: 'Preflight checks' })).toBeVisible();
  await expect(page.getByText('Passed').first()).toBeVisible();
  await wizard.continue();

  await wizard.expectStep('Owner account');
  await page.getByLabel('Setup code').fill(SETUP_CODE);
  await page.getByLabel('Username').fill('owner');
  await page.getByLabel(/^Password\*?$/).fill('a-long-demo-password');
  await page.getByLabel('Confirm password').fill('a-long-demo-password');
  await page.getByLabel('I have stored this password safely.').check();
  await page.getByRole('button', { name: 'Create owner account' }).click();

  await wizard.expectStep('Storage and network');
  await wizard.continue();

  await wizard.expectStep('Platform services');
  await expect(page.getByRole('list', { name: 'Service health' })).toBeVisible();
  await wizard.continue();

  await wizard.expectStep('Local model');
  await page.getByRole('button', { name: 'Install model' }).click();
  await expect(page.getByRole('progressbar', { name: /Downloading/ }).or(page.getByText(/downloaded/))).toBeVisible();

  // Setup state is on the server: a refresh resumes at the same step, download included.
  await page.reload();
  await wizard.expectStep('Local model');
  await expect(page.getByText('Installed on disk').first()).toBeVisible({ timeout: 60_000 });
  await wizard.continue();

  await wizard.expectStep('Connections');
  await page.getByRole('button', { name: 'Connect Google' }).click();
  // Google consent (simulated by the mock broker) returns to /connections/google, and the
  // app sends the operator back into the wizard.
  await page.waitForURL(/\/setup\/connections\?result=connected/);
  await expect(page.getByText('Google connected')).toBeVisible();
  await wizard.continue();

  await wizard.expectStep('Demo agents');
  const digest = page.locator('section.card').filter({ has: page.getByRole('heading', { name: 'Daily Gmail Digest' }) });
  await digest.getByLabel('Add Daily Gmail Digest to my crew').check();
  const approvals = digest.getByRole('checkbox', { name: /^Approve/ });
  for (let i = 0; i < (await approvals.count()); i++) await approvals.nth(i).check();
  await digest.getByRole('button', { name: 'Install Daily Gmail Digest' }).click();
  await expect(digest.getByText('In your crew.')).toBeVisible();
  await wizard.continue();

  await wizard.expectStep('Validation and finish');
  await expect(page.getByRole('list', { name: 'Validation checks' })).toBeVisible();
  // The diagnostics bundle is built by the device, not in the browser.
  const download = page.waitForEvent('download');
  await page.getByRole('link', { name: 'Download diagnostics' }).click();
  expect((await download).suggestedFilename()).toMatch(/^crewquarters-diagnostics-.*\.zip$/);
  await page.getByRole('button', { name: 'Finish setup' }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Home' })).toBeVisible();

  // The wizard is hidden once setup is complete.
  await page.goto('/setup');
  await expect(page).toHaveURL(/\/$/);
});

test('once the owner exists, sign-in does not offer setup and the wizard offers no second owner', async ({ page, mock }) => {
  await mock.reset('ready');
  // Wait for the bootstrap status answer before asserting that the link is absent.
  const status = page.waitForResponse((r) => r.url().endsWith('/api/v1/bootstrap/status'));
  await page.goto('/login');
  expect(await (await status).json()).toEqual({ ownerExists: true });
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Set up Crewquarters' })).toHaveCount(0);

  await page.goto('/setup/owner');
  await expect(page.getByText('The owner account already exists')).toBeVisible();
  await expect(page.getByLabel('Setup code')).toHaveCount(0);
  await page.getByRole('link', { name: 'Sign in' }).click();
  await expect(page).toHaveURL(/\/login\?next=/);
});
