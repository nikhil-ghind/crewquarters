import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { session } from '../api/session';
import type { InputRequestOut } from '../api/schema';
import { RequireAuth } from '../shell/AuthGate';
import * as f from '../test/fixtures';
import { renderWithProviders } from '../test/render';
import { errorEnvelope, server } from '../test/server';
import { CallerResult, parseCaller } from './activity/results/CallerResult';
import { GmailDigestResult, parseDigest } from './activity/results/GmailDigestResult';
import { PersonalSpaceResult, parsePersonalSpace } from './activity/results/PersonalSpaceResult';
import { PrReviewResult, parsePrReview } from './activity/results/PrReviewResult';
import { ResultView } from './activity/results/ResultView';
import { eventEntry } from './activity/RunDetailPage';
import LoginPage, { safeNext } from './auth/LoginPage';
import { InputRequestCard } from './common/InputRequestCard';
import { cronFor, draftFromCron, formatLocal, newDraft } from './schedules/ScheduleEditor';
import { profileStatus } from './connections/forms';

describe('Crew Request card', () => {
  it('shows the caller preview, consequence and count, and submits the saved version once', async () => {
    const answers: unknown[] = [];
    server.use(
      http.post('/api/v1/input-requests/:id/answer', async ({ request }) => {
        answers.push({ body: await request.json(), key: request.headers.get('Idempotency-Key') });
        const answered: InputRequestOut = { ...f.approvalRequest, state: 'answered', answeredAt: f.NOW, version: 3, answer: { choice: 'approve' } };
        return HttpResponse.json(answered);
      }),
    );
    session.signedIn('csrf');
    const { container } = renderWithProviders(<InputRequestCard request={f.approvalRequest} timeZone="Asia/Kolkata" />);
    expect(screen.getByRole('heading', { name: 'Approve 3 automated calls' })).toBeInTheDocument();
    expect(screen.getByText('••••21')).toBeInTheDocument();
    expect(screen.getByText(/3 automated calls will be placed now/)).toBeInTheDocument();
    // Agent-supplied text is never interpreted as HTML.
    expect(container.querySelector('b')).toBeNull();
    expect(screen.getByText(/<b>not bold<\/b>/)).toBeInTheDocument();
    const approve = screen.getByRole('button', { name: 'Approve 3 calls' });
    await userEvent.click(approve);
    await screen.findByText(/The run continues on its own/);
    expect(answers).toHaveLength(1);
    expect(answers[0]).toMatchObject({ body: { version: 2, value: { choice: 'approve' } } });
    expect(screen.queryByRole('button', { name: 'Approve 3 calls' })).not.toBeInTheDocument();
  });

  it('reports a double answer instead of answering again', async () => {
    server.use(http.post('/api/v1/input-requests/:id/answer', () => errorEnvelope(409, 'INPUT_ALREADY_CLOSED', 'This request is already answered.')));
    renderWithProviders(<InputRequestCard request={f.approvalRequest} />);
    await userEvent.click(screen.getByRole('button', { name: 'Cancel run' }));
    expect(await screen.findByText(/already answered, so your answer was not sent again/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve 3 calls' })).not.toBeInTheDocument();
  });

  it('renders a schema form for non-choice questions and validates locally first', async () => {
    let calls = 0;
    server.use(
      http.post('/api/v1/input-requests/:id/answer', () => {
        calls += 1;
        return HttpResponse.json({ ...f.approvalRequest, state: 'answered' });
      }),
    );
    const question: InputRequestOut = {
      ...f.approvalRequest,
      title: 'Which label?',
      preview: null,
      schema: { type: 'object', required: ['label'], properties: { label: { type: 'string', minLength: 2 } } },
    };
    renderWithProviders(<InputRequestCard request={question} />);
    await userEvent.click(screen.getByRole('button', { name: 'Send answer' }));
    expect(screen.getAllByText('Label is required.').length).toBeGreaterThan(0);
    expect(calls).toBe(0);
    await userEvent.type(screen.getByRole('textbox', { name: /Label/ }), 'Work');
    await userEvent.click(screen.getByRole('button', { name: 'Send answer' }));
    await waitFor(() => expect(calls).toBe(1));
  });
});

describe('result renderers', () => {
  const digest = parseDigest({
    date: '2026-09-24',
    timezone: 'Asia/Kolkata',
    processedCount: 200,
    truncated: true,
    counts: { urgent: 1, important: 1, lowPriority: 0, needsReview: 1 },
    groups: {
      urgent: [{ messageId: 'm1', threadId: 't1', from: 'a@example.com', subject: '<img src=x onerror=alert(1)>', receivedAt: f.NOW, reason: 'Outage', nextAction: 'Reply', needsReview: false, gmailLink: 'https://mail.google.com/mail/u/0/#inbox/m1' }],
      important: [{ messageId: 'm2', threadId: 't2', from: 'b@example.com', subject: 'Invoice', receivedAt: null, reason: 'Due', nextAction: 'Pay', needsReview: true, gmailLink: 'javascript:alert(1)' }],
      lowPriority: [],
    },
    model: { profile: 'local.general.small', provider: 'local', locality: 'local' },
  });

  it('Gmail digest: ordered sections, truncation warning, safe links, message IDs', () => {
    expect(digest).not.toBeNull();
    if (!digest) return;
    const { container } = renderWithProviders(<GmailDigestResult digest={digest} />);
    const labels = [...container.querySelectorAll('.result-section-header .badge, .result-section > summary .badge')].map((b) => b.textContent);
    expect(labels).toEqual(['Urgent', 'Important', 'Low priority']);
    expect(screen.getByText('This digest may be incomplete')).toBeInTheDocument();
    expect(screen.getByText('Needs review')).toBeInTheDocument();
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument();
    const links = screen.getAllByRole('link', { name: /Open in Gmail/ });
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute('href', 'https://mail.google.com/mail/u/0/#inbox/m1');
    expect(links[0]).toHaveAttribute('rel', 'noopener noreferrer');
    expect(screen.getByText('ID m2')).toBeInTheDocument();
    expect(screen.getByText('Local on this device')).toBeInTheDocument();
  });

  it('Caller: summary separates outcomes; no-answer is not a failure; skipped rows are not called', () => {
    const data = parseCaller({
      operatorDecision: 'approved',
      summary: { called: 2, answered: 1, responsesCaptured: 1, skipped: 1, failed: 0 },
      rows: [
        { row: 2, name: 'Asha', phoneMasked: '••••21', consent: 'validated', callStatus: 'answered_speech', transcript: 'Yes', sheetWrite: 'written', completedAt: f.NOW },
        { row: 3, name: 'Ben', phoneMasked: '••••57', consent: 'validated', callStatus: 'no_answer', transcript: null, sheetWrite: 'pending_retry', completedAt: f.NOW },
        { row: 4, name: 'Chen', phoneMasked: '••••90', consent: 'skipped', skipReason: 'No consent' },
      ],
    });
    expect(data).not.toBeNull();
    if (!data) return;
    renderWithProviders(<CallerResult data={data} timeZone="Asia/Kolkata" />);
    const summary = screen.getByRole('group', { name: 'Call summary' });
    expect(within(summary).getByText('Failed').previousSibling).toHaveTextContent('0');
    expect(screen.getByText('No answer')).toBeInTheDocument();
    expect(screen.getByText('Not called')).toBeInTheDocument();
    expect(screen.getByText('No consent')).toBeInTheDocument();
    expect(screen.getByText(/Retrying a sheet write never calls anyone again/)).toBeInTheDocument();
  });

  it('falls back to escaped generic output for unknown renderers', () => {
    renderWithProviders(<ResultView result={{ summary: '<script>x</script>' }} resultSchema={null} timeZone="UTC" />);
    expect(screen.getByText('<script>x</script>')).toBeInTheDocument();
  });

  const spaceResult = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
    status: 'ready',
    domain: 'Personal notes',
    intent: 'Plan my week',
    summary: 'Your notes cover goals and a project.',
    generatedAt: f.NOW,
    degraded: false,
    themes: [{ title: 'Goals', summary: 'Health first.', citations: ['c2', 'c-unknown'] }],
    highlights: [
      { title: '<img src=x onerror=alert(1)>', whyItMatters: 'Nearest deadline', nextStep: 'Finish the alerts page', citations: ['c1', 'c2'] },
      { title: 'No next step', whyItMatters: 'Just because', nextStep: null, citations: ['c1'] },
    ],
    exploreNext: ['What is left on the dashboard?'],
    suggestedAdditions: [],
    sources: [
      { citationId: 'c1', documentName: 'projects.md', locator: { section: 'Home-lab dashboard' }, score: 0.82 },
      { citationId: 'c2', documentName: 'goals.md', locator: { page: 3 }, score: 0.7 },
    ],
    stats: { queriesRun: 9, passagesConsidered: 14, passagesUsed: 6, documentsSeen: 3 },
    model: { profile: 'local.general.small', provider: 'local', locality: 'local' },
    ...over,
  });

  it('Personal Space: highlights, themes, explore next and numbered sources, all as text', () => {
    const data = parsePersonalSpace(spaceResult());
    expect(data).not.toBeNull();
    if (!data) return;
    const { container } = renderWithProviders(<PersonalSpaceResult data={data} timeZone="Asia/Kolkata" />);
    const labels = [...container.querySelectorAll('.result-section-header .badge, .result-section > summary .badge')].map((b) => b.textContent);
    expect(labels).toEqual(['Highlights', 'Themes', 'Explore next', 'Sources']);
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument();
    expect(screen.getByText('Personal notes')).toBeInTheDocument();
    expect(screen.getByText('Plan my week')).toBeInTheDocument();
    expect(screen.getByText('Local on this device')).toBeInTheDocument();
    // c1 is Source 1, c2 is Source 2; an unknown citation id is not shown.
    expect(screen.getAllByText('Source 1').length).toBe(2);
    expect(screen.getAllByText('Source 2').length).toBe(2);
    expect(screen.queryByText(/c-unknown/)).toBeNull();
    expect(screen.getByText('projects.md')).toBeInTheDocument();
    expect(screen.getByText(/Page 3/)).toBeInTheDocument();
    expect(screen.getByText(/Home-lab dashboard/)).toBeInTheDocument();
    expect(screen.getByText(/Searched with 9 queries; used 6 passages from 3 documents/)).toBeInTheDocument();
    expect(screen.queryByText('The model could not write a brief this time')).toBeNull();
  });

  it('Personal Space: a thin knowledge base shows what to add instead of an empty brief', () => {
    const data = parsePersonalSpace(
      spaceResult({
        status: 'insufficient_context',
        domain: null,
        summary: 'Only 1 relevant passage(s) were found.',
        themes: [],
        highlights: [],
        exploreNext: [],
        suggestedAdditions: ['Add notes about your goals.'],
        sources: [],
        stats: { queriesRun: 5, passagesConsidered: 1, passagesUsed: 1, documentsSeen: 1 },
      }),
    );
    expect(data).not.toBeNull();
    if (!data) return;
    renderWithProviders(<PersonalSpaceResult data={data} timeZone="UTC" />);
    expect(screen.getByText('Not enough in this knowledge base to personalize yet')).toBeInTheDocument();
    expect(screen.getByText('Add notes about your goals.')).toBeInTheDocument();
    expect(screen.queryByText('Highlights')).toBeNull();
  });

  it('Personal Space: a degraded result says so and labels the fallback section honestly', () => {
    const data = parsePersonalSpace(spaceResult({ degraded: true, domain: null, highlights: [], exploreNext: [] }));
    expect(data).not.toBeNull();
    if (!data) return;
    renderWithProviders(<PersonalSpaceResult data={data} timeZone="UTC" />);
    expect(screen.getByText('The model could not write a brief this time')).toBeInTheDocument();
    expect(screen.getByText('From your documents')).toBeInTheDocument();
    expect(screen.queryByText('Highlights')).toBeNull();
  });

  it('Personal Space: chosen by the declared renderer or by shape, and never for unrelated results', () => {
    const { unmount } = renderWithProviders(
      <ResultView result={spaceResult()} resultSchema={{ 'x-crewquarters-renderer': 'crewquarters.personal-space/v1' }} timeZone="UTC" />,
    );
    expect(screen.getByRole('region', { name: 'Highlights' })).toBeInTheDocument();
    unmount();
    renderWithProviders(<ResultView result={spaceResult()} resultSchema={null} timeZone="UTC" />);
    expect(screen.getByRole('region', { name: 'Themes' })).toBeInTheDocument();
    expect(parsePersonalSpace({ status: 'ok', summary: 'x' })).toBeNull();
    expect(parsePersonalSpace({ status: 'ready' })).toBeNull();
    expect(parsePersonalSpace(null)).toBeNull();
  });
});

