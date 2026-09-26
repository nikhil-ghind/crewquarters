import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { BackupOut, BackupPage } from '../../api/schema';
import { session } from '../../api/session';
import * as f from '../../test/fixtures';
import { renderWithProviders } from '../../test/render';
import { errorEnvelope, server } from '../../test/server';
import LoginPage from '../auth/LoginPage';
import SignUpPage from '../auth/SignUpPage';
import { OwnerStep, ValidationStep } from '../setup/steps';
import BackupsPage from './BackupsPage';
import StatusPage from './StatusPage';

const queued: BackupOut = {
  ...f.backupDevice,
  id: 'crewquarters-backup-20260925T043000Z-0a1b2c',
  status: 'queued',
  source: 'api',
  createdAt: '2026-09-25T04:30:00Z',
  finishedAt: null,
  sizeBytes: null,
  documentCount: null,
  downloadable: false,
};

describe('Backups page', () => {
  it('lists backups with status, source, contents and the last successful backup', async () => {
    renderWithProviders(<BackupsPage />);
    await screen.findByText('Last successful backup');
    expect(screen.getAllByText('crewquarters-backup-20260924T020000Z-nightly').length).toBeGreaterThan(0);
    expect(screen.getByText('pg_dump exited with status 1 (disk full).')).toBeInTheDocument();
    expect(screen.getAllByText('Failed').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Made on the device').length).toBe(2);
    expect(screen.getByText('Made from this page')).toBeInTheDocument();
    expect(screen.getByText('/var/lib/crewquarters/backups')).toBeInTheDocument();
    expect(screen.getByText(/Newest 7 backups kept/)).toBeInTheDocument();
    // Includes/excludes come from the API; backticked commands render as code.
    expect(screen.getByText('Uploaded knowledge documents')).toBeInTheDocument();
    expect(screen.getByText('crewquarters backup create --include-master-key').tagName).toBe('CODE');
    expect(screen.getByText('Backups made here never include the device master key')).toBeInTheDocument();
    expect(screen.getAllByText(/reconnect Google and Twilio/).length).toBeGreaterThan(0);
  });

  it('offers Download only for downloadable backups, as a plain link to the API', async () => {
    renderWithProviders(<BackupsPage />);
    await screen.findByText('Last successful backup');
    const links = screen.getAllByRole('link', { name: /^Download/ });
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute('href', `/api/v1/system/backups/${f.backupDevice.id}/download`);
    expect(links[0]).toHaveAttribute('download');
    expect(screen.getByText(/Contains the master key; copy it on the device/)).toBeInTheDocument();
  });

  it('creates a backup with CSRF and an idempotency key, then refreshes the list', async () => {
    const posts: { csrf: string | null; key: string | null }[] = [];
    let page: BackupPage = f.backupPage;
    server.use(
      http.get('/api/v1/system/backups', () => HttpResponse.json(page)),
      http.post('/api/v1/system/backups', ({ request }) => {
        posts.push({ csrf: request.headers.get('X-CSRF-Token'), key: request.headers.get('Idempotency-Key') });
        page = { ...f.backupPage, items: [queued, ...f.backupPage.items] };
        return HttpResponse.json(queued, { status: 202 });
      }),
    );
    session.signedIn('csrf-backup');
    renderWithProviders(<BackupsPage />);
    await screen.findByText('Last successful backup');
    await userEvent.click(screen.getByRole('button', { name: 'Create backup' }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]?.csrf).toBe('csrf-backup');
    expect(posts[0]?.key).toMatch(/^[0-9a-f-]{36}$/);
    expect(await screen.findByText(queued.id)).toBeInTheDocument();
    expect(screen.getAllByText('Waiting to start').length).toBeGreaterThan(0);
    // While a backup is queued or running, Create is disabled with the reason.
    expect(screen.getByRole('button', { name: 'Create backup' })).toBeDisabled();
    expect(screen.getByText('A backup is already in progress.')).toBeInTheDocument();
  });

  it('shows a 409 in-progress error with remediation', async () => {
    server.use(
      http.post('/api/v1/system/backups', () =>
        errorEnvelope(409, 'BACKUP_IN_PROGRESS', 'A backup is already queued or running.', { backupId: queued.id }),
      ),
    );
    session.signedIn('csrf');
    renderWithProviders(<BackupsPage />);
    await screen.findByText('Last successful backup');
    await userEvent.click(screen.getByRole('button', { name: 'Create backup' }));
    const panel = await screen.findByRole('alert');
    expect(within(panel).getByText('Could not create a backup')).toBeInTheDocument();
    expect(within(panel).getByText(/already queued or running. Wait for it to finish/)).toBeInTheDocument();
    expect(within(panel).getByText(/BACKUP_IN_PROGRESS/)).toBeInTheDocument();
  });

  it('disables Create with a reason when backups are not configured', async () => {
    server.use(http.get('/api/v1/system/backups', () => HttpResponse.json({ ...f.backupPage, enabled: false, location: null, items: [] })));
    renderWithProviders(<BackupsPage />);
    expect(await screen.findByText('Backups are not configured on this device')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create backup' })).toBeDisabled();
    expect(screen.getByText(/Backups are not configured on this device \(CQ_BACKUP_DIR\)\./)).toBeInTheDocument();
    expect(screen.getByText('No backups yet')).toBeInTheDocument();
  });

  it('explains CLI-only restore with the device commands and no restore button', async () => {
    renderWithProviders(<BackupsPage />);
    await screen.findByText('Last successful backup');
    const commands = screen.getByLabelText('Restore commands');
    expect(commands).toHaveTextContent(
      `sudo crewquarters backup restore /var/lib/crewquarters/backups/${f.backupDevice.id}.tar.gz --stop`,
    );
    expect(commands).toHaveTextContent('sudo crewquarters up');
    expect(screen.getByText(/backup of the current state is taken automatically/)).toBeInTheDocument();
    expect(screen.getByText(/Checksums and database schema compatibility are verified/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy commands' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Restore/ })).not.toBeInTheDocument();
  });
});

describe('diagnostics download', () => {
  it('System status links to the server-built bundle instead of building one in the browser', async () => {
    renderWithProviders(<StatusPage />);
    const link = await screen.findByRole('link', { name: 'Download diagnostics' });
    expect(link).toHaveAttribute('href', '/api/v1/system/diagnostics');
    expect(link).toHaveAttribute('download');
    // A plain GET link: nothing is assembled in the page.
    expect(link.tagName).toBe('A');
  });

  it('the setup validation step uses the same endpoint', async () => {
    renderWithProviders(<ValidationStep />);
    const link = await screen.findByRole('link', { name: 'Download diagnostics' });
    expect(link).toHaveAttribute('href', '/api/v1/system/diagnostics');
  });
});

describe('bootstrap status', () => {
  it('the sign-in page offers setup only when no owner exists', async () => {
    server.use(http.get('/api/v1/bootstrap/status', () => HttpResponse.json({ ownerExists: false })));
    renderWithProviders(<LoginPage />, { path: '/login', route: '/login' });
    expect(await screen.findByRole('link', { name: 'Set up Crewquarters' })).toHaveAttribute('href', '/setup');
    expect(screen.getByRole('link', { name: 'Create an account' })).toHaveAttribute('href', '/signup');
  });

  it('the sign-in page hides the setup link once the owner exists', async () => {
    let asked = false;
    server.use(
      http.get('/api/v1/bootstrap/status', () => {
        asked = true;
        return HttpResponse.json({ ownerExists: true });
      }),
    );
    renderWithProviders(<LoginPage />, { path: '/login', route: '/login' });
    await waitFor(() => expect(asked).toBe(true));
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Set up Crewquarters' })).not.toBeInTheDocument();
    expect(screen.queryByText(/First time on this device/)).not.toBeInTheDocument();
    // Sign-up is always reachable; the page itself explains when an owner already exists.
    expect(screen.getByRole('link', { name: 'Create an account' })).toHaveAttribute('href', '/signup');
  });

  it('the setup owner step does not offer a second owner account', async () => {
    renderWithProviders(<OwnerStep signedIn={false} />);
    expect(await screen.findByText('The owner account already exists')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/login?next=/setup');
    expect(screen.queryByLabelText(/Setup code/)).not.toBeInTheDocument();
  });

  it('the setup owner step shows the form on a fresh device', async () => {
    server.use(http.get('/api/v1/bootstrap/status', () => HttpResponse.json({ ownerExists: false })));
    renderWithProviders(<OwnerStep signedIn={false} />);
    expect(await screen.findByLabelText(/Setup code/)).toBeInTheDocument();
  });
});

describe('sign up', () => {
  it('tells visitors the device already has an owner instead of showing the form', async () => {
    renderWithProviders(<SignUpPage />, { path: '/signup', route: '/signup' });
    expect(await screen.findByText('This device already has an owner')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/login');
    expect(screen.queryByLabelText(/Setup code/)).not.toBeInTheDocument();
  });

  it('checks the fields locally before calling the device', async () => {
    let called = false;
    server.use(
      http.get('/api/v1/bootstrap/status', () => HttpResponse.json({ ownerExists: false })),
      http.post('/api/v1/bootstrap', () => {
        called = true;
        return HttpResponse.json(f.session, { status: 201 });
      }),
    );
    renderWithProviders(<SignUpPage />, { path: '/signup', route: '/signup' });
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText(/Username/), 'x');
    await user.type(screen.getByLabelText(/^Password/), 'short');
    await user.click(screen.getByRole('button', { name: 'Create account' }));
    expect(await screen.findAllByText('Enter the setup code shown by the installer.')).not.toHaveLength(0);
    expect(screen.getAllByText('Use at least 12 characters.')).not.toHaveLength(0);
    expect(called).toBe(false);
  });

  it('creates the owner account with the setup code', async () => {
    let body: unknown = null;
    let saved: { setupState?: unknown } | null = null;
    server.use(
      http.get('/api/v1/bootstrap/status', () => HttpResponse.json({ ownerExists: false })),
      http.post('/api/v1/bootstrap', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(f.session, { status: 201 });
      }),
      // The wizard then records the owner step and moves on to storage.
      http.patch('/api/v1/settings', async ({ request }) => {
        saved = (await request.json()) as { setupState?: unknown };
        return HttpResponse.json({ ...f.settings, setupState: saved.setupState as Record<string, unknown> });
      }),
    );
    renderWithProviders(<SignUpPage />, { path: '/signup', route: '/signup' });
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText(/Setup code/), 'setup-code-0123456789');
    await user.type(screen.getByLabelText(/Username/), 'owner');
    await user.type(screen.getByLabelText(/^Password/), 'a-long-demo-password');
    await user.type(screen.getByLabelText(/Confirm password/), 'a-long-demo-password');
    await user.click(screen.getByRole('button', { name: 'Create account' }));
    await waitFor(() =>
      expect(body).toEqual({ token: 'setup-code-0123456789', username: 'owner', password: 'a-long-demo-password', email: null }),
    );
    await waitFor(() =>
      expect(saved?.setupState).toMatchObject({ current: 'storage', completed: ['welcome', 'preflight', 'owner'] }),
    );
  });
});
