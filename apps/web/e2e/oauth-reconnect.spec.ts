import { ConnectionsPage } from './pages';
import { expect, login, test } from './support/fixtures';

test.beforeEach(async ({ mock, page }) => {
  await mock.reset('populated');
  await login(page);
});

test('expired Google access links from banner and Overview to one reconnect flow', async ({ page, mock }) => {
  await mock.googleExpired();
  await page.reload();
  await expect(page.getByText('Google access expired').first()).toBeVisible();
  const attention = page.locator('article.card-attention').filter({ hasText: 'Google access expired' });
  await expect(attention).toBeVisible();
  await attention.getByRole('link', { name: 'Reconnect Google' }).click();

  await expect(page).toHaveURL(/\/connections\/google$/);
  await expect(page.getByText('Needs attention').first()).toBeVisible();
  await page.getByRole('button', { name: 'Reconnect Google' }).click();
  await page.waitForURL(/\/connections\/google\?result=connected/);
  await expect(page.getByText('Google connected')).toBeVisible();
  await expect(page.locator('main').getByText('Connected', { exact: true }).first()).toBeVisible();

  await page.goto('/connections');
  await expect(new ConnectionsPage(page).card('Google').getByText('Connected', { exact: true })).toBeVisible();
  await expect(page.getByText('Google access expired')).toHaveCount(0);
});

test('a denied Google consent explains what happened with a diagnostic code', async ({ page, mock }) => {
  await mock.googleExpired();
  await mock.googleDeny(true);
  await page.goto('/connections/google');
  await page.getByRole('button', { name: 'Reconnect Google' }).click();
  await page.waitForURL(/result=error/);
  await expect(page.getByText('Google sign-in did not finish')).toBeVisible();
  await expect(page.getByText(/Diagnostic code: OAUTH_DENIED/)).toBeVisible();
});
