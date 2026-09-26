/** Run now placements (PLAN.md 13.5, 13.6, 13.11): agent detail, install finish, Home, Schedules. */
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { InstallationOut, ScheduleOut } from '../../api/schema';
import { permissionItems } from '../../lib/permissions';
import { writeDraft } from '../../lib/storage';
import * as f from '../../test/fixtures';
import { renderWithProviders } from '../../test/render';
import { server } from '../../test/server';
import OverviewPage from '../overview/OverviewPage';
import SchedulesPage from '../schedules/SchedulesPage';
import AgentDetailPage from './AgentDetailPage';
import InstallWizard from './InstallWizard';
import { RunNowButton } from './RunNowButton';

const page = <T,>(items: T[]) => ({ items, nextCursor: null });
const runRoute = { path: '/runs/:runId', element: <p>Run page</p> };
const newRun = { ...f.run, id: '01890000-0000-7000-8000-0000000000aa', state: 'QUEUED' as const };

const notReady: InstallationOut = {
  ...f.installation,
  readiness: {
    ready: false,
    checks: [{ name: 'connection', status: 'needs_attention', detail: 'Google access expired', resource: 'google' }],
  },
};

const schedule: ScheduleOut = {
  id: 'sched-1',
  installationId: f.installation.id,
  agentName: f.installation.agentName,
  ready: true,
  blockers: [],
  cron: '0 10 * * *',
  timezone: 'UTC',
  misfirePolicy: 'fire_once',
  enabled: true,
  nextRunAt: '2026-09-26T10:00:00Z',
  nextOccurrences: [
    { at: '2026-09-26T10:00:00Z', local: '2026-09-26T10:00:00', zoneAbbreviation: 'UTC' },
    { at: '2026-09-27T10:00:00Z', local: '2026-09-27T10:00:00', zoneAbbreviation: 'UTC' },
  ],
  lastFiredAt: null,
  lastRunId: null,
  version: 1,
  createdAt: f.NOW,
  updatedAt: f.NOW,
};

/** Records every POST /runs; answers with a new run. */
function recordRuns(): { installationId: unknown; key: string | null }[] {
  const posts: { installationId: unknown; key: string | null }[] = [];
  server.use(
    http.post('/api/v1/runs', async ({ request }) => {
      const body = (await request.json()) as { installationId: unknown };
      posts.push({ installationId: body.installationId, key: request.headers.get('Idempotency-Key') });
      return HttpResponse.json(newRun, { status: 201 });
    }),
  );
  return posts;
}

describe('RunNowButton', () => {
  it('rapid clicks start one run with one key, then open the run', async () => {
    const posts = recordRuns();
    renderWithProviders(<RunNowButton installation={f.installation} />, { extraRoutes: [runRoute] });
    const button = screen.getByRole('button', { name: 'Run now Caller' });
    await userEvent.dblClick(button);
    await userEvent.click(button).catch(() => undefined);
    expect(await screen.findByText('Run page')).toBeInTheDocument();
    expect(new Set(posts.map((p) => p.key)).size).toBe(1);
    expect(posts[0]).toMatchObject({ installationId: 'inst-1' });
  });
});

describe('Run now on the marketplace agent detail page', () => {
  const detail = { path: '/agents/marketplace/:agentId', route: '/agents/marketplace/caller', extraRoutes: [runRoute] };

  it('shows Run now next to Open when installed and opens the new run', async () => {
    server.use(http.get('/api/v1/catalog/agents/:id', () => HttpResponse.json({ ...f.catalogCaller, installed: true })));
    const posts = recordRuns();
    renderWithProviders(<AgentDetailPage />, detail);
    const run = await screen.findByRole('button', { name: 'Run now Caller' });
    expect(screen.getByRole('link', { name: 'Open in your crew' })).toHaveAttribute('href', '/agents/inst-1');
    expect(screen.queryByRole('link', { name: 'Install agent' })).not.toBeInTheDocument();
    await userEvent.click(run);
    expect(await screen.findByText('Run page')).toBeInTheDocument();
    expect(posts).toHaveLength(1);
  });

  it('is disabled with the readiness reason when the agent is not ready', async () => {
    server.use(
      http.get('/api/v1/catalog/agents/:id', () => HttpResponse.json(f.catalogCaller)),
      http.get('/api/v1/agent-installations', () => HttpResponse.json(page([notReady]))),
    );
    renderWithProviders(<AgentDetailPage />, detail);
    const run = await screen.findByRole('button', { name: 'Run now Caller' });
    expect(run).toBeDisabled();
    expect(run).toHaveAccessibleDescription('Connections: Google access expired');
  });

  it('offers Install agent, not Run now, when the agent is not installed', async () => {
    server.use(
      http.get('/api/v1/catalog/agents/:id', () => HttpResponse.json(f.catalogCaller)),
      http.get('/api/v1/agent-installations', () => HttpResponse.json(page([]))),
    );
    renderWithProviders(<AgentDetailPage />, detail);
    expect(await screen.findByRole('link', { name: 'Install agent' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Run now/ })).not.toBeInTheDocument();
  });
});

