import { useQueryClient } from '@tanstack/react-query';
import { Server } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router';
import { api, mutate } from '../../api/client';
import { isApiError, remediation, type FieldError } from '../../api/errors';
import { keys, useBootstrapStatus } from '../../api/queries';
import { session } from '../../api/session';
import { Button } from '../../components/Button';
import { Banner } from '../../components/Feedback';
import { ErrorSummary, Field } from '../../components/Field';
import { useDocumentTitle } from '../../components/Layout';
import { useSaveSetup } from '../setup/setupState';

/** Creates the device's one owner account with the installer's setup code (POST /bootstrap). */
export default function SignUpPage() {
  useDocumentTitle('Create your account');
  const navigate = useNavigate();
  const client = useQueryClient();
  const save = useSaveSetup();
  const bootstrap = useBootstrapStatus();
  const [token, setToken] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [exists, setExists] = useState(false);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const local: FieldError[] = [];
    if (!token.trim()) local.push({ path: '/token', message: 'Enter the setup code shown by the installer.' });
    if (!/^[A-Za-z0-9._@-]{3,64}$/.test(username.trim())) local.push({ path: '/username', message: 'Use 3–64 letters, numbers, dots, dashes or underscores.' });
    if (password.length < 12) local.push({ path: '/password', message: 'Use at least 12 characters.' });
    if (password !== confirm) local.push({ path: '/confirm', message: 'The passwords do not match.' });
    setErrors(local);
    if (local.length > 0) return;
    setBusy(true);
    setError(null);
    try {
      const out = await mutate(
        api.POST('/api/v1/bootstrap', { body: { token: token.trim(), username: username.trim(), password, email: null } }),
      );
      session.signedIn(out.csrfToken);
      client.setQueryData(keys.me, out);
      client.setQueryData(keys.bootstrapStatus, { ownerExists: true });
      await client.refetchQueries({ queryKey: keys.settings });
      await save({ completed: ['welcome', 'preflight', 'owner'], current: 'storage' });
      void navigate('/setup/storage');
    } catch (e) {
      setPassword('');
      setConfirm('');
      if (isApiError(e) && e.code === 'ALREADY_BOOTSTRAPPED') setExists(true);
      else if (isApiError(e) && e.status === 422) setErrors(e.fieldErrors);
      else setError(isApiError(e) ? remediation(e) : 'Could not create the account.');
    } finally {
      setBusy(false);
    }
  };
  const err = (name: string) => errors.find((e) => e.path.endsWith(`/${name}`))?.message;
  const ownerExists = exists || bootstrap.data?.ownerExists === true;

  return (
    <div className="bare-layout">
      <header className="bare-header">
        <span className="brand-mark" aria-hidden="true">
          <Server size={18} />
        </span>
        <span className="brand-name">Crewquarters</span>
      </header>
      <main id="main" className="bare-main auth-split">
        <section className="auth-intro stack" aria-label="About Crewquarters">
          <p className="auth-intro-title">Your AI crew, running on your own device.</p>
          <p>
            One owner account controls this device. You need the setup code the installer printed; it works once.
          </p>
        </section>
        <div className="card auth-card stack">
          <div className="stack-sm">
            <h1>Create your account</h1>
            <p className="muted">Become the owner of this Crewquarters device.</p>
          </div>
          {ownerExists ? (
            <Banner tone="info" title="This device already has an owner" action={<Link to="/login">Sign in</Link>}>
              Only one account can exist. Sign in as the owner instead.
            </Banner>
          ) : (
            <>
              {error ? (
                <Banner tone="danger" role="alert">
                  {error}
                </Banner>
              ) : null}
              <form className="form" onSubmit={(e) => void onSubmit(e)} noValidate>
                <ErrorSummary
                  errors={errors}
                  labels={{ token: 'Setup code', username: 'Username', password: 'Password', confirm: 'Confirm password' }}
                />
                <Field label="Setup code" required error={err('token')} help="Printed by the installer, or run “cq-admin bootstrap-token” on the device.">
                  <input className="input mono" value={token} autoComplete="off" spellCheck={false} onChange={(e) => setToken(e.target.value)} />
                </Field>
                <Field label="Username" required error={err('username')}>
                  <input className="input" value={username} autoComplete="username" onChange={(e) => setUsername(e.target.value)} />
                </Field>
                <Field label="Password" required error={err('password')} help="At least 12 characters. There is no password reset.">
                  <input className="input" type="password" value={password} autoComplete="new-password" onChange={(e) => setPassword(e.target.value)} />
                </Field>
                <Field label="Confirm password" required error={err('confirm')}>
                  <input className="input" type="password" value={confirm} autoComplete="new-password" onChange={(e) => setConfirm(e.target.value)} />
                </Field>
                <Button type="submit" variant="primary" busy={busy} busyLabel="Creating account…" block>
                  Create account
                </Button>
              </form>
              <p className="muted">
                Already have an account? <Link to="/login">Sign in</Link>
              </p>
            </>
          )}
        </div>
      </main>
    </div>
  );
}
