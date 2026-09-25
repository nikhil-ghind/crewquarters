import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, RotateCw } from 'lucide-react';
import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';
import { api, mutate, unwrap } from '../../api/client';
import { DIAGNOSTICS_URL } from '../../api/endpoints';
import { isApiError, type FieldError } from '../../api/errors';
import { useActionGuard } from '../../api/guards';
import { useIntentKey, useModelAction } from '../../api/mutations';
import {
  keys,
  useBootstrapStatus,
  useCatalog,
  useConnections,
  useInstallations,
  useModels,
  useSettings,
  useSystemStatus,
} from '../../api/queries';
import { session } from '../../api/session';
import { useModelEvents } from '../../api/streams';
import { Button, DownloadLink } from '../../components/Button';
import { Banner, CopyButton, ErrorPanel, SkeletonBlock } from '../../components/Feedback';
import { ErrorSummary, Field } from '../../components/Field';
import { Advanced, Card, KeyValue } from '../../components/Layout';
import { StatusBadge } from '../../components/StatusBadge';
import { useFeedback } from '../../components/Toast';
import { formatBytes } from '../../lib/format';
import { CONNECTION_STATUS, MODEL_DOWNLOAD_STATUS, READINESS_STATUS } from '../../lib/status';
import { QuickInstall } from '../agents/QuickInstall';
import { CheckList, fromStatusCheck, type CheckRow } from '../common/CheckList';
import { licenseText, pickRecommended } from '../models/modelInfo';
import { ModelProgress } from '../models/ModelProgress';
import { CallbackUrls } from '../connections/CallbackUrls';
import { googleErrorText } from '../connections/googleErrors';
import { connectionFor, GoogleConnect, ProviderKeyForm, TwilioForm } from '../connections/forms';
import { nextStep, readSetupState, useSaveSetup, type SetupStepId } from './setupState';

interface StepFrameProps {
  step: SetupStepId;
  title: string;
  purpose: string;
  children: ReactNode;
  /** Primary action; defaults to Continue. */
  continueLabel?: string;
  continueDisabledReason?: string | null;
  onContinue?: () => Promise<void> | void;
  busy?: boolean;
  /** Optional steps: where to finish later. */
  skipTo?: string;
  hideContinue?: boolean;
}