describe('run timeline entries', () => {
  it('maps structured events to operator language', () => {
    expect(eventEntry(f.event(1, 'run.state_changed', { from: 'RUNNING', to: 'LOADING_MODEL' }), 'UTC')?.title).toBe('Loading local model');
    expect(eventEntry(f.event(2, 'run.input_requested', { inputRequestId: 'x', key: 'k', title: 'Approve' }), 'UTC')?.title).toBe('Asked: Approve');
    expect(eventEntry(f.event(3, 'run.log', { level: 'info', message: 'x' }), 'UTC')).toBeNull();
  });
});

describe('schedule presets', () => {
  it('builds cron from presets and recognizes them again', () => {
    const d = { ...newDraft('Asia/Kolkata'), preset: 'weekdays' as const, time: '09:30' };
    expect(cronFor(d)).toBe('30 9 * * 1-5');
    expect(draftFromCron('0 10 * * *', 'UTC', 'skip')).toMatchObject({ preset: 'daily', time: '10:00', misfirePolicy: 'skip' });
    expect(draftFromCron('*/5 * * * *', 'UTC', 'fire_once').preset).toBe('custom');
    expect(formatLocal('2026-09-26T10:00:00+05:30')).toMatch(/10:00 \(UTC\+05:30\)/);
  });
});

