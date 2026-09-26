import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowUpRight, Phone, RotateCw, Trash2 } from 'lucide-react';
import { useEffect, useState, type FormEvent } from 'react';
import { isApiError, remediation } from '../../api/errors';
import { useActionGuard } from '../../api/guards';
import { useIntentKey } from '../../api/mutations';
import { endpoints as pendingApi } from '../../api/endpoints';
import type { ConnectionOut, GoogleCapability, ProviderProfileOut } from '../../api/schema';
import { keys, useConnections, useProviderProfiles, useSettings } from '../../api/queries';
import { Button } from '../../components/Button';
import { ConfirmDialog } from '../../components/Dialog';
import { Banner, CopyButton, EmptyState, ErrorPanel } from '../../components/Feedback';
import { ErrorSummary, Field, SecretInput } from '../../components/Field';
import { KeyValue } from '../../components/Layout';
import { StatusBadge } from '../../components/StatusBadge';
import { useFeedback } from '../../components/Toast';
import { formatRelative, formatUtc } from '../../lib/format';
import { writePref } from '../../lib/storage';
import { CONNECTION_STATUS, PROFILE_UNTESTED, PROVIDER_NAMES } from '../../lib/status';
import { affectedList, useAffected } from './affected';

export const GOOGLE_RETURN_KEY = 'google.returnTo';

const GOOGLE_CAPABILITIES: { id: GoogleCapability; label: string; help: string }[] = [
  { id: 'gmail.readonly', label: 'Read Gmail (read-only)', help: 'Used by Daily Gmail Digest. Cannot send or delete mail.' },
  { id: 'gmail.send', label: 'Email alerts to you', help: 'Used by Intruder Watch. Sends only to your own Gmail address. Needs Read Gmail to learn it.' },
  { id: 'spreadsheets', label: 'Google Sheets', help: 'Used by Caller to read contacts and write results to the sheet you configure.' },
];

export function connectionFor(list: ConnectionOut[] | undefined, provider: string): ConnectionOut | undefined {
  return list?.find((c) => c.provider === provider);
}

export function LastChecked({ connection }: { connection: ConnectionOut | undefined }) {
  if (!connection?.lastCheckedAt) return <span className="muted">Never checked</span>;
  return (
    <span className="muted" title={formatUtc(connection.lastCheckedAt)}>
      Last successful check {formatRelative(connection.lastCheckedAt)}
    </span>
  );
}

/** Only follow an authorization URL on https or this same origin. */
export function safeAuthorizationUrl(url: string): string | null {
  try {
    const parsed = new URL(url, window.location.origin);
    if (parsed.origin === window.location.origin) return parsed.toString();
    return parsed.protocol === 'https:' ? parsed.toString() : null;
  } catch {
    return null;
  }
}

// --- Google ----------------------------------------------------------------------------

