import { api, expect, login, test } from './support/fixtures';

test.beforeEach(async ({ mock }) => {
  await mock.reset('populated');
});

const DOWNLOAD = /^Download crewquarters-backup-/;

test('create a backup from the UI, watch it finish, and download it', async ({ page }) => {
  await login(page, '/system/backups');
  await expect(page.getByRole('heading', { level: 1, name: 'Backups' })).toBeVisible();
  await expect(page.getByText('pg_dump exited with status 1 (disk full).')).toBeVisible();
  await expect(page.getByText('Contains the master key; copy it on the device')).toBeVisible();
  // One downloadable archive to start with (the other holds the master key; one failed).
  await expect(page.getByRole('link', { name: DOWNLOAD })).toHaveCount(1);

  await page.getByRole('button', { name: 'Create backup' }).click();
  await expect(page.getByText('Made from this page').first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Create backup' })).toBeDisabled();
  await expect(page.getByText('A backup is already in progress.')).toBeVisible();

  // Queued, running, then succeeded: picked up by polling.
  await expect(page.getByRole('link', { name: DOWNLOAD })).toHaveCount(2, { timeout: 20_000 });
  await expect(page.getByRole('button', { name: 'Create backup' })).toBeEnabled();

  const download = page.waitForEvent('download');
  await page.getByRole('link', { name: DOWNLOAD }).first().click();
  expect((await download).suggestedFilename()).toMatch(/^crewquarters-backup-\d{8}T\d{6}Z-[0-9a-f]{6}\.tar\.gz$/);

  await page.getByText('Restore', { exact: true }).click();
  await expect(page.getByLabel('Restore commands')).toContainText('sudo crewquarters backup restore /var/lib/crewquarters/backups/');
  await expect(page.getByLabel('Restore commands')).toContainText('sudo crewquarters up');
});

test('a second backup while one runs is refused with BACKUP_IN_PROGRESS', async ({ page }) => {
  await login(page, '/system/backups');
  await api(page, 'POST', '/api/v1/system/backups');
  await expect(api(page, 'POST', '/api/v1/system/backups')).rejects.toThrow(/409.*BACKUP_IN_PROGRESS/);
});

test('backups that are not configured cannot be created', async ({ page, mock }) => {
  await mock.backupsDisabled(true);
  await login(page, '/system/backups');
  await expect(page.getByText('Backups are not configured on this device', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Create backup' })).toBeDisabled();
});

test('System status downloads the diagnostics bundle built by the device', async ({ page }) => {
  await login(page, '/system/status');
  const download = page.waitForEvent('download');
  await page.getByRole('link', { name: 'Download diagnostics' }).click();
  expect((await download).suggestedFilename()).toMatch(/^crewquarters-diagnostics-\d{8}T\d{6}Z\.zip$/);
});
