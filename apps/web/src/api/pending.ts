/**
 * Thin typed calls for routes not yet in this branch's schema.d.ts (see
 * ./pending-contracts.ts). They reuse the CSRF, idempotency, timeout, session and
 * connectivity handling of the generated client.
 *
 * TODO(contracts): once schema.d.ts contains these routes, replace each function body
 * with the equivalent `api.GET/POST/...` call and delete `request()`.
 */
import { observeResponse, securityHeaders, timedFetch } from './client';
import { connectivity } from './connectivity';
import { fromEnvelope, toApiError } from './errors';
import {
  PENDING_PATHS as P,
  type CitationOut,
  type ConnectionOut,
  type DocumentOut,
  type GoogleStartIn,
  type GoogleStartOut,
  type KnowledgeBaseCreateIn,
  type KnowledgeBaseOut,
  type KnowledgeQueryIn,
  type KnowledgeQueryOut,
  type Page,
  type ProviderProfileCreateIn,
  type ProviderProfileOut,
  type ProviderProfileTestOut,
  type TwilioCredentialsIn,
  type TwilioTestCallIn,
  type TwilioTestCallOut,
} from './pending-contracts';

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

interface Options {
  body?: unknown;
  form?: FormData;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

async function request<T>(method: Method, path: string, options: Options = {}): Promise<T> {
  const headers = new Headers({ Accept: 'application/json' });
  if (options.idempotencyKey) headers.set('Idempotency-Key', options.idempotencyKey);
  let body: BodyInit | undefined;
  if (options.form) {
    body = options.form; // the browser sets the multipart boundary
  } else if (options.body !== undefined) {
    headers.set('Content-Type', 'application/json');
    body = JSON.stringify(options.body);
  }
  securityHeaders(method, headers);
  const req = new Request(new URL(path, window.location.origin), {
    method,
    headers,
    body,
    credentials: 'same-origin',
    signal: options.signal,
  });
  let response: Response;
  try {
    // Uploads can legitimately take longer than the default timeout.
    response = options.form ? await globalThis.fetch(req) : await timedFetch(req);
  } catch (error) {
    const apiError = toApiError(error);
    if (apiError.isNetwork) connectivity.failed();
    throw apiError;
  }
  observeResponse(response);
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  let json: unknown;
  try {
    json = text ? JSON.parse(text) : undefined;
  } catch {
    json = undefined;
  }
  if (!response.ok) throw fromEnvelope(response.status, json);
  return json as T;
}

const list = (path: string) => `${path}?limit=200`;

export const pendingApi = {
  // Google (capability broker, proxied by the control API)
  googleStart: (body: GoogleStartIn) => request<GoogleStartOut>('POST', P.googleStart, { body }),
  googleTest: (idempotencyKey?: string) => request<ConnectionOut>('POST', P.googleTest, { idempotencyKey }),
  googleDisconnect: (idempotencyKey?: string) => request<void>('DELETE', P.google, { idempotencyKey }),

  // Twilio
  twilioSave: (body: TwilioCredentialsIn, idempotencyKey?: string) =>
    request<ConnectionOut>('PUT', P.twilio, { body, idempotencyKey }),
  twilioTest: (idempotencyKey?: string) => request<ConnectionOut>('POST', P.twilioTest, { idempotencyKey }),
  twilioTestCall: (body: TwilioTestCallIn, idempotencyKey: string) =>
    request<TwilioTestCallOut>('POST', P.twilioTestCall, { body, idempotencyKey }),
  twilioDelete: (idempotencyKey?: string) => request<void>('DELETE', P.twilio, { idempotencyKey }),

  // OpenAI / Anthropic provider keys
  providerProfiles: () => request<Page<ProviderProfileOut>>('GET', list(P.providerProfiles)),
  providerProfileCreate: (body: ProviderProfileCreateIn, idempotencyKey?: string) =>
    request<ProviderProfileOut>('POST', P.providerProfiles, { body, idempotencyKey }),
  providerProfileDelete: (id: string, idempotencyKey?: string) =>
    request<void>('DELETE', P.providerProfile(id), { idempotencyKey }),
  providerProfileTest: (id: string, idempotencyKey?: string) =>
    request<ProviderProfileTestOut>('POST', P.providerProfileTest(id), { idempotencyKey }),

  // Knowledge
  knowledgeBases: () => request<Page<KnowledgeBaseOut>>('GET', list(P.knowledgeBases)),
  knowledgeBase: (id: string) => request<KnowledgeBaseOut>('GET', P.knowledgeBase(id)),
  knowledgeBaseCreate: (body: KnowledgeBaseCreateIn, idempotencyKey?: string) =>
    request<KnowledgeBaseOut>('POST', P.knowledgeBases, { body, idempotencyKey }),
  knowledgeBaseDelete: (id: string, idempotencyKey?: string) =>
    request<void>('DELETE', P.knowledgeBase(id), { idempotencyKey }),
  documents: (kbId: string) => request<Page<DocumentOut>>('GET', list(P.documents(kbId))),
  documentUpload: (kbId: string, file: File, idempotencyKey: string, signal?: AbortSignal) => {
    const form = new FormData();
    form.append('file', file, file.name);
    return request<DocumentOut>('POST', P.documents(kbId), { form, idempotencyKey, signal });
  },
  documentDelete: (kbId: string, docId: string, idempotencyKey?: string) =>
    request<void>('DELETE', P.document(kbId, docId), { idempotencyKey }),
  documentReindex: (kbId: string, docId: string, idempotencyKey?: string) =>
    request<DocumentOut>('POST', P.documentReindex(kbId, docId), { idempotencyKey }),
  knowledgeQuery: (kbId: string, body: KnowledgeQueryIn) =>
    request<KnowledgeQueryOut>('POST', P.knowledgeQuery(kbId), { body }),

  // Chat citations
  citation: (sessionId: string, messageId: string, citationId: string) =>
    request<CitationOut>('GET', P.citation(sessionId, messageId, citationId)),
};

/** Largest document the knowledge service accepts (CQ_MAX_UPLOAD_BYTES default). */
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
