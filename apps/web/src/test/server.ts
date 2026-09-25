/**
 * MSW server with default handlers for the generated API. Tests override individual
 * routes with `server.use(...)`.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import * as f from './fixtures';

const page = <T,>(items: T[]) => ({ items, nextCursor: null });

export const handlers = [
  http.get('/api/v1/me', () => HttpResponse.json(f.session)),
  http.get('/api/v1/settings', () => HttpResponse.json(f.settings)),
  http.get('/api/v1/system/status', () => HttpResponse.json(f.systemStatus)),
  http.get('/api/v1/system/memory', () => HttpResponse.json(f.memory)),
  http.get('/api/v1/attention', () => HttpResponse.json(f.attention)),
  http.get('/api/v1/models', () => HttpResponse.json(page([f.model]))),
  http.get('/api/v1/models/:id', () => HttpResponse.json(f.model)),
  http.get('/api/v1/connections', () => HttpResponse.json(page(f.connections))),
  http.get('/api/v1/catalog/agents', () => HttpResponse.json(page([f.catalogCaller]))),
  http.get('/api/v1/agent-installations', () => HttpResponse.json(page([f.installation]))),
  http.get('/api/v1/schedules', () => HttpResponse.json(page([]))),
  http.get('/api/v1/runs', () => HttpResponse.json(page([f.run]))),
  http.get('/api/v1/runs/:id', () => HttpResponse.json(f.run)),
  http.get('/api/v1/input-requests', () => HttpResponse.json(page([f.approvalRequest]))),
  http.get('/api/v1/knowledge-bases', () => HttpResponse.json(page([]))),
];

export const server = setupServer(...handlers);

export function errorEnvelope(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
  return HttpResponse.json({ error: { code, message, requestId: 'req-test', details } }, { status });
}
