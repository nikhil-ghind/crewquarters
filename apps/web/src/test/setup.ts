import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterAll, afterEach, beforeAll } from 'vitest';
import { connectivity } from '../api/connectivity';
import { session } from '../api/session';
import { server } from './server';

// jsdom has no <dialog> modality; emulate the parts the app relies on.
if (typeof HTMLDialogElement !== 'undefined' && !HTMLDialogElement.prototype.showModal) {
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement) {
    if (!this.hasAttribute('open')) return;
    this.removeAttribute('open');
    this.dispatchEvent(new Event('close'));
  };
}

const isBrowserLike = typeof window !== 'undefined';

// jsdom has no layout; scrollIntoView/scrollTo are no-ops.
if (isBrowserLike) {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
  window.scrollTo = (() => undefined);
}

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  session.reset();
  connectivity.reset();
  if (!isBrowserLike) return;
  cleanup();
  window.localStorage.clear();
  window.sessionStorage.clear();
  document.cookie.split(';').forEach((c) => {
    const name = c.split('=')[0]?.trim();
    if (name) document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
  });
});
afterAll(() => server.close());