describe('Run now on the install wizard finish step', () => {
  function atReview() {
    const approvals = Object.fromEntries(permissionItems(f.callerPermissions).map((i) => [i.id, true]));
    writeDraft('install.caller.0.1.0', {
      step: 'review',
      config: { spreadsheetId: 'sheet-1', maxCalls: 3 },
      bindings: {},
      approvals,
      scheduleOn: false,
      schedule: { preset: 'daily', time: '10:00', weekday: 1, cron: '0 10 * * *', timezone: 'UTC', misfirePolicy: 'fire_once' },
    });
  }

  function serve(installed: InstallationOut) {
    server.use(
      http.get('/api/v1/catalog/agents/:id', () => HttpResponse.json(f.catalogCaller)),
      http.get('/api/v1/provider-profiles', () => HttpResponse.json(page([]))),
      http.post('/api/v1/agent-installations', () => HttpResponse.json(installed, { status: 201 })),
      http.get('/api/v1/agent-installations/:id', () => HttpResponse.json(installed)),
    );
  }

  const wizard = { path: '/agents/marketplace/:agentId/install', route: '/agents/marketplace/caller/install', extraRoutes: [runRoute] };

  it('after installing, Run now is the primary action and opens the run', async () => {
    atReview();
    serve(f.installation);
    const posts = recordRuns();
    renderWithProviders(<InstallWizard />, wizard);
    await userEvent.click(await screen.findByRole('button', { name: 'Install agent' }));
    const heading = await screen.findByRole('heading', { name: 'Caller is in your crew' });
    expect(heading).toHaveFocus();
    expect(screen.getByRole('link', { name: 'Open Caller' })).toHaveAttribute('href', '/agents/inst-1');
    const run = screen.getByRole('button', { name: 'Run now Caller' });
    expect(run).toHaveClass('btn-primary');
    await userEvent.click(run);
    expect(await screen.findByText('Run page')).toBeInTheDocument();
    expect(posts).toEqual([expect.objectContaining({ installationId: 'inst-1' })]);
  });

  it('when the new installation is not ready, Run now is disabled and says why', async () => {
    atReview();
    serve(notReady);
    renderWithProviders(<InstallWizard />, wizard);
    await userEvent.click(await screen.findByRole('button', { name: 'Install agent' }));
    await screen.findByRole('heading', { name: 'Caller is in your crew' });
    const run = screen.getByRole('button', { name: 'Run now Caller' });
    expect(run).toBeDisabled();
    expect(run).toHaveAccessibleDescription('Connections: Google access expired');
    expect(screen.getByRole('list', { name: 'Readiness' })).toBeInTheDocument();
  });
});

describe('Run now on Home', () => {
  it('with one agent, the header action starts it directly; Next up has one Run now per agent', async () => {
    server.use(http.get('/api/v1/schedules', () => HttpResponse.json(page([schedule]))));
    const posts = recordRuns();
    const { container } = renderWithProviders(<OverviewPage />, { extraRoutes: [runRoute] });
    const header = container.querySelector('.page-header') as HTMLElement;
    const run = await within(header).findByRole('button', { name: 'Run now Caller' });
    expect(within(header).queryByRole('link', { name: 'Run a crew member' })).not.toBeInTheDocument();
    const nextUp = (await screen.findByRole('heading', { name: 'Next up' })).closest('.card') as HTMLElement;
    // Two upcoming times of the same schedule, one Run now.
    await waitFor(() => expect(within(nextUp).getAllByRole('listitem')).toHaveLength(2));
    expect(within(nextUp).getAllByRole('button', { name: 'Run now Caller' })).toHaveLength(1);
    await userEvent.click(run);
    expect(await screen.findByText('Run page')).toBeInTheDocument();
    expect(posts).toHaveLength(1);
  });

  it('with several agents, the header keeps Run a crew member; a not-ready row says why', async () => {
    const second: InstallationOut = { ...notReady, id: 'inst-2', agentName: 'Digest' };
    server.use(
      http.get('/api/v1/agent-installations', () => HttpResponse.json(page([f.installation, second]))),
      http.get('/api/v1/schedules', () =>
        HttpResponse.json(page([{ ...schedule, id: 'sched-2', installationId: 'inst-2', agentName: 'Digest', nextOccurrences: schedule.nextOccurrences.slice(0, 1) }])),
      ),
    );
    renderWithProviders(<OverviewPage />);
    expect(await screen.findByRole('link', { name: 'Run a crew member' })).toHaveAttribute('href', '/agents/installed');
    const run = await screen.findByRole('button', { name: 'Run now Digest' });
    expect(run).toBeDisabled();
    expect(run).toHaveAccessibleDescription('Connections: Google access expired');
  });
});

describe('Run now on Schedules', () => {
  it('each schedule row can start its agent now without touching the schedule', async () => {
    let patched = 0;
    server.use(
      http.get('/api/v1/schedules', () => HttpResponse.json(page([schedule]))),
      http.patch('/api/v1/schedules/:id', () => {
        patched += 1;
        return HttpResponse.json(schedule);
      }),
    );
    const posts = recordRuns();
    renderWithProviders(<SchedulesPage />, { extraRoutes: [runRoute] });
    await userEvent.click(await screen.findByRole('button', { name: 'Run now Caller' }));
    expect(await screen.findByText('Run page')).toBeInTheDocument();
    expect(posts).toEqual([expect.objectContaining({ installationId: 'inst-1' })]);
    expect(patched).toBe(0);
  });

  it('is disabled with the reason when the agent is not ready', async () => {
    server.use(
      http.get('/api/v1/schedules', () => HttpResponse.json(page([{ ...schedule, ready: false, blockers: notReady.readiness.checks }]))),
      http.get('/api/v1/agent-installations', () => HttpResponse.json(page([notReady]))),
    );
    renderWithProviders(<SchedulesPage />);
    const run = await screen.findByRole('button', { name: 'Run now Caller' });
    expect(run).toBeDisabled();
    expect(run).toHaveAccessibleDescription('Connections: Google access expired');
  });
});
