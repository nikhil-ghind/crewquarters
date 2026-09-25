import { ChatPage } from './pages';
import { expect, login, test } from './support/fixtures';

test.beforeEach(async ({ mock, page }) => {
  await mock.reset('ready');
  await login(page, '/chat');
});

test('opt-in RAG chat: cold start, cited answer, source drawer, disable releases the lease', async ({ page, mock }) => {
  const chat = new ChatPage(page);
  await expect(page.getByText('Chat is off until you enable it')).toBeVisible();
  await expect(page.getByText('Local on this device')).toBeVisible();

  await mock.speed(500);
  await chat.enable({ knowledgeBase: 'Team handbook', title: 'Handbook questions' });

  // Cold model: a stage banner and a disabled composer until ready.
  await expect(page.getByRole('heading', { name: 'Getting the model ready' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Send' })).toBeDisabled();
  await expect(page.getByText('Waiting for the model to be ready.')).toBeVisible();
  await expect(page.getByText(/Holding model/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Send' })).toBeEnabled({ timeout: 30_000 });
  await mock.speed(150);

  await chat.ask('How many vacation days do new employees get?');
  const log = page.getByRole('log', { name: 'Conversation' });
  const chip = log.getByRole('button', { name: /^Source 1:/ }).first();
  await expect(chip).toBeVisible({ timeout: 20_000 });
  await chip.click();

  const drawer = page.getByRole('complementary', { name: /Source/ });
  await expect(drawer.getByText('Matched passage')).toBeVisible();
  await expect(drawer.locator('.field-label').first()).not.toBeEmpty();
  await expect(drawer.locator('blockquote.passage')).not.toBeEmpty();
  // Passage text is never interpreted as markup.
  expect(await drawer.locator('script').count()).toBe(0);
  await drawer.getByRole('button', { name: 'Close source' }).click();

  // Every citation chip's passage stays escaped, including the injection fixture.
  const chips = log.getByRole('button', { name: /^Source \d+:/ });
  for (let i = 0; i < (await chips.count()); i++) {
    await chips.nth(i).click();
    await expect(page.getByRole('complementary', { name: /Source/ }).locator('blockquote.passage')).toBeVisible();
    expect(await page.locator('.drawer script').count()).toBe(0);
  }

  // Disable: the lease is released and history is kept.
  await page.getByRole('button', { name: 'Disable chat' }).click();
  await expect(page.getByText(/Model will unload in .* unless another run is using it/)).toBeVisible();
  await expect(page.getByText(/Holding model/)).toHaveCount(0);
  await expect(chat.composer()).toBeDisabled();
  await expect(log.getByText('How many vacation days do new employees get?')).toBeVisible();
});

test('answer only from knowledge: an unsupported question gets an honest no-evidence answer', async ({ page }) => {
  const chat = new ChatPage(page);
  await chat.enable({ knowledgeBase: 'Team handbook', onlyKnowledge: true, title: 'Strict' });
  await expect(page.getByRole('button', { name: 'Send' })).toBeEnabled({ timeout: 30_000 });
  await chat.ask('What will the weather be tomorrow?');
  const log = page.getByRole('log', { name: 'Conversation' });
  await expect(log.getByText(/could not find this in the knowledge base/)).toBeVisible({ timeout: 20_000 });
  await expect(log.getByRole('button', { name: /^Source \d+:/ })).toHaveCount(0);
});
