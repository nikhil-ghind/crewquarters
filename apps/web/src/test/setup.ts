import { Blob as NodeBlob, File as NodeFile } from 'node:buffer';
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

// Under jsdom, fetch/Request/Response are Node's (undici) but Blob/File/FormData are jsdom's,
// and the two do not interoperate: an upload built from jsdom's FormData loses its file name
// (vitest's compat Request re-wraps each file as a nameless Blob), and undici's multipart
// parser builds parts with the global File (jsdom's) and then asserts they are its own, so
// `await request.formData()` in an MSW handler throws. A browser has one implementation of
// each; give the tests Node's, which match fetch.
if (isBrowserLike) {
  const probe = new Response('', { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } });
  const NodeFormData = (await probe.formData()).constructor as typeof FormData;
  globalThis.Blob = NodeBlob as unknown as typeof Blob;
  globalThis.File = NodeFile as unknown as typeof File;
  globalThis.FormData = NodeFormData;
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