export function GoogleConnect({ returnTo }: { returnTo: string }) {
  const connections = useConnections();
  const google = connectionFor(connections.data, 'google');
  const guard = useActionGuard();
  const client = useQueryClient();
  const { toast } = useFeedback();
  const affected = useAffected('google');
  const [caps, setCaps] = useState<GoogleCapability[]>(['gmail.readonly', 'spreadsheets']);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);

  const start = useMutation({
    mutationFn: () => pendingApi.googleStart({ capabilities: caps }),
    onSuccess: (out) => {
      const target = safeAuthorizationUrl(out.authorizationUrl);
      if (!target) throw new Error('Unexpected authorization address');
      // Remember where to come back to; the broker returns to /connections/google.
      writePref(GOOGLE_RETURN_KEY, returnTo);
      window.location.assign(target);
    },
  });
  const test = useMutation({
    mutationFn: () => pendingApi.googleTest(),
    onSettled: () => void client.invalidateQueries({ queryKey: keys.connections }),
  });
  const disconnect = useMutation({
    mutationFn: () => pendingApi.googleDisconnect(),
    onSuccess: () => {
      setConfirmDisconnect(false);
      toast('Google disconnected.');
    },
    onSettled: () => void client.invalidateQueries({ queryKey: keys.connections }),
  });

  const status = google?.status ?? 'NOT_CONNECTED';
  const connected = status === 'CONNECTED';
  const needsAttention = status === 'NEEDS_ATTENTION';
  const granted = google?.grantedCapabilities ?? [];

  return (
    <div className="stack">
      <div className="row-between">
        <StatusBadge status={CONNECTION_STATUS[status]} context="Google" />
        <LastChecked connection={google} />
      </div>
      {google?.account ? <p>Account: {google.account}</p> : null}
      {google?.detail ? <p className="muted">{google.detail}</p> : null}
      {needsAttention ? (
        <Banner tone="warning" title="Google access needs to be renewed">
          Reconnect to restore access. Affected: {affectedList(affected).join('; ') || 'no installed agents'}.
        </Banner>
      ) : null}
      {connected || granted.length > 0 ? (
        <div className="stack-sm">
          <h3>Granted access</h3>
          <ul className="stack-sm" style={{ listStyle: 'none' }}>
            {GOOGLE_CAPABILITIES.map((cap) => (
              <li key={cap.id} className="row">
                <StatusBadge
                  status={
                    granted.includes(cap.id)
                      ? { label: 'Granted', tone: 'success', icon: 'check' }
                      : { label: 'Not granted', tone: 'neutral', icon: 'minus' }
                  }
                />
                <span>{cap.label}</span>
              </li>
            ))}
          </ul>
          <p className="muted">Tokens are stored encrypted on this device and are never shown.</p>
        </div>
      ) : null}
      {!connected ? (
        <fieldset className="fieldset">
          <legend>Access to request</legend>
          {GOOGLE_CAPABILITIES.map((cap) => (
            <label key={cap.id} className="check-row">
              <input
                type="checkbox"
                checked={caps.includes(cap.id)}
                onChange={(e) => setCaps(e.target.checked ? [...caps, cap.id] : caps.filter((c) => c !== cap.id))}
              />
              <span>
                <span className="field-label">{cap.label}</span>
                <span className="field-help" style={{ display: 'block' }}>
                  {cap.help}
                </span>
              </span>
            </label>
          ))}
        </fieldset>
      ) : null}
      {start.isError ? <ErrorPanel error={start.error} title="Could not start Google sign-in" /> : null}
      {test.isError ? <ErrorPanel error={test.error} title="Google check failed" /> : null}
      <div className="row">
        {!connected ? (
          <Button
            variant="primary"
            icon={<ArrowUpRight size={16} aria-hidden="true" />}
            busy={start.isPending}
            busyLabel="Opening Google…"
            disabledReason={guard.offline ?? (caps.length === 0 ? 'Choose at least one kind of access.' : null)}
            onClick={() => start.mutate()}
          >
            {needsAttention ? 'Reconnect Google' : 'Connect Google'}
          </Button>
        ) : (
          <>
            <Button icon={<RotateCw size={16} aria-hidden="true" />} busy={test.isPending} busyLabel="Checking…" onClick={() => test.mutate()} disabledReason={guard.offline}>
              Check connection
            </Button>
            <Button variant="tertiary" icon={<Trash2 size={16} aria-hidden="true" />} onClick={() => setConfirmDisconnect(true)} disabledReason={guard.offline}>
              Disconnect Google
            </Button>
          </>
        )}
      </div>
      <p className="muted">
        Google opens in this tab. After you approve, you come back here. In Google’s testing mode, access expires after
        seven days and must be reconnected.
      </p>
      <ConfirmDialog
        open={confirmDisconnect}
        title="Disconnect Google?"
        consequence="Crewquarters revokes its Google access and deletes the stored tokens. Future runs of the agents below will fail their readiness check until you reconnect."
        affected={affectedList(affected)}
        confirmLabel="Disconnect Google"
        busyLabel="Disconnecting…"
        destructive
        busy={disconnect.isPending}
        onConfirm={() => disconnect.mutate()}
        onCancel={() => setConfirmDisconnect(false)}
      >
        {disconnect.isError ? <ErrorPanel error={disconnect.error} title="Could not disconnect" /> : null}
      </ConfirmDialog>
    </div>
  );
}

// --- Twilio -----------------------------------------------------------------------------

const E164 = /^\+[1-9]\d{6,14}$/;