function StepFrame({
  step,
  title,
  purpose,
  children,
  continueLabel = 'Continue',
  continueDisabledReason,
  onContinue,
  busy,
  skipTo,
  hideContinue,
}: StepFrameProps) {
  const navigate = useNavigate();
  const save = useSaveSetup();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const signedIn = session.get().status === 'authenticated';
  const next = nextStep(step);

  const advance = async (mode: 'complete' | 'skip') => {
    setSaving(true);
    setError(null);
    try {
      if (mode === 'complete' && onContinue) await onContinue();
      if (signedIn) {
        await save(
          mode === 'complete'
            ? { completed: [step], current: next }
            : { skipped: [step], current: next },
        );
      }
      void navigate(`/setup/${next}`);
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  };

  useEffect(() => {
    document.getElementById('setup-step-title')?.focus();
  }, [step]);

  return (
    <section className="stack-lg" aria-labelledby="setup-step-title">
      <header className="stack-sm">
        <h1 id="setup-step-title" tabIndex={-1}>
          {title}
        </h1>
        <p className="page-purpose">{purpose}</p>
      </header>
      {children}
      {error ? <ErrorPanel error={error} title="Could not save this step" /> : null}
      <div className="row sticky-action">
        {step !== 'welcome' ? (
          <Button variant="tertiary" onClick={() => navigate(-1)}>
            Back
          </Button>
        ) : null}
        {skipTo ? (
          <Button onClick={() => void advance('skip')} disabled={saving}>
            Skip for now
          </Button>
        ) : null}
        {!hideContinue ? (
          <Button
            variant="primary"
            busy={saving || busy}
            busyLabel="Saving…"
            disabledReason={continueDisabledReason}
            onClick={() => void advance('complete')}
          >
            {continueLabel}
          </Button>
        ) : null}
      </div>
      {skipTo ? <p className="muted">If you skip, you can finish this later in {skipTo}.</p> : null}
    </section>
  );
}

// --- Welcome ------------------------------------------------------------------------------

export function WelcomeStep() {
  const health = useQuery({
    queryKey: keys.health,
    queryFn: () => unwrap(api.GET('/api/v1/health/ready')),
    retry: false,
  });
  return (
    <StepFrame step="welcome" title="Welcome to Crewquarters" purpose="Set up this device to run your crew of agents and local AI models.">
      <Card>
        <div className="stack long-form">
          <p>
            Crewquarters runs agents and AI models on this device. Your documents, mail and conversations stay here unless you
            explicitly approve a cloud provider for a specific agent.
          </p>
          <ul style={{ marginLeft: 20 }}>
            <li>Check this device and create the owner account.</li>
            <li>Install a local model and connect the services your agents need.</li>
            <li>Add the demo agents and confirm everything works.</li>
          </ul>
          <p className="muted">Setup saves your progress as you go, so you can close this page and continue later.</p>
        </div>
      </Card>
      <KeyValue
        items={[
          ['Platform', health.data?.status === 'ok' ? 'Services are responding' : health.isPending ? 'Checking…' : 'Not responding yet'],
          ['Licenses', 'Open-source notices ship with the installed package (Help › Licenses on the device).'],
        ]}
      />
    </StepFrame>
  );
}

// --- Preflight ------------------------------------------------------------------------------

export function PreflightStep({ signedIn }: { signedIn: boolean }) {
  const health = useQuery({
    queryKey: keys.health,
    queryFn: async () => {
      // /health/ready answers 503 with the same body when something is not ready.
      const result = await api.GET('/api/v1/health/ready');
      if (result.data) return result.data;
      const body = result.error as unknown;
      if (body && typeof body === 'object' && 'status' in body) return body as { status: 'ok' | 'unavailable'; checks?: Record<string, string> };
      throw new Error('Readiness check failed');
    },
    retry: false,
  });
  const status = useSystemStatus({ enabled: signedIn });
  const settings = useSettings({ enabled: signedIn });
  const save = useSaveSetup();
  const state = readSetupState(settings.data);
  const [accepted, setAccepted] = useState<string[]>([]);

  const rows: CheckRow[] = useMemo(() => {
    if (signedIn && status.data) return status.data.checks.map(fromStatusCheck);
    if (health.isPending) {
      return [{ id: 'database', name: 'Database', state: 'checking', explanation: 'Checking the database…' }];
    }
    const checks = health.data?.checks ?? {};
    const out: CheckRow[] = Object.entries(checks).map(([name, value]) => ({
      id: name,
      name: name === 'database' ? 'Database' : name === 'migrations' ? 'Database migrations' : name,
      state: value.startsWith('ok') ? 'passed' : 'failed',
      explanation: value,
      technical: `${name}: ${value}`,
    }));
    if (out.length === 0 && health.isError) {
      out.push({ id: 'api', name: 'Platform services', state: 'failed', explanation: 'The control service is not responding.' });
    }
    return out;
  }, [signedIn, status.data, health.isPending, health.data, health.isError]);

  const warnings = rows.filter((r) => r.state === 'warning');
  const failed = rows.filter((r) => r.state === 'failed');
  const allAccepted = warnings.every((w) => accepted.includes(w.id) || state.acceptedWarnings.includes(w.id));
  const checking = rows.some((r) => r.state === 'checking') || (signedIn && status.isPending);

  return (
    <StepFrame
      step="preflight"
      title="System preflight"
      purpose="Crewquarters checks this device before installing anything."
      continueDisabledReason={
        checking
          ? 'Wait for the checks to finish.'
          : failed.length > 0
            ? 'Fix the failed checks, then select Retry check.'
            : !allAccepted
              ? 'Review and accept each warning to continue.'
              : null
      }
      onContinue={async () => {
        if (signedIn && accepted.length > 0) await save({ acceptedWarnings: accepted });
      }}
    >
      {!signedIn ? (
        <Banner tone="info" title="More checks after the owner account">
          Architecture, GPU, NVIDIA runtime and disk checks need the owner account; they run again automatically before setup
          finishes.
        </Banner>
      ) : null}
      <CheckList rows={rows} label="Preflight checks" />
      {warnings.length > 0 ? (
        <fieldset className="fieldset">
          <legend>Accept warnings</legend>
          {warnings.map((w) => (
            <label key={w.id} className="check-row">
              <input
                type="checkbox"
                checked={accepted.includes(w.id) || state.acceptedWarnings.includes(w.id)}
                disabled={state.acceptedWarnings.includes(w.id)}
                onChange={(e) => setAccepted(e.target.checked ? [...accepted, w.id] : accepted.filter((a) => a !== w.id))}
              />
              <span>
                I understand: {w.name} — {w.explanation}
              </span>
            </label>
          ))}
        </fieldset>
      ) : null}
      {failed.length > 0 ? (
        <div className="stack-sm">
          <span className="diag-code">
            Diagnostic code: PREFLIGHT_FAILED · {failed.map((f) => f.id).join(', ')}
          </span>
          <div className="row">
            <Button
              icon={<RotateCw size={16} aria-hidden="true" />}
              onClick={() => {
                void health.refetch();
                if (signedIn) void status.refetch();
              }}
            >
              Retry check
            </Button>
            <CopyButton text={`PREFLIGHT_FAILED ${failed.map((f) => `${f.id}=${f.explanation}`).join('; ')}`} label="Copy diagnostic code" />
          </div>
        </div>
      ) : null}
    </StepFrame>
  );
}

// --- Owner -----------------------------------------------------------------------------------

export function OwnerStep({ signedIn }: { signedIn: boolean }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const save = useSaveSetup();
  const { announce } = useFeedback();
  const [token, setToken] = useState('');
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [acknowledged, setAcknowledged] = useState(false);
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [exists, setExists] = useState(false);
  const bootstrap = useBootstrapStatus({ enabled: !signedIn });

  if (signedIn) {
    return (
      <StepFrame step="owner" title="Owner account" purpose="The owner account controls this device.">
        <Banner tone="success" title="Owner account created">
          You are signed in as the owner.
        </Banner>
      </StepFrame>
    );
  }

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const local: FieldError[] = [];
    if (!token.trim()) local.push({ path: '/token', message: 'Enter the setup code shown by the installer.' });
    if (!/^[A-Za-z0-9._-]{3,64}$/.test(username.trim())) local.push({ path: '/username', message: 'Use 3–64 letters, numbers, dots, dashes or underscores.' });
    if (email && !/^[^@\s]+@[^@\s]+$/.test(email)) local.push({ path: '/email', message: 'Enter a valid email address or leave it empty.' });
    if (password.length < 12) local.push({ path: '/password', message: 'Use at least 12 characters.' });
    if (password !== confirm) local.push({ path: '/confirm', message: 'The passwords do not match.' });
    if (!acknowledged) local.push({ path: '/acknowledged', message: 'Confirm that you have stored the password safely.' });
    setErrors(local);
    if (local.length > 0) return;
    setBusy(true);
    setError(null);
    try {
      const out = await mutate(
        api.POST('/api/v1/bootstrap', {
          body: { token: token.trim(), username: username.trim(), password, email: email || null },
        }),
      );
      session.signedIn(out.csrfToken);
      client.setQueryData(keys.me, out);
      client.setQueryData(keys.bootstrapStatus, { ownerExists: true });
      await client.refetchQueries({ queryKey: keys.settings });
      await save({ completed: ['welcome', 'preflight', 'owner'], current: 'storage' });
      announce('Owner account created.');
      void navigate('/setup/storage');
    } catch (e) {
      setPassword('');
      setConfirm('');
      if (isApiError(e) && e.code === 'ALREADY_BOOTSTRAPPED') setExists(true);
      else if (isApiError(e) && e.status === 422) setErrors(e.fieldErrors);
      else setError(e);
    } finally {
      setBusy(false);
    }
  };
  const err = (name: string) => errors.find((e) => e.path.endsWith(`/${name}`))?.message;

  if (exists || bootstrap.data?.ownerExists) {
    // The one owner already exists: never offer a second create form.
    return (
      <section className="stack-lg" aria-labelledby="setup-step-title">
        <header className="stack-sm">
          <h1 id="setup-step-title" tabIndex={-1}>
            Owner account
          </h1>
          <p className="page-purpose">The owner account controls this device. There is only one owner.</p>
        </header>
        <Banner tone="info" title="The owner account already exists" action={<Link to="/login?next=/setup">Sign in</Link>}>
          Sign in as the owner to continue setup.
        </Banner>
      </section>
    );
  }

  return (
    <section className="stack-lg" aria-labelledby="setup-step-title">
      <header className="stack-sm">
        <h1 id="setup-step-title" tabIndex={-1}>
          Owner account
        </h1>
        <p className="page-purpose">Create the account that controls this device. There is only one owner.</p>
      </header>
      {error ? <ErrorPanel error={error} title="Could not create the owner account" /> : null}
      <form className="form" onSubmit={(e) => void onSubmit(e)} noValidate>
        <ErrorSummary
          errors={errors}
          labels={{ token: 'Setup code', username: 'Username', email: 'Email', password: 'Password', confirm: 'Confirm password', acknowledged: 'Password recovery' }}
        />
        <Field label="Setup code" required error={err('token')} help="Printed by the installer, or run “cq-admin bootstrap-token” on the device. It works once.">
          <input className="input mono" value={token} autoComplete="off" spellCheck={false} onChange={(e) => setToken(e.target.value)} />
        </Field>
        <Field label="Username" required error={err('username')}>
          <input className="input" value={username} autoComplete="username" onChange={(e) => setUsername(e.target.value)} />
        </Field>
        <Field label="Email" error={err('email')} help="Optional. Used only to identify you on this device.">
          <input className="input" type="email" value={email} autoComplete="email" onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Password" required error={err('password')} help="At least 12 characters.">
          <input className="input" type="password" value={password} autoComplete="new-password" onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Field label="Confirm password" required error={err('confirm')}>
          <input className="input" type="password" value={confirm} autoComplete="new-password" onChange={(e) => setConfirm(e.target.value)} />
        </Field>
        <Banner tone="warning" role="none" title="There is no password reset">
          If you lose this password, recovering the device needs console access. Store it in a password manager.
        </Banner>
        <label className="check-row">
          <input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} aria-invalid={err('acknowledged') ? true : undefined} />
          <span>I have stored this password safely.</span>
        </label>
        <div className="row sticky-action">
          <Button variant="tertiary" onClick={() => navigate('/setup/preflight')}>
            Back
          </Button>
          <Button type="submit" variant="primary" busy={busy} busyLabel="Creating account…">
            Create owner account
          </Button>
        </div>
      </form>
    </section>
  );
}