describe('sign-in and session expiry', () => {
  it('only accepts same-app return paths', () => {
    expect(safeNext('/runs/1')).toBe('/runs/1');
    expect(safeNext('//evil.example')).toBe('/');
    expect(safeNext('https://evil.example')).toBe('/');
    expect(safeNext(null)).toBe('/');
  });

  it('redirects an anonymous visitor to /login with the return path', async () => {
    server.use(http.get('/api/v1/me', () => errorEnvelope(401, 'UNAUTHENTICATED', 'Sign in to continue.')));
    renderWithProviders(
      <RequireAuth>
        <p>Secret page</p>
      </RequireAuth>,
      { path: '/runs/:id', route: '/runs/42', extraRoutes: [{ path: '/login', element: <LoginPage /> }] },
    );
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
    expect(screen.queryByText('Secret page')).not.toBeInTheDocument();
  });

  it('signs in, keeps the CSRF token and returns to the page', async () => {
    let signedIn = false;
    server.use(
      http.get('/api/v1/me', () => (signedIn ? HttpResponse.json(f.session) : errorEnvelope(401, 'UNAUTHENTICATED', 'Sign in.'))),
      http.post('/api/v1/sessions', async ({ request }) => {
        const body = (await request.json()) as { username: string; password: string };
        if (body.password !== 'correct-horse-battery') return errorEnvelope(401, 'INVALID_CREDENTIALS', 'The username or password is incorrect.');
        signedIn = true;
        return HttpResponse.json(f.session, { status: 201 });
      }),
    );
    renderWithProviders(<LoginPage />, {
      path: '/login',
      route: '/login?next=%2Fruns%2F42',
      extraRoutes: [{ path: '/runs/:id', element: <p>Run page</p> }],
    });
    await userEvent.type(screen.getByLabelText(/Username/), 'owner');
    await userEvent.type(screen.getByLabelText(/Password/), 'wrong-password-1');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByText('The username or password is incorrect.')).toBeInTheDocument();
    expect(screen.getByLabelText(/Password/)).toHaveValue('');
    await userEvent.type(screen.getByLabelText(/Password/), 'correct-horse-battery');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByText('Run page')).toBeInTheDocument();
    expect(session.csrfToken()).toBe('csrf-token-1');
  });
});

