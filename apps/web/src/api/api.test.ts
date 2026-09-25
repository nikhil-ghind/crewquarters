import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { errorEnvelope, server } from '../test/server';
import { api, idempotencyKey, mutate, unwrap, withKey } from './client';
import { connectivity } from './connectivity';
import { ApiError } from './errors';
import { endpoints as pendingApi } from './endpoints';
import { session } from './session';
import { mergeEvents } from './streams';
import { event } from '../test/fixtures';

describe('typed client', () => {
  it('sends the CSRF header and an Idempotency-Key on mutations only', async () => {
    const seen: Record<string, string | null>[] = [];
    server.use(
      http.post('/api/v1/runs', ({ request }) => {
        seen.push({ csrf: request.headers.get('X-CSRF-Token'), key: request.headers.get('Idempotency-Key') });
        return HttpResponse.json({ id: 'r1' }, { status: 201 });
      }),
      http.get('/api/v1/settings', ({ request }) => {
        seen.push({ csrf: request.headers.get('X-CSRF-Token'), key: request.headers.get('Idempotency-Key') });
        return HttpResponse.json({});
      }),
    );
    session.signedIn('csrf-abc');
    await mutate(api.POST('/api/v1/runs', { body: { installationId: 'i1' }, headers: withKey('fixed-key-123') }));
    await mutate(api.POST('/api/v1/runs', { body: { installationId: 'i1' } }));
    await unwrap(api.GET('/api/v1/settings'));
    expect(seen[0]).toEqual({ csrf: 'csrf-abc', key: 'fixed-key-123' });
    expect(seen[1]?.csrf).toBe('csrf-abc');
    expect(seen[1]?.key).toMatch(/^[0-9a-f-]{36}$/);
    expect(seen[2]).toEqual({ csrf: null, key: null });
  });

  it('falls back to the readable cq_csrf cookie', async () => {
    let header: string | null = null;
    server.use(
      http.post('/api/v1/runs', ({ request }) => {
        header = request.headers.get('X-CSRF-Token');
        return HttpResponse.json({ id: 'r1' }, { status: 201 });
      }),
    );
    document.cookie = 'cq_csrf=cookie-token; path=/';
    await mutate(api.POST('/api/v1/runs', { body: { installationId: 'i1' } }));
    expect(header).toBe('cookie-token');
  });

  it('maps the error envelope to ApiError with field errors', async () => {
    server.use(
      http.post('/api/v1/agent-installations', () =>
        errorEnvelope(422, 'VALIDATION_FAILED', 'The request is invalid.', {
          errors: [{ path: '/config/spreadsheetId', message: 'Field required' }],
        }),
      ),
    );
    const error = await mutate(
      api.POST('/api/v1/agent-installations', { body: { agentId: 'caller', approvedPermissions: {}, enabled: true } }),
    ).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.code).toBe('VALIDATION_FAILED');
    expect(apiError.requestId).toBe('req-test');
    expect(apiError.fieldErrors).toEqual([{ path: '/config/spreadsheetId', message: 'Field required' }]);
  });

  it('marks the session expired on 401 while signed in', async () => {
    server.use(http.get('/api/v1/runs', () => errorEnvelope(401, 'SESSION_EXPIRED', 'Your session has expired.')));
    session.signedIn('x');
    await expect(unwrap(api.GET('/api/v1/runs'))).rejects.toMatchObject({ code: 'SESSION_EXPIRED' });
    expect(session.get().status).toBe('expired');
  });

  it('marks the device offline on network errors and on a proxy 502', async () => {
    server.use(http.get('/api/v1/runs', () => HttpResponse.error()));
    await expect(unwrap(api.GET('/api/v1/runs'))).rejects.toMatchObject({ code: 'NETWORK_ERROR' });
    expect(connectivity.get().api).toBe('offline');
    server.use(http.get('/api/v1/runs', () => new HttpResponse('Bad gateway', { status: 502, headers: { 'Content-Type': 'text/plain' } })));
    await expect(unwrap(api.GET('/api/v1/runs'))).rejects.toBeInstanceOf(ApiError);
    expect(connectivity.get().api).toBe('offline');
    server.use(http.get('/api/v1/runs', () => HttpResponse.json({ items: [], nextCursor: null })));
    await unwrap(api.GET('/api/v1/runs'));
    expect(connectivity.get().api).toBe('online');
  });

  it('generates RFC 4122 v4 keys without crypto.randomUUID', () => {
    const key = idempotencyKey();
    expect(key).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect(idempotencyKey()).not.toBe(key);
  });
});

describe('pending (hand-written) routes', () => {
  it('uploads multipart with CSRF and the caller key', async () => {
    let seen: { key: string | null; csrf: string | null; name: string | null } | null = null;
    server.use(
      http.post('/api/v1/knowledge-bases/:kb/documents', ({ request }) => {
        // jsdom's FormData does not round-trip through undici, so only check that the
        // client left the multipart Content-Type to the browser (no JSON header).
        const contentType = request.headers.get('Content-Type') ?? '';
        seen = {
          key: request.headers.get('Idempotency-Key'),
          csrf: request.headers.get('X-CSRF-Token'),
          name: contentType.includes('application/json') ? 'json' : 'not-json',
        };
        return HttpResponse.json({ id: 'd1', state: 'PENDING' }, { status: 202 });
      }),
    );
    session.signedIn('csrf-up');
    const out = await pendingApi.documentUpload('kb1', new File(['hello'], 'notes.md'), 'upload-key-1');
    expect(out.state).toBe('PENDING');
    expect(seen).toEqual({ key: 'upload-key-1', csrf: 'csrf-up', name: 'not-json' });
  });

  it('never returns secrets: Twilio save sends them once and the response is status only', async () => {
    server.use(
      http.put('/api/v1/connections/twilio', async ({ request }) => {
        const body = (await request.json()) as Record<string, string>;
        expect(body.authToken).toBe('secret-token');
        return HttpResponse.json({ provider: 'twilio', displayName: 'Twilio', status: 'CONNECTED', grantedCapabilities: [], lastCheckedAt: null });
      }),
    );
    const out = await pendingApi.twilioSave({ accountSid: 'AC' + '0'.repeat(32), authToken: 'secret-token', fromNumber: '+15551234567' });
    expect(JSON.stringify(out)).not.toContain('secret-token');
  });
});

describe('run event merge', () => {
  it('orders by sequence and drops duplicates from reconnects', () => {
    const a = event(1, 'run.state_changed', { from: null, to: 'QUEUED' });
    const b = event(2, 'run.progress', { message: 'Working' });
    const c = event(3, 'run.log', { level: 'info', message: 'x' });
    const merged = mergeEvents([a, b], [b, c, a]);
    expect(merged.map((e) => e.sequence)).toEqual([1, 2, 3]);
    expect(mergeEvents(merged, [c])).toBe(merged);
  });
});