// --- Storage and network ---------------------------------------------------------------------------

export function StorageStep() {
  const status = useSystemStatus();
  const settings = useSettings();
  const state = readSetupState(settings.data);
  const save = useSaveSetup();
  const [exposure, setExposure] = useState<'local' | 'lan'>(state.exposure ?? 'local');
  const disk = status.data?.checks.find((c) => c.group === 'storage');
  const models = useModels();
  const recommended = pickRecommended(models.data);
  return (
    <StepFrame
      step="storage"
      title="Storage and network"
      purpose="Confirm where data is kept and who can reach this device."
      continueDisabledReason={disk?.status === 'failed' ? 'Free up disk space first; the data path is not writable or full.' : null}
      onContinue={() => save({ exposure })}
    >
      <Card title="Storage">
        <KeyValue
          items={[
            ['Data path', 'The platform data directory configured by the installer (database, documents, models).'],
            ['Free space', disk ? disk.detail : 'Checking…'],
            [
              'Needed for the recommended model',
              recommended?.diskBytes ? `${formatBytes(recommended.diskBytes)} for ${recommended.displayName}` : 'Shown in the next steps',
            ],
          ]}
        />
      </Card>
      <fieldset className="fieldset">
        <legend>Who can open Crewquarters</legend>
        <label className="check-row">
          <input type="radio" name="exposure" checked={exposure === 'local'} onChange={() => setExposure('local')} />
          <span>
            <span className="field-label">Only this device</span>
            <span className="field-help" style={{ display: 'block' }}>
              Safest. Open Crewquarters from the device’s own browser.
            </span>
          </span>
        </label>
        <label className="check-row">
          <input type="radio" name="exposure" checked={exposure === 'lan'} onChange={() => setExposure('lan')} />
          <span>
            <span className="field-label">Devices on my local network</span>
            <span className="field-help" style={{ display: 'block' }}>
              Other computers on your network can sign in. The installer must have enabled LAN access with HTTPS.
            </span>
          </span>
        </label>
      </fieldset>
      <Card title="Callbacks from Google and Twilio">
        <p className="muted">
          Google sign-in and Twilio call updates reach this device through a fixed public address. Only those callback routes
          are exposed; the rest of Crewquarters stays private.
        </p>
        <CallbackUrls />
      </Card>
    </StepFrame>
  );
}


