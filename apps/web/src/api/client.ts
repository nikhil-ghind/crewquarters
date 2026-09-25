/**
 * The one HTTP data-access layer (PLAN.md section 13.19): openapi-fetch over the
 * generated `paths` type, same origin only.
 *
 * Middleware responsibilities:
 * - CSRF: every state-changing request carries `X-CSRF-Token` (session-bound token
 *   from SessionOut / the `cq_csrf` cookie). The session cookie itself is HttpOnly and
 *   sent by the browser automatically (`credentials: 'same-origin'`).
 * - Idempotency: every POST/PATCH/PUT/DELETE carries an `Idempotency-Key`. Callers that
 *   may resubmit the same intent (double click, "try again" after an unknown outcome)
 *   pass their own stable key via `idempotencyKey()`/`withKey()`.
 * - Session expiry: a 401 while signed in flips the session store to "expired"; the
 *   router then redirects to /login and returns afterwards.
 * - Connectivity: network failures mark the API offline for the global banner.
 * - Timeouts: requests abort after 30 s. A timed-out mutation is reported as
 *   OUTCOME_UNKNOWN and is never retried automatically.
 */
import createClient, { type Middleware } from 'openapi-fetch';
import { connectivity } from './connectivity';
import { ApiError, fromEnvelope, toApiError } from './errors';
import type { paths } from './schema';
import { CSRF_HEADER, session } from './session';

export const API_PREFIX = '/api/v1';
export const REQUEST_TIMEOUT_MS = 30_000;
const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
const AUTH_ENTRY = [`${API_PREFIX}/sessions`, `${API_PREFIX}/bootstrap`];

/** RFC 4122 v4 UUID from getRandomValues (randomUUID needs a secure context; LAN HTTP is not). */
export function idempotencyKey(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** Header object for a caller-chosen idempotency key. */
export function withKey(key: string): { 'Idempotency-Key': string } {
  return { 'Idempotency-Key': key };
}

function origin(): string {
  return typeof window === 'undefined' ? 'http://localhost' : window.location.origin;
}

export function securityHeaders(method: string, headers: Headers): void {
  if (!UNSAFE.has(method.toUpperCase())) return;
  const csrf = session.csrfToken();
  if (csrf && !headers.has(CSRF_HEADER)) headers.set(CSRF_HEADER, csrf);
  if (!headers.has('Idempotency-Key')) headers.set('Idempotency-Key', idempotencyKey());
}

/** Shared response bookkeeping for the typed client and the raw helpers in pending.ts. */
export function observeResponse(response: Response): void {
  const path = new URL(response.url, origin()).pathname;
  const isJson = (response.headers.get('content-type') ?? '').includes('json');
  if ([502, 503, 504].includes(response.status) && !isJson) {
    // The reverse proxy answered but the control API did not.
    connectivity.failed();
    return;
  }
  connectivity.ok();
  if (response.status === 401 && !AUTH_ENTRY.includes(path)) {
    if (session.get().status === 'authenticated') session.expired();
  }
}

const middleware: Middleware = {
  onRequest({ request }) {
    securityHeaders(request.method, request.headers);
    return request;
  },
  onResponse({ response }) {
    observeResponse(response);
    return response;
  },
  onError({ error }) {
    const apiError = toApiError(error);
    if (apiError.isNetwork) connectivity.failed();
    return apiError;
  },
};

export function timedFetch(input: Request): Promise<Response> {
  const signal = AbortSignal.any([input.signal, AbortSignal.timeout(REQUEST_TIMEOUT_MS)]);
  return globalThis.fetch(new Request(input, { signal }));
}

export const api = createClient<paths>({
  baseUrl: origin(),
  credentials: 'same-origin',
  fetch: timedFetch,
});
api.use(middleware);

interface FetchResult<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

/**
 * Await an openapi-fetch call and return its data, or throw an ApiError built from the
 * control API's error envelope. A timed-out mutation becomes OUTCOME_UNKNOWN.
 */
export async function unwrap<T>(call: Promise<FetchResult<T>>, method = 'GET'): Promise<T> {
  let result: FetchResult<T>;
  try {
    result = await call;
  } catch (error) {
    const apiError = toApiError(error);
    if (apiError.code === 'OUTCOME_UNKNOWN' && !UNSAFE.has(method)) {
      throw new ApiError(0, 'NETWORK_ERROR', 'The device took too long to respond.');
    }
    throw apiError;
  }
  if (result.response.ok) return result.data as T;
  throw fromEnvelope(result.response.status, result.error);
}

/** Like unwrap, for mutations: a timeout is reported as OUTCOME_UNKNOWN. */
export function mutate<T>(call: Promise<FetchResult<T>>): Promise<T> {
  return unwrap(call, 'POST');
}
