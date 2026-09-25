import { ModelsPage } from './pages';
import { expect, login, test } from './support/fixtures';

test.beforeEach(async ({ mock, page }) => {
  await mock.reset('ready');
  await login(page, '/models');
});

test('disk and memory states are separate; load survives refresh; unload', async ({ page, mock }) => {
  const models = new ModelsPage(page);
  const card = models.card('General small (8B)');
  await expect(card.getByText('Installed on disk')).toBeVisible();
  await expect(card.getByText('Not loaded')).toBeVisible();
  // Never one ambiguous "Active" label.
  await expect(page.getByText('Active', { exact: true })).toHaveCount(0);

  await mock.speed(700);
  await card.getByRole('button', { name: 'Load now' }).click();
  const dialog = page.getByRole('dialog', { name: /Load General small/ });
  await expect(dialog.getByText('Expected allocation')).toBeVisible();
  await expect(dialog.getByText('System reserve (always kept)')).toBeVisible();
  await dialog.getByRole('button', { name: 'Load model' }).click();

  await expect(card.getByRole('list', { name: 'Load stages' })).toBeVisible();
  await expect(card.getByText(/elapsed/)).toBeVisible();

  // Progress lives on the server: a reload re-attaches to the same load.
  await page.reload();
  const again = new ModelsPage(page).card('General small (8B)');
  await expect(again.getByRole('list', { name: 'Load stages' })).toBeVisible();
  await expect(again.getByText('Ready', { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(again.getByText('Installed on disk')).toBeVisible();

  await mock.speed(150);
  await again.getByRole('button', { name: 'Unload' }).click();
  await expect(again.getByText('Not loaded')).toBeVisible({ timeout: 20_000 });
});

test('a load error shows the error and a Retry action', async ({ page, mock }) => {
  await mock.modelError('local.general.small');
  await page.reload();
  const card = new ModelsPage(page).card('General small (8B)');
  await expect(card.getByText('Error', { exact: true }).first()).toBeVisible();
  await expect(card.getByRole('button', { name: 'Retry' })).toBeVisible();
  await expect(card.getByText(/Diagnostic code: LOAD_FAILED/)).toBeVisible();
});