describe('cloud key status', () => {
  const base = { id: 'p1', provider: 'openai' as const, displayName: 'OpenAI key', allowedModels: [], budgets: {}, enabled: true, lastCheckedAt: null };
  it('shows a rejected key as needing attention, and untested keys honestly', () => {
    expect(profileStatus({ ...base, status: 'ERROR' }).label).toBe('Needs attention');
    expect(profileStatus({ ...base, status: 'CONNECTED' }).label).toBe('Connected');
    expect(profileStatus({ ...base, status: 'UNTESTED' }).label).toBe('Not tested yet');
    expect(profileStatus({ ...base, status: 'CONNECTED', enabled: false }).label).toBe('Disabled');
  });
});

describe('PR review result renderer', () => {
  const data = parsePrReview({
    repo: 'acme/shop',
    dryRun: true,
    counts: { pulls: 2, reviewed: 1, skipped: 1, findings: 1, bugs: 1, style: 0, posted: 0 },
    pulls: [
      {
        number: 7,
        title: '<img src=x onerror=alert(1)>',
        author: 'dev',
        url: 'https://github.com/acme/shop/pull/7',
        headSha: 'abc',
        status: 'reviewed',
        skipReason: null,
        filesReviewed: 1,
        filesSkipped: 0,
        truncated: false,
        droppedFindings: 0,
        posted: false,
        reviewUrl: null,
        postNote: null,
        findings: [{ path: 'a.py', line: 3, category: 'bug', severity: 'high', comment: 'Off by one.', suggestion: null }],
      },
      {
        number: 8,
        title: 'Elsewhere',
        author: 'dev',
        url: 'javascript:alert(1)',
        headSha: 'def',
        status: 'skipped',
        skipReason: 'Already reviewed at this commit',
      },
    ],
  });

  it('shows the dry-run notice, findings as text, and only safe GitHub links', () => {
    expect(data).not.toBeNull();
    if (!data) return;
    const { container } = renderWithProviders(<PrReviewResult data={data} />);
    expect(screen.getByText('Dry run')).toBeInTheDocument();
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText(/Off by one\./)).toBeInTheDocument();
    expect(screen.getByText('Skipped: Already reviewed at this commit')).toBeInTheDocument();
    const links = container.querySelectorAll('a');
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute('href', 'https://github.com/acme/shop/pull/7');
    expect(links[0]).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('rejects a result that is not a PR review', () => {
    expect(parsePrReview({ rows: [] })).toBeNull();
    expect(parsePrReview(null)).toBeNull();
  });
});
