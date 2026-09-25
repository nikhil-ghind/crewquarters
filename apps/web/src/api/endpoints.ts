/**
 * Typed calls for connections, provider keys, knowledge and chat citations, on the
 * generated openapi-fetch client (CSRF, idempotency, timeouts, session and
 * connectivity handling come from ./client.ts).
 */
import { api, mutate, unwrap, withKey } from './client';
import type {
  GoogleStartIn,
  KnowledgeBaseCreateIn,
  KnowledgeQueryIn,
  ProviderProfileCreateIn,
  TwilioCredentialsIn,
  TwilioTestCallIn,
} from './schema';

/** Largest document the knowledge service accepts (CQ_MAX_UPLOAD_BYTES default). */
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;

/** Where the capability broker returns the browser after Google consent
 * (`?result=connected` or `?result=error&code=...`). */
export const GOOGLE_RETURN_ROUTE = '/connections/google';

const headers = (key?: string) => (key ? withKey(key) : undefined);
const LIST = { limit: 200 };

export const endpoints = {
  // Google (capability broker, proxied by the control API). Start also sets the
  // cq_oauth_binding cookie; the browser then navigates to authorizationUrl.
  googleStart: (body: GoogleStartIn) => mutate(api.POST('/api/v1/connections/google/start', { body })),
  googleTest: (key?: string) => mutate(api.POST('/api/v1/connections/google/test', { headers: headers(key) })),
  googleDisconnect: (key?: string) => mutate(api.DELETE('/api/v1/connections/google', { headers: headers(key) })),

  // Twilio
  twilioSave: (body: TwilioCredentialsIn, key?: string) =>
    mutate(api.PUT('/api/v1/connections/twilio', { body, headers: headers(key) })),
  twilioTest: (key?: string) => mutate(api.POST('/api/v1/connections/twilio/test', { headers: headers(key) })),
  twilioTestCall: (body: TwilioTestCallIn, key: string) =>
    mutate(api.POST('/api/v1/connections/twilio/test-call', { body, headers: withKey(key) })),
  twilioDelete: (key?: string) => mutate(api.DELETE('/api/v1/connections/twilio', { headers: headers(key) })),

  // OpenAI / Anthropic provider keys
  providerProfiles: () => unwrap(api.GET('/api/v1/provider-profiles', { params: { query: LIST } })),
  providerProfileCreate: (body: ProviderProfileCreateIn, key?: string) =>
    mutate(api.POST('/api/v1/provider-profiles', { body, headers: headers(key) })),
  providerProfileDelete: (id: string, key?: string) =>
    mutate(api.DELETE('/api/v1/provider-profiles/{profile_id}', { params: { path: { profile_id: id } }, headers: headers(key) })),
  providerProfileTest: (id: string, key?: string) =>
    mutate(api.POST('/api/v1/provider-profiles/{profile_id}/test', { params: { path: { profile_id: id } }, headers: headers(key) })),

  // Knowledge
  knowledgeBases: () => unwrap(api.GET('/api/v1/knowledge-bases', { params: { query: LIST } })),
  knowledgeBase: (id: string) => unwrap(api.GET('/api/v1/knowledge-bases/{kb_id}', { params: { path: { kb_id: id } } })),
  knowledgeBaseCreate: (body: KnowledgeBaseCreateIn, key?: string) =>
    mutate(api.POST('/api/v1/knowledge-bases', { body, headers: headers(key) })),
  knowledgeBaseDelete: (id: string, key?: string) =>
    mutate(api.DELETE('/api/v1/knowledge-bases/{kb_id}', { params: { path: { kb_id: id } }, headers: headers(key) })),
  documents: (kbId: string) =>
    unwrap(api.GET('/api/v1/knowledge-bases/{kb_id}/documents', { params: { path: { kb_id: kbId }, query: LIST } })),
  documentUpload: (kbId: string, file: File, key: string, signal?: AbortSignal) => {
    const form = new FormData();
    form.append('file', file, file.name);
    return mutate(
      api.POST('/api/v1/knowledge-bases/{kb_id}/documents', {
        params: { path: { kb_id: kbId } },
        // The generated multipart body type describes the field; the browser encodes
        // the FormData itself (and sets the multipart boundary).
        body: { file: file.name },
        bodySerializer: () => form,
        headers: withKey(key),
        signal,
        // Uploads may take longer than the default request timeout.
        fetch: (request: Request) => globalThis.fetch(request),
      }),
    );
  },
  documentDelete: (kbId: string, docId: string, key?: string) =>
    mutate(
      api.DELETE('/api/v1/knowledge-bases/{kb_id}/documents/{document_id}', {
        params: { path: { kb_id: kbId, document_id: docId } },
        headers: headers(key),
      }),
    ),
  documentReindex: (kbId: string, docId: string, key?: string) =>
    mutate(
      api.POST('/api/v1/knowledge-bases/{kb_id}/documents/{document_id}/reindex', {
        params: { path: { kb_id: kbId, document_id: docId } },
        headers: headers(key),
      }),
    ),
  knowledgeQuery: (kbId: string, body: KnowledgeQueryIn) =>
    mutate(api.POST('/api/v1/knowledge-bases/{kb_id}/query', { params: { path: { kb_id: kbId } }, body })),

  // Chat citations
  citation: (sessionId: string, messageId: string, citationId: string) =>
    unwrap(
      api.GET('/api/v1/chat/sessions/{session_id}/messages/{message_id}/citations/{citation_id}', {
        params: { path: { session_id: sessionId, message_id: messageId, citation_id: citationId } },
      }),
    ),
};