// --- Platform services --------------------------------------------------------------------------

export function ServicesStep() {
  const status = useSystemStatus({ refetchInterval: 5_000 });
  const rows = (status.data?.checks ?? []).map(fromStatusCheck);
  const failed = rows.filter((r) => r.state === 'failed');
  return (
    <StepFrame
      step="services"
      title="Platform services"
      purpose="The database, agent runtime and model serving must be healthy before continuing."
      continueDisabledReason={status.isPending ? 'Waiting for the health checks.' : failed.length > 0 ? 'Some core services are not ready yet.' : null}
    >
      {status.isError ? <ErrorPanel error={status.error} title="Could not read service health" onRetry={() => void status.refetch()} /> : null}
      {status.isPending ? <SkeletonBlock label="Checking services" /> : <CheckList rows={rows} label="Service health" />}
      {failed.length > 0 ? (
        <Button icon={<RotateCw size={16} aria-hidden="true" />} onClick={() => void status.refetch()}>
          Retry check
        </Button>
      ) : null}
    </StepFrame>
  );
}

// --- Local model ---------------------------------------------------------------------------------------


export function ModelStep() {
  const models = useModels();
  const model = pickRecommended(models.data);
  const action = useModelAction();
  const [key, resetKey] = useIntentKey();
  const guard = useActionGuard();
  const save = useSaveSetup();
  useModelEvents(model?.id, model?.downloadState === 'DOWNLOADING');
  const installed = model?.downloadState === 'INSTALLED';

  return (
    <StepFrame
      step="model"
      title="Local model"
      purpose="Download a model to this device. Downloading does not use memory; the model loads only when chat or an agent needs it."
      skipTo="Models"
      continueDisabledReason={installed ? null : 'Install the model, or skip for now.'}
      onContinue={() => (model ? save({ modelId: model.id }) : undefined)}
    >
      {models.isPending ? <SkeletonBlock /> : null}
      {models.isError ? <ErrorPanel error={models.error} title="Could not load models" onRetry={() => void models.refetch()} /> : null}
      {model ? (
        <Card title={model.displayName} subtitle="Recommended for this device">
          <div className="stack">
            <div className="row">
              <StatusBadge status={MODEL_DOWNLOAD_STATUS[model.downloadState]} context="Disk" />
            </div>
            <KeyValue
              items={[
                ['Download size', formatBytes(model.diskBytes)],
                ['Memory when loaded', model.expectedMemoryBytes ? `About ${formatBytes(model.expectedMemoryBytes)}` : 'Measured on first load'],
                ['Context', model.contextLimit ? `${model.contextLimit.toLocaleString()} tokens` : '—'],
                ['License', licenseText(model)],
                ['Validated on this hardware', model.validation ?? 'Not yet measured'],
              ]}
            />
            <ModelProgress model={model} />
            {action.isError ? <ErrorPanel error={action.error} title="The download could not start" /> : null}
            {model.downloadState === 'NOT_INSTALLED' || model.downloadState === 'DOWNLOAD_ERROR' ? (
              <Button
                variant="secondary"
                icon={<Download size={16} aria-hidden="true" />}
                busy={action.isPending}
                busyLabel="Starting download…"
                disabledReason={guard.runtime}
                onClick={() => action.mutate({ modelId: model.id, action: 'install', key }, { onSuccess: resetKey })}
              >
                {model.downloadState === 'DOWNLOAD_ERROR' ? 'Retry download' : 'Install model'}
              </Button>
            ) : null}
            <p className="muted">You can leave this page; the download continues and appears on the Models page.</p>
          </div>
        </Card>
      ) : models.isSuccess ? (
        <Banner tone="warning" title="No model is available for this device">
          The model catalog is empty. Continue and add a model later from Models.
        </Banner>
      ) : null}
    </StepFrame>
  );
}


