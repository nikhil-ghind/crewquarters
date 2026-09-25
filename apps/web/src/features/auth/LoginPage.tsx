import { useQueryClient } from '@tanstack/react-query';
import { Server } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';
import { api, mutate } from '../../api/client';
import { isApiError, remediation } from '../../api/errors';
import { keys, useBootstrapStatus } from '../../api/queries';
import { session } from '../../api/session';
import { Button } from '../../components/Button';
import { Banner } from '../../components/Feedback';
import { Field } from '../../components/Field';
import { useDocumentTitle } from '../../components/Layout';

/** Only same-app paths are accepted as a post-login destination. */
export function safeNext(next: string | null): string {
  if (!next || !next.startsWith('/') || next.startsWith('//') || next.startsWith('/login')) return '/';
  return next;
}

export default function LoginPage() {
  useDocumentTitle('Sign in');
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const expired = params.get('expired') === '1';
  const bootstrap = useBootstrapStatus();

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!username.trim() || !password) {
      setError('Enter your username and password.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const out = await mutate(api.POST('/api/v1/sessions', { body: { username: username.trim(), password } }));
      session.signedIn(out.csrfToken);
      client.setQueryData(keys.me, out);
      await client.invalidateQueries({ queryKey: keys.settings });
      void navigate(safeNext(params.get('next')), { replace: true });
    } catch (e) {
      setPassword('');
      setError(isApiError(e) ? remediation(e) : 'Sign-in failed.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bare-layout">
      <header className="bare-header">
        <span className="brand-mark" aria-hidden="true">
          <Server size={18} />
        </span>
        <span className="brand-name">Crewquarters</span>
      </header>
      <main id="main" className="bare-main">
        <div className="card auth-card stack">
          <div className="stack-sm">
            <h1>Sign in</h1>
            <p className="muted">Sign in as the owner of this Crewquarters device.</p>
          </div>
          {expired ? (
            <Banner tone="info" title="Your session expired">
              Sign in again to continue where you left off.
            </Banner>
          ) : null}
          {error ? (
            <Banner tone="danger" role="alert">
              {error}
            </Banner>
          ) : null}
          <form className="form" onSubmit={(e) => void onSubmit(e)} noValidate>
            <Field label="Username" required>
              <input
                className="input"
                name="username"
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </Field>
            <Field label="Password" required>
              <input
                className="input"
                type="password"
                name="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </Field>
            <Button type="submit" variant="primary" busy={busy} busyLabel="Signing in…" block>
              Sign in
            </Button>
          </form>
          {bootstrap.data?.ownerExists === false ? (
            <p className="muted">
              First time on this device? <Link to="/setup">Set up Crewquarters</Link> with the setup code shown by the
              installer.
            </p>
          ) : null}
        </div>
      </main>
    </div>
  );
}