export function TwilioForm() {
  const connections = useConnections();
  const settings = useSettings();
  const twilio = connectionFor(connections.data, 'twilio');
  const saved = twilio !== undefined && twilio.status !== 'NOT_CONNECTED';
  const client = useQueryClient();
  const guard = useActionGuard();
  const { toast, announce } = useFeedback();
  const affected = useAffected('twilio');
  const [accountSid, setAccountSid] = useState('');
  const [authToken, setAuthToken] = useState('');
  const [fromNumber, setFromNumber] = useState('');
  const [replacing, setReplacing] = useState(!saved);
  const [errors, setErrors] = useState<{ path: string; message: string }[]>([]);
  const [callOpen, setCallOpen] = useState(false);
  const [callTo, setCallTo] = useState('');
  const [callKey, resetCallKey] = useIntentKey();
  const [confirmDelete, setConfirmDelete] = useState(false);

  const save = useMutation({
    mutationFn: () => pendingApi.twilioSave({ accountSid: accountSid.trim(), authToken, fromNumber: fromNumber.trim() }),
    onSuccess: () => {
      // Accepted secrets are cleared from the form and never shown again.
      setAuthToken('');
      setAccountSid('');
      setReplacing(false);
      announce('Twilio credentials saved and validated.');
    },
    onError: (e) => {
      setAuthToken('');
      if (isApiError(e)) setErrors(e.fieldErrors);
    },
    onSettled: () => void client.invalidateQueries({ queryKey: keys.connections }),
  });
  const test = useMutation({
    mutationFn: () => pendingApi.twilioTest(),
    onSettled: () => void client.invalidateQueries({ queryKey: keys.connections }),
  });
  const testCall = useMutation({
    mutationFn: () => pendingApi.twilioTestCall({ to: callTo.trim(), confirm: true }, callKey),
    onSuccess: (out) => {
      setCallOpen(false);
      resetCallKey();
      toast(`Test call ${out.placed ? 'placed' : 'requested'} to ${out.to}.`);
    },
  });
  const remove = useMutation({
    mutationFn: () => pendingApi.twilioDelete(),
    onSuccess: () => {
      setConfirmDelete(false);
      setReplacing(true);
      toast('Twilio credentials deleted.');
    },
    onSettled: () => void client.invalidateQueries({ queryKey: keys.connections }),
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const local: { path: string; message: string }[] = [];
    if (!/^AC[0-9a-fA-F]{32}$/.test(accountSid.trim())) local.push({ path: '/accountSid', message: 'Account SID starts with AC followed by 32 hexadecimal characters.' });
    if (authToken.length < 8) local.push({ path: '/authToken', message: 'Enter the auth token from the Twilio console.' });
    if (!E164.test(fromNumber.trim())) local.push({ path: '/fromNumber', message: 'Use international format, for example +15551234567.' });
    setErrors(local);
    if (local.length === 0) save.mutate();
  };
  const err = (name: string) => errors.find((e) => e.path.endsWith(name))?.message;
  const callbackBase = settings.data?.callbackUrls?.twilioCallbackBase ?? settings.data?.callbackBaseUrl ?? null;

  return (
    <div className="stack">
      <div className="row-between">
        <StatusBadge status={CONNECTION_STATUS[twilio?.status ?? 'NOT_CONNECTED']} context="Twilio" />
        <LastChecked connection={twilio} />
      </div>
      {twilio?.detail ? <p className="muted">{twilio.detail}</p> : null}
      <KeyValue
        items={[
          [
            'Callback address',
            callbackBase ? (
              <span className="row">
                <span className="mono break-anywhere">{callbackBase}</span>
                <CopyButton text={callbackBase} label="Copy" />
              </span>
            ) : (
              'Not configured by the installer'
            ),
          ],
        ]}
      />
      {replacing ? (
        <form className="form" onSubmit={onSubmit} noValidate>
          <ErrorSummary errors={errors} labels={{ accountSid: 'Account SID', authToken: 'Auth token', fromNumber: 'Caller number' }} />
          <Field label="Account SID" required error={err('accountSid')}>
            <input className="input mono" value={accountSid} autoComplete="off" spellCheck={false} onChange={(e) => setAccountSid(e.target.value)} />
          </Field>
          <Field label="Auth token" required error={err('authToken')} help="Stored encrypted. It is never shown again after saving.">
            <SecretInput saved={false} replacing value={authToken} onChange={setAuthToken} onReplace={() => undefined} name="authToken" />
          </Field>
          <Field label="Caller number" required error={err('fromNumber')} help="A Twilio number you own, in international format.">
            <input className="input mono" value={fromNumber} inputMode="tel" placeholder="+15551234567" onChange={(e) => setFromNumber(e.target.value)} />
          </Field>
          {save.isError && !isApiError(save.error) ? <ErrorPanel error={save.error} title="Could not save Twilio" /> : null}
          {save.isError && isApiError(save.error) && save.error.fieldErrors.length === 0 ? (
            <ErrorPanel error={save.error} title="Twilio did not accept these credentials" />
          ) : null}
          <div className="row">
            <Button type="submit" variant="primary" busy={save.isPending} busyLabel="Saving and validating…" disabledReason={guard.offline}>
              {saved ? 'Replace credentials' : 'Save and validate'}
            </Button>
            {saved ? (
              <Button variant="tertiary" onClick={() => setReplacing(false)}>
                Keep current credentials
              </Button>
            ) : null}
          </div>
        </form>
      ) : (
        <div className="stack-sm">
          <Field label="Auth token">
            <SecretInput saved replacing={false} value="" onChange={() => undefined} onReplace={() => setReplacing(true)} name="authToken" />
          </Field>
        </div>
      )}
      {test.isError ? <ErrorPanel error={test.error} title="Twilio check failed" /> : null}
      {saved ? (
        <div className="row">
          <Button icon={<RotateCw size={16} aria-hidden="true" />} busy={test.isPending} busyLabel="Checking…" onClick={() => test.mutate()} disabledReason={guard.offline}>
            Test connection
          </Button>
          <Button icon={<Phone size={16} aria-hidden="true" />} onClick={() => setCallOpen(true)} disabledReason={guard.offline}>
            Place a test call…
          </Button>
          <Button variant="tertiary" icon={<Trash2 size={16} aria-hidden="true" />} onClick={() => setConfirmDelete(true)} disabledReason={guard.offline}>
            Delete credentials
          </Button>
        </div>
      ) : null}
      <p className="muted">Test connection checks the credentials without placing a call.</p>
      <ConfirmDialog
        open={callOpen}
        title="Place a live test call?"
        consequence={
          <>
            Twilio will call the number below now and play a fixed test message. This is a real phone call billed to your
            Twilio account. Only call a verified number whose owner agreed to receive it.
          </>
        }
        confirmLabel="Place test call"
        busyLabel="Calling…"
        busy={testCall.isPending}
        confirmDisabledReason={E164.test(callTo.trim()) ? null : 'Enter the number in international format first.'}
        onConfirm={() => testCall.mutate()}
        onCancel={() => {
          setCallOpen(false);
          testCall.reset();
        }}
      >
        <Field label="Number to call" required help="International format, for example +15551234567.">
          <input className="input mono" inputMode="tel" value={callTo} onChange={(e) => setCallTo(e.target.value)} />
        </Field>
        {testCall.isError ? (
          <Banner tone="danger" role="alert" title="No call was placed">
            {isApiError(testCall.error) && testCall.error.code === 'RATE_LIMITED'
              ? `Test calls are limited to one a minute. Try again in ${typeof testCall.error.details.retryAfterSeconds === 'number' ? testCall.error.details.retryAfterSeconds : 60} seconds.`
              : isApiError(testCall.error)
                ? remediation(testCall.error)
                : 'The call could not be requested.'}
          </Banner>
        ) : null}
      </ConfirmDialog>
      <ConfirmDialog
        open={confirmDelete}
        title="Delete Twilio credentials?"
        consequence="The saved account SID and auth token are deleted from this device. Agents that place calls will fail readiness until new credentials are saved."
        affected={affectedList(affected)}
        confirmLabel="Delete credentials"
        destructive
        busy={remove.isPending}
        onConfirm={() => remove.mutate()}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
}

// --- OpenAI / Anthropic -----------------------------------------------------------------

/** Profile status comes from the model gateway's key test: CONNECTED, ERROR or UNTESTED. */
export function profileStatus(p: ProviderProfileOut) {
  if (!p.enabled) return CONNECTION_STATUS.DISABLED;
  if (p.status === 'ERROR') return CONNECTION_STATUS.NEEDS_ATTENTION;
  if (p.status === 'CONNECTED') return CONNECTION_STATUS.CONNECTED;
  if (p.status === 'UNTESTED') return PROFILE_UNTESTED;
  const known = Object.keys(CONNECTION_STATUS).includes(p.status);
  return known ? CONNECTION_STATUS[p.status as keyof typeof CONNECTION_STATUS] : CONNECTION_STATUS.UNKNOWN;
}

export function ProviderKeyForm({ provider }: { provider: 'openai' | 'anthropic' }) {
  const name = PROVIDER_NAMES[provider] ?? provider;
  const profiles = useProviderProfiles();
  const mine = (profiles.data ?? []).filter((p) => p.provider === provider);
  const client = useQueryClient();
  const guard = useActionGuard();
  const { toast } = useFeedback();
  const affected = useAffected(provider);
  const [displayName, setDisplayName] = useState(`${name} key`);
  // Names are unique per provider; suggest a free one once the existing keys load.
  const taken = mine.map((p) => p.displayName.trim().toLowerCase()).join('|');
  useEffect(() => {
    const names = new Set(taken ? taken.split('|') : []);
    setDisplayName((current) => {
      if (!names.has(current.trim().toLowerCase())) return current;
      let i = 2;
      while (names.has(`${name} key ${i}`.toLowerCase())) i += 1;
      return `${name} key ${i}`;
    });
  }, [taken, name]);
  const [apiKey, setApiKey] = useState('');
  const [models, setModels] = useState('');
  const [dailyTokens, setDailyTokens] = useState('');
  const [enabled, setEnabled] = useState(true);
  const [errors, setErrors] = useState<{ path: string; message: string }[]>([]);
  const [toDelete, setToDelete] = useState<ProviderProfileOut | null>(null);
  const [testResult, setTestResult] = useState<Record<string, string>>({});

  const create = useMutation({
    mutationFn: () =>
      pendingApi.providerProfileCreate({
        provider,
        displayName: displayName.trim(),
        apiKey,
        allowedModels: models
          .split(/[\n,]/)
          .map((m) => m.trim())
          .filter(Boolean),
        budgets: dailyTokens ? { dailyTokens: Number(dailyTokens) } : {},
        enabled,
      }),
    onSuccess: () => {
      setApiKey('');
      toast(`${name} key saved.`);
    },
    onError: () => setApiKey(''),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: keys.providerProfiles });
      void client.invalidateQueries({ queryKey: keys.connections });
    },
  });
  const test = useMutation({
    mutationFn: (id: string) => pendingApi.providerProfileTest(id),
    onSuccess: (out, id) => {
      setTestResult((r) => ({ ...r, [id]: out.status === 'CONNECTED' ? 'Test succeeded' : `Test failed${out.detail ? `: ${out.detail}` : ''}` }));
      // The Connections summary derives the provider status from the tested profiles.
      void client.invalidateQueries({ queryKey: keys.connections });
    },
    onError: (e, id) => setTestResult((r) => ({ ...r, [id]: isApiError(e) ? remediation(e) : 'Test failed' })),
    onSettled: () => void client.invalidateQueries({ queryKey: keys.providerProfiles }),
  });
  const remove = useMutation({
    mutationFn: (id: string) => pendingApi.providerProfileDelete(id),
    onSuccess: () => {
      setToDelete(null);
      toast(`${name} key deleted.`);
    },
    onSettled: () => {
      void client.invalidateQueries({ queryKey: keys.providerProfiles });
      void client.invalidateQueries({ queryKey: keys.connections });
    },
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const local: { path: string; message: string }[] = [];
    if (!displayName.trim()) local.push({ path: '/displayName', message: 'Give this key a name.' });
    else if (mine.some((p) => p.displayName.trim().toLowerCase() === displayName.trim().toLowerCase()))
      local.push({ path: '/displayName', message: 'A key with this name already exists. Choose another name.' });
    if (apiKey.length < 8) local.push({ path: '/apiKey', message: `Paste the API key from your ${name} account.` });
    if (dailyTokens && (!/^\d+$/.test(dailyTokens) || Number(dailyTokens) <= 0)) local.push({ path: '/dailyTokens', message: 'Enter a whole number of tokens, or leave it empty.' });
    setErrors(local);
    if (local.length === 0) create.mutate();
  };
  const err = (field: string) => errors.find((e) => e.path.endsWith(field))?.message;

  return (
    <div className="stack">
      <Banner tone="info" role="none" title={`Cloud · ${name}`}>
        Requests that use this key send prompts and included data off this device to {name}. It is never used as an
        automatic fallback for local models; an agent must be approved for {name} explicitly.
      </Banner>
      {profiles.isError ? <ErrorPanel error={profiles.error} title={`Could not load ${name} keys`} onRetry={() => void profiles.refetch()} /> : null}
      {mine.length === 0 && profiles.isSuccess ? (
        <EmptyState title={`No ${name} key yet`}>Add a key below to allow approved agents to use {name}.</EmptyState>
      ) : null}
      {mine.map((p) => (
        <div key={p.id} className="card card-compact stack-sm">
          <div className="row-between">
            <span className="field-label">{p.displayName}</span>
            <StatusBadge status={profileStatus(p)} />
          </div>
          <span className="muted">
            Models: {p.allowedModels.length > 0 ? p.allowedModels.join(', ') : 'provider default'} · Key: saved, hidden
            {typeof p.budgets.dailyTokens === 'number' ? ` · Daily token guard ${p.budgets.dailyTokens}` : ''}
          </span>
          {testResult[p.id] ? <p role="status">{testResult[p.id]}</p> : null}
          <div className="row">
            <Button busy={test.isPending && test.variables === p.id} busyLabel="Testing…" onClick={() => test.mutate(p.id)} disabledReason={guard.offline}>
              Send test request
            </Button>
            <Button variant="tertiary" onClick={() => setToDelete(p)} disabledReason={guard.offline}>
              Delete key
            </Button>
          </div>
          <p className="muted" style={{ fontSize: 12 }}>
            The test sends one very small request to {name}; that test content leaves the device.
          </p>
        </div>
      ))}
      <form className="form" onSubmit={onSubmit} noValidate>
        <h3>Add a {name} key</h3>
        <ErrorSummary errors={errors} />
        <Field label="Name" required error={err('displayName')}>
          <input className="input" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        </Field>
        <Field label="API key" required error={err('apiKey')} help="Stored encrypted. It is never shown again after saving.">
          <SecretInput saved={false} replacing value={apiKey} onChange={setApiKey} onReplace={() => undefined} name="apiKey" />
        </Field>
        <Field label="Allowed models" help="One per line or comma-separated. Leave empty for the provider default.">
          <textarea className="textarea" rows={2} value={models} onChange={(e) => setModels(e.target.value)} />
        </Field>
        <Field label="Daily token guard" help="Optional. Requests stop for the day after this many tokens." error={err('dailyTokens')}>
          <input className="input" inputMode="numeric" value={dailyTokens} onChange={(e) => setDailyTokens(e.target.value)} />
        </Field>
        <label className="check-row">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          <span>Enabled</span>
        </label>
        {create.isError ? <ErrorPanel error={create.error} title={`Could not save the ${name} key`} /> : null}
        <div className="row">
          <Button type="submit" variant="primary" busy={create.isPending} busyLabel="Saving…" disabledReason={guard.offline}>
            Save {name} key
          </Button>
        </div>
      </form>
      <ConfirmDialog
        open={toDelete !== null}
        title={`Delete ${toDelete?.displayName ?? 'this key'}?`}
        consequence={`Agents approved for ${name} will fail readiness until another key is added.`}
        affected={affectedList(affected)}
        confirmLabel="Delete key"
        destructive
        busy={remove.isPending}
        onConfirm={() => toDelete && remove.mutate(toDelete.id)}
        onCancel={() => setToDelete(null)}
      />
    </div>
  );
}