// --- Connections ---------------------------------------------------------------------------------------

export function ConnectionsStep() {
  const [params] = useSearchParams();
  const result = params.get('result');
  const code = params.get('code');
  const connections = useConnections();
  const client = useQueryClient();
  const google = connectionFor(connections.data, 'google');
  useEffect(() => {
    if (result) void client.invalidateQueries({ queryKey: keys.connections });
  }, [result, client]);
  return (
    <StepFrame
      step="connections"
      title="Connections"
      purpose="Connect the services your agents need. Secrets are stored encrypted and never shown again."
      skipTo="Connections"
      continueDisabledReason={google?.status === 'CONNECTED' ? null : 'Connect Google for the demo agents, or skip for now.'}
    >
      {result === 'connected' ? (
        <Banner tone="success" title="Google connected">
          The granted access is listed below.
        </Banner>
      ) : null}
      {result === 'error' ? (
        <Banner tone="danger" role="alert" title="Google sign-in did not finish">
          {googleErrorText(code)} <span className="diag-code">Diagnostic code: {code ?? 'OAUTH_ERROR'}</span>
        </Banner>
      ) : null}
      <Card title="Google" subtitle="Required by Daily Gmail Digest and Caller">
        <GoogleConnect returnTo="/setup/connections" />
      </Card>
      <Advanced label="Optional: Twilio, OpenAI and Anthropic">
        <Card title="Twilio" subtitle="Needed by Caller to place calls" headingLevel={3}>
          <TwilioForm />
        </Card>
        <Card title="OpenAI" subtitle="Optional cloud models" headingLevel={3}>
          <ProviderKeyForm provider="openai" />
        </Card>
        <Card title="Anthropic" subtitle="Optional cloud models" headingLevel={3}>
          <ProviderKeyForm provider="anthropic" />
        </Card>
      </Advanced>
    </StepFrame>
  );
}


