/** Narrowing helpers for untyped fields in the knowledge and chat contracts. */
import type { CitationOut, DocumentOut } from '../api/schema';

/** A citation stored on a chat message (ChatMessageOut.citations items are untyped);
 * documentAvailable/documentState are known only after resolving it. */
export type StoredCitation = Omit<CitationOut, 'documentAvailable' | 'documentState'> & {
  documentAvailable?: boolean;
  documentState?: string | null;
};

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
