/**
 * Chat replies stream back from POST /chat/sessions/{id}/messages as SSE
 * ('message', 'delta', 'done', 'error'). EventSource cannot POST, so this reads the
 * stream with fetch. Aborting the request is the Stop action: the server stores the
 * partial reply as "stopped".
 */
import { observeResponse, securityHeaders } from './client';
import { connectivity } from './connectivity';
import { fromEnvelope, toApiError } from './errors';
import type { ChatMessageOut } from './schema';
import { readEventStream } from './sse';

export interface ChatStreamHandlers {
  /** First event: the stored user message, the assistant message id and, for
   * knowledge-grounded sessions, the citations the answer may reference. */
  onMessage?: (userMessage: ChatMessageOut, assistantMessageId: string, citations: unknown[]) => void;
  onDelta?: (text: string) => void;
  onDone?: (message: ChatMessageOut) => void;
  onError?: (error: { code: string; message: string }) => void;
}

export async function sendChatMessage(
  sessionId: string,
  content: string,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const headers = new Headers({ 'Content-Type': 'application/json', Accept: 'text/event-stream' });
  securityHeaders('POST', headers);
  let response: Response;
  try {
    response = await fetch(
      new URL(`/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/messages`, window.location.origin),
      {
        method: 'POST',
        headers,
        body: JSON.stringify({ content }),
        credentials: 'same-origin',
        signal,
      },
    );
  } catch (error) {
    const apiError = toApiError(error);
    if (apiError.isNetwork) connectivity.failed();
    throw apiError;
  }
  observeResponse(response);
  if (!response.ok || !response.body) {
    const text = await response.text();
    let body: unknown;
    try {
      body = text ? JSON.parse(text) : undefined;
    } catch {
      body = undefined;
    }
    throw fromEnvelope(response.status, body);
  }
  for await (const event of readEventStream(response.body)) {
    let data: unknown;
    try {
      data = JSON.parse(event.data);
    } catch {
      continue;
    }
    const d = data as Record<string, unknown>;
    switch (event.event) {
      case 'message':
        handlers.onMessage?.(
          d.userMessage as ChatMessageOut,
          typeof d.assistantMessageId === 'string' ? d.assistantMessageId : '',
          Array.isArray(d.citations) ? d.citations : [],
        );
        break;
      case 'delta':
        if (typeof d.text === 'string') handlers.onDelta?.(d.text);
        break;
      case 'done':
        handlers.onDone?.(d as unknown as ChatMessageOut);
        break;
      case 'error':
        handlers.onError?.({
          code: typeof d.code === 'string' ? d.code : 'CHAT_ERROR',
          message: typeof d.message === 'string' ? d.message : 'The model could not answer.',
        });
        break;
      default:
        break;
    }
  }
}