// --- Demo agents ---------------------------------------------------------------------------------------

export function AgentsStep() {
  const catalog = useCatalog();
  const installations = useInstallations();
  const demo = (catalog.data ?? []).filter((a) => a.agentId === 'daily-gmail-digest' || a.agentId === 'caller');
  const shown = demo.length > 0 ? demo : (catalog.data ?? []);
  return (
    <StepFrame
      step="agents"
      title="Demo agents"
      purpose="Add the Caller and Daily Gmail Digest agents to your crew. You review every permission before anything is installed."
      skipTo="Crew › Marketplace"
    >
      {catalog.isPending ? <SkeletonBlock /> : null}
      {catalog.isError ? <ErrorPanel error={catalog.error} title="Could not load the agent catalog" onRetry={() => void catalog.refetch()} /> : null}
      {shown.map((agent) => (
        <QuickInstall
          key={agent.agentId}
          agent={agent}
          installation={installations.data?.find((i) => i.agentId === agent.agentId)}
        />
      ))}
    </StepFrame>
  );
}

// --- Validation -----------------------------------------------------------------------------------------

export function ValidationStep() {
  const navigate = useNavigate();
  const status = useSystemStatus({ refetchInterval: 10_000 });
  const models = useModels();
  const connections = useConnections();
  const installations = useInstallations();
  const settings = useSettings();
  const state = readSetupState(settings.data);
  const save = useSaveSetup();
  const [finishing, setFinishing] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const serviceRows = (status.data?.checks ?? []).map(fromStatusCheck);
  const model = pickRecommended(models.data);
  const modelSkipped = state.skipped.includes('model');
  const modelRow: CheckRow = {
    id: 'model',
    name: 'Local model',
    state: models.isPending ? 'checking' : model?.downloadState === 'INSTALLED' ? 'passed' : modelSkipped ? 'warning' : 'failed',
    explanation:
      model?.downloadState === 'INSTALLED'
        ? `${model.displayName} is installed on disk and loads when needed.`
        : modelSkipped
          ? 'Skipped. Install a model later from Models before running agents that need one.'
          : 'No model is installed. Go back to Local model or skip it explicitly.',
  };
  const connectionRows: CheckRow[] = (connections.data ?? []).map((c) => ({
    id: `connection:${c.provider}`,
    name: c.displayName,
    state: c.status === 'CONNECTED' ? 'passed' : c.status === 'NEEDS_ATTENTION' || c.status === 'UNKNOWN' ? 'warning' : 'passed',
    explanation: c.status === 'NOT_CONNECTED' ? 'Not connected (optional).' : CONNECTION_STATUS[c.status].label,
  }));
  const agentRows: CheckRow[] = (installations.data ?? []).map((i) => ({
    id: `agent:${i.id}`,
    name: i.agentName,
    state: i.readiness.ready ? 'passed' : 'warning',
    explanation: i.readiness.ready
      ? 'Ready to run.'
      : i.readiness.checks
          .filter((c) => c.status !== 'ok')
          .map((c) => `${READINESS_STATUS[c.status].label}: ${c.detail}`)
          .join('; '),
  }));
  const rows = [...serviceRows, modelRow, ...connectionRows, ...agentRows];
  const blocking = serviceRows.some((r) => r.state === 'failed') || modelRow.state === 'failed';
  const checking = status.isPending || models.isPending;

  const finish = async () => {
    setFinishing(true);
    setError(null);
    try {
      await status.refetch();
      await save({ completed: ['validation'], current: 'validation', setupCompleted: true });
      void navigate('/', { replace: true });
    } catch (e) {
      setError(e);
    } finally {
      setFinishing(false);
    }
  };

  return (
    <section className="stack-lg" aria-labelledby="setup-step-title">
      <header className="stack-sm">
        <h1 id="setup-step-title" tabIndex={-1}>
          Validation and finish
        </h1>
        <p className="page-purpose">Crewquarters checks everything end to end before you start.</p>
      </header>
      {checking ? <SkeletonBlock label="Running checks" /> : <CheckList rows={rows} label="Validation checks" />}
      {blocking ? (
        <Banner tone="danger" role="alert" title="Setup is not finished yet">
          Resolve the failed checks above. Nothing is marked complete until core services and the model choice are usable.
        </Banner>
      ) : null}
      {error ? <ErrorPanel error={error} title="Could not finish setup" /> : null}
      <div className="row sticky-action">
        <Button variant="tertiary" onClick={() => navigate('/setup/agents')}>
          Back
        </Button>
        <DownloadLink href={DIAGNOSTICS_URL} icon={<Download size={16} aria-hidden="true" />}>
          Download diagnostics
        </DownloadLink>
        <Button
          variant="primary"
          busy={finishing}
          busyLabel="Finishing…"
          disabledReason={checking ? 'Wait for the checks to finish.' : blocking ? 'Resolve the failed checks first.' : null}
          onClick={() => void finish()}
        >
          Finish setup
        </Button>
      </div>
    </section>
  );
}
