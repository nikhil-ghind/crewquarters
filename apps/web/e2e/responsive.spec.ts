import type { Page } from '@playwright/test';
import { api, expect, listRuns, login, test } from './support/fixtures';

const WIDTHS = [1440, 1024, 768, 390] as const;

async function noHorizontalOverflow(page: Page, label: string): Promise<void> {
  const { scroll, inner } = await page.evaluate(() => ({
    scroll: document.scrollingElement?.scrollWidth ?? 0,
    inner: window.innerWidth,
  }));
  expect(scroll, `${label}: page must not scroll horizontally`).toBeLessThanOrEqual(inner + 1);
}

async function settle(page: Page): Promise<void> {
  await expect(page.locator('h1').first()).toBeVisible();
  await expect(page.locator('.skeleton')).toHaveCount(0, { timeout: 15_000 });
}

test('layout is usable at 1440, 1024, 768 and 390 CSS pixels', async ({ page, mock }) => {
  test.setTimeout(180_000);
  await mock.reset('populated');
  await login(page);
  const waiting = (await listRuns(page)).find((r) => r.state === 'WAITING_INPUT');
  const kbs = await api<{ items: { id: string }[] }>(page, 'GET', '/api/v1/knowledge-bases');
  const session = await api<{ id: string }>(page, 'POST', '/api/v1/chat/sessions', {
    modelProfile: 'local.general.small',
    knowledgeBaseId: kbs.items[0]?.id,
    retrievalMode: 'when_relevant',
    title: 'Handbook',
  });
  await api(page, 'POST', `/api/v1/chat/sessions/${session.id}/enable`);

  const pages: [string, string][] = [
    ['overview', '/'],
    ['run-approval', `/runs/${waiting?.id ?? ''}`],
    ['chat', `/chat/${session.id}`],
    ['models', '/models'],
  ];

  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 900 });
    for (const [name, path] of pages) {
      await page.goto(path);
      await settle(page);
      await noHorizontalOverflow(page, `${name}@${width}`);

      const sidebar = page.locator('.app-shell > .sidebar');
      if (width >= 1280) {
        await expect(sidebar).toHaveAttribute('data-collapsed', 'false');
        await expect(sidebar.getByText('Home', { exact: true })).toBeVisible();
      } else if (width >= 768) {
        await expect(sidebar).toHaveAttribute('data-collapsed', 'true');
        await expect(sidebar.getByRole('link', { name: 'Home' })).toBeVisible();
      } else {
        await expect(sidebar).toHaveCount(0);
        await expect(page.getByRole('button', { name: 'Open navigation' })).toBeVisible();
      }
      await page.screenshot({ path: `e2e/screenshots/${width}-${name}.png`, fullPage: true });
    }
  }

  // Below 768 px: navigation drawer and tables become cards.
  await page.setViewportSize({ width: 390, height: 900 });
  await page.goto('/activity/runs');
  await settle(page);
  await expect(page.getByRole('table')).toHaveCount(0);
  await expect(page.getByRole('list', { name: 'Runs, newest first' })).toBeVisible();
  await page.getByRole('button', { name: 'Open navigation' }).click();
  const drawer = page.getByRole('dialog', { name: 'Navigation' });
  await expect(drawer.getByRole('link', { name: 'Models' })).toBeVisible();
  await drawer.getByRole('link', { name: 'Models' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Models' })).toBeVisible();
  await expect(drawer).toBeHidden();
});
