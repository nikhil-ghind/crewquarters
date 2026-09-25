/**
 * HAND-WRITTEN API TYPES AWAITING OPENAPI REGENERATION.
 *
 * TODO(contracts): every type and path in this file mirrors a public control-API
 * route or field added in commit 142b289 (branch worktree-agent-aef336634d890f61d:
 * connections, provider profiles, knowledge bases/documents/query, chat citation
 * resolve, ConnectionOut.UNKNOWN/account/detail, SettingsOut.callbackUrls,
 * RunEventOut.occurredAt). The shapes are copied from that commit's regenerated
 * packages/contracts/clients/typescript/schema.d.ts, but the committed schema.d.ts on
 * this branch predates it.
 *
 * When schema.d.ts on this branch includes them:
 *   1. replace each type below with `Schemas['<Name>']` from ./schema.ts (same names);
 *   2. move the calls in ./pending.ts onto the typed `api` client in ./client.ts;
 *   3. delete this file.
 *
 * This is the ONLY place in the web app where API shapes are written by hand.
 */
import type { ConnectionOut as GeneratedConnectionOut, SettingsOut as GeneratedSettingsOut } from './schema';

/** Paths of the pending routes (same-origin `/api/v1` prefix). */
export const PENDING_PATHS = {
  googleStart: '/api/v1/connections/google/start',
  googleTest: '/api/v1/connections/google/test',
  google: '/api/v1/connections/google',
  twilio: '/api/v1/connections/twilio',
  twilioTest: '/api/v1/connections/twilio/test',
  twilioTestCall: '/api/v1/connections/twilio/test-call',
  providerProfiles: '/api/v1/provider-profiles',
  providerProfile: (id: string) => `/api/v1/provider-profiles/${encodeURIComponent(id)}`,
  providerProfileTest: (id: string) => `/api/v1/provider-profiles/${encodeURIComponent(id)}/test`,
  knowledgeBases: '/api/v1/knowledge-bases',
  knowledgeBase: (id: string) => `/api/v1/knowledge-bases/${encodeURIComponent(id)}`,
  documents: (kbId: string) => `/api/v1/knowledge-bases/${encodeURIComponent(kbId)}/documents`,
  document: (kbId: string, docId: string) =>
    `/api/v1/knowledge-bases/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(docId)}`,
  documentReindex: (kbId: string, docId: string) =>
    `/api/v1/knowledge-bases/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(docId)}/reindex`,
  knowledgeQuery: (kbId: string) => `/api/v1/knowledge-bases/${encodeURIComponent(kbId)}/query`,
  citation: (sessionId: string, messageId: string, citationId: string) =>
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/citations/${encodeURIComponent(citationId)}`,
} as const;

/** Where the capability broker returns the browser after Google consent
 * (`?result=connected` or `?result=error&code=...`). */
export const GOOGLE_RETURN_ROUTE = '/connections/google';

// --- Changed generated types ------------------------------------------------------------

/** ConnectionOut adds status UNKNOWN (broker unreachable) and optional account/detail. */
export type ConnectionOut = Omit<GeneratedConnectionOut, 'status'> & {
  status: GeneratedConnectionOut['status'] | 'UNKNOWN';
  account?: string | null;
  detail?: string | null;
};
export type ConnectionStatus = ConnectionOut['status'];

/** SettingsOut adds read-only callbackUrls (googleRedirectUri, twilioCallbackBase). */
export type SettingsOut = Omit<GeneratedSettingsOut, 'callbackBaseUrl'> & {
  callbackBaseUrl: string | null;
  callbackUrls?: { [key: string]: string };
};

// --- Connections ------------------------------------------------------------------------

export type GoogleCapability = 'gmail.readonly' | 'spreadsheets';

export interface GoogleStartIn {
  capabilities: GoogleCapability[];
}
export interface GoogleStartOut {
  authorizationUrl: string;
}

export interface TwilioCredentialsIn {
  accountSid: string;
  authToken: string;
  fromNumber: string;
}

export interface TwilioTestCallIn {
  to: string;
  confirm: boolean;
}
export interface TwilioTestCallOut {
  placed: boolean;
  to: string;
  status?: string | null;
}

export interface ProviderProfileOut {
  id: string;
  provider: 'openai' | 'anthropic';
  displayName: string;
  allowedModels: string[];
  budgets: { [key: string]: unknown };
  enabled: boolean;
  status: string;
  lastCheckedAt: string | null;
}

export interface ProviderProfileCreateIn {
  provider: 'openai' | 'anthropic';
  displayName: string;
  apiKey: string;
  allowedModels?: string[];
  budgets?: { [key: string]: unknown };
  enabled: boolean;
}

export interface ProviderProfileTestOut {
  status: 'CONNECTED' | 'ERROR';
  detail?: string | null;
  checkedAt: string;
}

// --- Knowledge ----------------------------------------------------------------------------

export interface KnowledgeBaseOut {
  id: string;
  name: string;
  embeddingProfile: string;
  embeddingDimension: number;
  createdAt: string;
}

export interface KnowledgeBaseCreateIn {
  name: string;
}

export type DocumentState = 'PENDING' | 'PROCESSING' | 'READY' | 'FAILED';

export interface DocumentOut {
  id: string;
  knowledgeBaseId: string;
  name: string;
  mime: string;
  bytes: number;
  sha256: string;
  state: DocumentState;
  extracted?: { [key: string]: unknown } | null;
  error?: { [key: string]: unknown } | null;
  createdAt: string;
  updatedAt: string;
}

export interface KnowledgeFilters {
  documentIds?: string[];
}

export interface KnowledgeQueryIn {
  query: string;
  topK: number;
  maxContextTokens: number;
  filters?: KnowledgeFilters;
}

export interface PassageDocument {
  id: string;
  name: string;
}

export interface PassageOut {
  citationId: string;
  text: string;
  score: number;
  document: PassageDocument;
  locator: { [key: string]: unknown };
  location: string;
}

export interface KnowledgeQueryOut {
  knowledgeBaseId: string;
  passages: PassageOut[];
}

/** Stored on assistant messages (ChatMessageOut.citations items) and returned by the
 * citation resolve route with documentAvailable/documentState. */
export interface CitationOut {
  index: number;
  citationId: string;
  text: string;
  score?: number | null;
  document: PassageDocument;
  locator: { [key: string]: unknown };
  location: string;
  knowledgeBaseId: string;
  documentAvailable: boolean;
  documentState?: string | null;
}

/** A stored citation before resolution (no documentAvailable yet). */
export type StoredCitation = Omit<CitationOut, 'documentAvailable' | 'documentState'> & {
  documentAvailable?: boolean;
  documentState?: string | null;
};

export interface Page<T> {
  items: T[];
  nextCursor?: string | null;
}

/** RunEventOut adds occurredAt (when the agent produced the event). */
export interface RunEventTimes {
  occurredAt?: string | null;
}

/** Narrow an untyped citation object (ChatMessageOut.citations item). */
export function toCitation(value: unknown, position: number): StoredCitation | null {
  if (typeof value !== 'object' || value === null) return null;
  const v = value as Record<string, unknown>;
  const doc = (typeof v.document === 'object' && v.document !== null ? v.document : {}) as Record<string, unknown>;
  if (typeof v.citationId !== 'string') return null;
  return {
    index: typeof v.index === 'number' ? v.index : position + 1,
    citationId: v.citationId,
    text: typeof v.text === 'string' ? v.text : '',
    score: typeof v.score === 'number' ? v.score : null,
    document: {
      id: typeof doc.id === 'string' ? doc.id : '',
      name: typeof doc.name === 'string' ? doc.name : 'Document',
    },
    locator: typeof v.locator === 'object' && v.locator !== null ? (v.locator as Record<string, unknown>) : {},
    location: typeof v.location === 'string' ? v.location : '',
    knowledgeBaseId: typeof v.knowledgeBaseId === 'string' ? v.knowledgeBaseId : '',
    documentAvailable: typeof v.documentAvailable === 'boolean' ? v.documentAvailable : undefined,
    documentState: typeof v.documentState === 'string' ? v.documentState : null,
  };
}

/** Narrow a DocumentOut.error object. */
export function documentError(error: DocumentOut['error']): { code: string; message: string } | null {
  if (!error) return null;
  return {
    code: typeof error.code === 'string' ? error.code : 'FAILED',
    message: typeof error.message === 'string' ? error.message : 'The document could not be indexed.',
  };
}

export function documentChunks(doc: DocumentOut): number | null {
  const chunks = doc.extracted?.chunks;
  return typeof chunks === 'number' ? chunks : null;
}
