/** Page objects for the primary flows (PLAN.md section 13.19). */
import { expect, type Locator, type Page } from '@playwright/test';

export class SetupWizardPage {
  readonly page: Page;
  constructor(page: Page) {
    this.page = page;
  }
  heading(name: string | RegExp): Locator {
    return this.page.getByRole('heading', { level: 1, name });
  }
  async continue(): Promise<void> {
    await this.page.getByRole('button', { name: 'Continue', exact: true }).click();
  }
  async expectStep(name: string | RegExp): Promise<void> {
    await expect(this.heading(name)).toBeVisible();
  }
}

export class InstallWizardPage {
  readonly page: Page;
  constructor(page: Page) {
    this.page = page;
  }
  async open(agentName: string): Promise<void> {
    await this.page.goto('/agents/marketplace');
    await this.page.getByRole('link', { name: agentName, exact: true }).click();
    await this.page.getByRole('link', { name: 'Install agent' }).click();
    await expect(this.page.getByRole('heading', { level: 1, name: `Install ${agentName}` })).toBeVisible();
  }
  async continue(): Promise<void> {
    await this.page.getByRole('button', { name: 'Continue', exact: true }).click();
  }
  /** Approve every permission with its own checkbox. */
  async approveAll(): Promise<void> {
    const boxes = this.page.getByRole('checkbox', { name: /^Approve/ });
    const n = await boxes.count();
    for (let i = 0; i < n; i++) await boxes.nth(i).check();
  }
  async install(): Promise<void> {
    await this.page.getByRole('button', { name: 'Install agent' }).click();
    await this.page.waitForURL(/\/agents\/[0-9a-f-]{8,}/);
  }
}

export class RunDetailPage {
  readonly page: Page;
  constructor(page: Page) {
    this.page = page;
  }
  status(label: string): Locator {
    return this.page.locator('.page-header').getByText(label, { exact: true });
  }
  async expectState(label: string, timeout = 30_000): Promise<void> {
    await expect(this.page.locator('.page-header .badge').filter({ hasText: new RegExp(`(^|: )${label}$`) }).first()).toBeVisible({ timeout });
  }
  timelineItems(): Locator {
    return this.page.getByRole('list', { name: 'Run timeline' }).getByRole('listitem');
  }
  result(): Locator {
    return this.page.locator('section.card').filter({ has: this.page.getByRole('heading', { name: 'Result', exact: true }) });
  }
}

export class InputRequestCard {
  readonly root: Locator;
  readonly page: Page;
  constructor(page: Page, title: string | RegExp = /Approve \d+ automated calls?/) {
    this.page = page;
    this.root = page.locator('article.card').filter({ has: page.getByRole('heading', { name: title }) }).first();
  }
  button(name: string | RegExp): Locator {
    return this.root.getByRole('button', { name });
  }
}

export class ModelsPage {
  readonly page: Page;
  constructor(page: Page) {
    this.page = page;
  }
  card(name: string): Locator {
    return this.page.locator('article.card').filter({ has: this.page.getByRole('heading', { name }) });
  }
}

export class KnowledgePage {
  readonly page: Page;
  constructor(page: Page) {
    this.page = page;
  }
  async openBase(name: string): Promise<void> {
    await this.page.goto('/knowledge');
    await this.page.getByRole('link', { name }).click();
    await expect(this.page.getByRole('heading', { level: 1, name })).toBeVisible();
  }
}

export class ChatPage {
  readonly page: Page;
  constructor(page: Page) {
    this.page = page;
  }
  composer(): Locator {
    return this.page.getByRole('textbox', { name: 'Message' });
  }
  async enable(opts: { knowledgeBase?: string; onlyKnowledge?: boolean; title?: string }): Promise<void> {
    await this.page.goto('/chat');
    if (opts.knowledgeBase) await this.page.getByLabel('Knowledge base').selectOption({ label: opts.knowledgeBase });
    if (opts.onlyKnowledge) await this.page.getByLabel('Answer only from knowledge').check();
    if (opts.title) await this.page.getByLabel('Title').fill(opts.title);
    await this.page.getByRole('button', { name: 'Enable local chat' }).click();
    await this.page.waitForURL(/\/chat\/[0-9a-f-]{8,}/);
  }
  async ask(question: string): Promise<void> {
    await this.composer().fill(question);
    await this.page.getByRole('button', { name: 'Send' }).click();
  }
}

export class ConnectionsPage {
  readonly page: Page;
  constructor(page: Page) {
    this.page = page;
  }
  card(provider: string): Locator {
    return this.page.locator('article.card').filter({ has: this.page.getByRole('heading', { name: provider }) });
  }
}
