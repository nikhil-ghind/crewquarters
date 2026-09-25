/**
 * Component gallery (the Storybook-equivalent required by the Person 4 brief): every
 * library component in ready, loading, empty, degraded, error and disabled states,
 * using static fixtures only — this page never calls the API.
 */
import { Inbox } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { ApiError } from '../../api/errors';
import type { StoredCitation } from '../../lib/knowledge';
import { Button } from '../../components/Button';
import { DataTable, type Column } from '../../components/DataTable';
import { ConfirmDialog } from '../../components/Dialog';
import { Banner, EmptyState, ErrorPanel, Skeleton, SkeletonBlock, SkeletonTable } from '../../components/Feedback';
import { ErrorSummary, Field, SecretInput } from '../../components/Field';
import { Advanced, Card, KeyValue, PageHeader } from '../../components/Layout';
import { LocalityChip } from '../../components/LocalityChip';
import { LogViewer, type LogEntry } from '../../components/LogViewer';
import { Progress, ResourceMeter } from '../../components/Meters';
import { PermissionList } from '../../components/PermissionRow';
import { SchemaForm } from '../../components/SchemaForm';
import { SourceDrawer } from '../../components/SourceDrawer';
import { StatusBadge } from '../../components/StatusBadge';
import { Stepper, Timeline } from '../../components/Stepper';
import { useFeedback } from '../../components/Toast';
import type { JsonSchema } from '../../lib/jsonSchema';
import { permissionItems } from '../../lib/permissions';
import {
  CALL_STATUS,
  CHECK_STATUS,
  CONNECTION_STATUS,
  DEVICE_STATUS,
  DOCUMENT_STATUS,
  INPUT_STATUS,
  MODEL_DOWNLOAD_STATUS,
  MODEL_MEMORY_STATUS,
  READINESS_STATUS,
  RUN_STATUS,
  type StatusSpec,
} from '../../lib/status';
import { CheckList } from '../common/CheckList';
import { parsePreview, PreviewBlocks } from '../common/PreviewBlocks';
import { CallerResult, parseCaller } from '../activity/results/CallerResult';
import { GmailDigestResult, parseDigest } from '../activity/results/GmailDigestResult';

function State({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="gallery-state">
      <span className="gallery-state-label">{label}</span>
      {children}
    </div>
  );
}

function Vocabulary({ title, map }: { title: string; map: Record<string, StatusSpec> }) {
  return (
    <State label={title}>
      <div className="row">
        {Object.entries(map).map(([k, v]) => (
          <StatusBadge key={k} status={v} />
        ))}
      </div>
    </State>
  );
}

const SAMPLE_SCHEMA: JsonSchema = {
  type: 'object',
  required: ['timezone', 'spreadsheetId'],
  properties: {
    timezone: { type: 'string', description: 'IANA timezone that defines “yesterday”.', 'x-crewquarters-widget': 'timezone' },
    spreadsheetId: { type: 'string', minLength: 1, 'x-crewquarters-widget': 'spreadsheet' },
    maxMessages: { type: 'integer', minimum: 1, maximum: 500, default: 200 },
    excludeCategories: { type: 'array', items: { enum: ['CATEGORY_PROMOTIONS', 'CATEGORY_SOCIAL'] }, default: ['CATEGORY_PROMOTIONS'] },
    script: { type: 'string', maxLength: 1000, 'x-crewquarters-widget': 'textarea' },
    batchSize: { type: 'integer', minimum: 1, maximum: 25, default: 10, 'x-crewquarters-group': 'Advanced' },
  },
};

const PERMISSIONS = {
  llmProfiles: ['local.general'],
  knowledge: [],
  connectors: { google: ['spreadsheets'], twilio: ['call.fixed_script'] },
  cloudProviders: ['openai'],
  userInput: true,
};

interface Row {
  id: string;
  name: string;
  state: keyof typeof RUN_STATUS;
  started: string;
}
const ROWS: Row[] = [
  { id: '1', name: 'Daily Gmail Digest', state: 'SUCCEEDED', started: '10:00' },
  { id: '2', name: 'Caller', state: 'WAITING_INPUT', started: '10:12' },
  { id: '3', name: 'Hello Crew', state: 'FAILED', started: '09:55' },
];
const COLUMNS: Column<Row>[] = [
  { key: 'name', header: 'Run', cell: (r) => <a href="#table">{r.name}</a>, sortValue: (r) => r.name, primary: true },
  { key: 'state', header: 'Status', cell: (r) => <StatusBadge status={RUN_STATUS[r.state]} /> },
  { key: 'started', header: 'Started', cell: (r) => r.started, sortValue: (r) => r.started },
];

const LOGS: LogEntry[] = [
  { id: 1, time: '2026-09-25T04:30:00Z', level: 'info', message: 'Fetched 42 messages' },
  { id: 2, time: '2026-09-25T04:30:02Z', level: 'warning', message: 'Batch 3 retried <b>not bold</b>' },
  { id: 3, time: '2026-09-25T04:30:05Z', level: 'error', message: 'Model timeout; retrying' },
];

const PREVIEW = parsePreview({
  blocks: [
    { type: 'table', columns: ['Row', 'Name', 'Number', 'Consent'], rows: [['2', 'Asha', '••••21', 'validated'], ['3', 'Ben', '••••57', 'validated']] },
    { type: 'text', text: 'Disclosure: This is an automated demonstration call.\nScript: Hello {name}.' },
    { type: 'keyValue', items: [{ label: 'Recipients', value: '2' }, { label: 'Call cap', value: '3' }] },
  ],
  choices: [],
  consequence: '2 automated calls will be placed now.',
});

const DIGEST = parseDigest({
  date: '2026-09-24',
  timezone: 'Asia/Kolkata',
  processedCount: 200,
  truncated: true,
  counts: { urgent: 1, important: 1, lowPriority: 0, needsReview: 1 },
  groups: {
    urgent: [{ messageId: 'm1', threadId: 't1', from: 'Ops <ops@example.com>', subject: 'Server down <img src=x onerror=alert(1)>', receivedAt: '2026-09-24T03:10:00Z', reason: 'Production outage', nextAction: 'Reply to acknowledge', needsReview: false, gmailLink: 'https://mail.google.com/mail/u/0/#inbox/m1' }],
    important: [{ messageId: 'm2', threadId: 't2', from: 'Finance', subject: 'Invoice', receivedAt: null, reason: 'Payment due', nextAction: 'Pay by Friday', needsReview: true, gmailLink: 'javascript:alert(1)' }],
    lowPriority: [],
  },
  model: { profile: 'local.general.small', provider: 'local', locality: 'local' },
});

const CALLER = parseCaller({
  operatorDecision: 'approved',
  summary: { called: 2, answered: 1, responsesCaptured: 1, skipped: 1, failed: 0 },
  rows: [
    { row: 2, name: 'Asha', phoneMasked: '••••21', consent: 'validated', callStatus: 'answered_speech', transcript: 'Yes, count me in', sheetWrite: 'written', completedAt: '2026-09-25T05:00:00Z' },
    { row: 3, name: 'Ben', phoneMasked: '••••57', consent: 'validated', callStatus: 'no_answer', transcript: null, sheetWrite: 'pending_retry', completedAt: '2026-09-25T05:02:00Z' },
    { row: 4, name: 'Chen', phoneMasked: '••••90', consent: 'skipped', skipReason: 'Consent column is not "yes"' },
  ],
});

const CITATION: StoredCitation = {
  index: 1,
  citationId: 'c1',
  text: 'Refunds are issued within 14 days. <script>alert("x")</script>',
  score: 0.82,
  document: { id: 'd1', name: 'policy.md' },
  locator: { section: 'Refunds' },
  location: 'Refunds (line 4)',
  knowledgeBaseId: 'kb1',
};

export default function GalleryPage() {
  const [config, setConfig] = useState<Record<string, unknown>>({ timezone: 'Asia/Kolkata', maxMessages: 200 });
  const [approvals, setApprovals] = useState<Record<string, boolean>>({});
  const [dialog, setDialog] = useState(false);
  const [secret, setSecret] = useState('');
  const [replacing, setReplacing] = useState(false);
  const { toast } = useFeedback();
  const items = permissionItems(PERMISSIONS);
  const apiError = new ApiError(503, 'MODEL_GATEWAY_UNAVAILABLE', 'Model gateway stream failed.', 'req-123', { upstream: 'gateway' });

  return (
    <div className="bare-layout">
      <main id="main" className="page">
        <PageHeader title="Component gallery" purpose="Every component in ready, loading, empty, degraded, error and disabled states. Static fixtures only." />

        <Card title="Status vocabulary">
          <div className="stack">
            <Vocabulary title="Runs" map={RUN_STATUS} />
            <Vocabulary title="Model on disk" map={MODEL_DOWNLOAD_STATUS} />
            <Vocabulary title="Model in memory" map={MODEL_MEMORY_STATUS} />
            <Vocabulary title="Connections" map={CONNECTION_STATUS} />
            <Vocabulary title="Device" map={DEVICE_STATUS} />
            <Vocabulary title="Checks" map={CHECK_STATUS} />
            <Vocabulary title="Crew Requests" map={INPUT_STATUS} />
            <Vocabulary title="Readiness" map={READINESS_STATUS} />
            <Vocabulary title="Documents" map={DOCUMENT_STATUS} />
            <Vocabulary title="Calls" map={CALL_STATUS} />
            <State label="Locality chips">
              <div className="row">
                <LocalityChip provider="local" />
                <LocalityChip provider="local" long />
                <LocalityChip provider="openai" />
                <LocalityChip provider="anthropic" />
              </div>
            </State>
          </div>
        </Card>

        <Card title="Buttons">
          <div className="grid-3">
            <State label="Ready">
              <div className="row">
                <Button variant="primary">Install agent</Button>
                <Button>Test connection</Button>
                <Button variant="tertiary">View details</Button>
              </div>
            </State>
            <State label="Loading">
              <Button variant="primary" busy busyLabel="Submitting…">
                Approve 3 calls
              </Button>
            </State>
            <State label="Disabled (with reason)">
              <Button variant="primary" disabledReason="Install a local model first.">
                Enable local chat
              </Button>
            </State>
          </div>
        </Card>

        <Card title="Resource meter and progress">
          <div className="grid-3">
            <State label="Ready (safe)">
              <ResourceMeter label="Unified memory" value={40} max={128} valueText="40.0 GiB of 128.0 GiB used" warnAt={0.85} dangerAt={0.95} />
            </State>
            <State label="Degraded (warning threshold)">
              <ResourceMeter label="Unified memory" value={112} max={128} valueText="112.0 GiB of 128.0 GiB used" warnAt={0.85} dangerAt={0.95} />
            </State>
            <State label="Error (danger threshold)">
              <ResourceMeter label="Disk" value={126} max={128} valueText="126 GiB of 128 GiB used" warnAt={0.85} dangerAt={0.95} />
            </State>
            <State label="Determinate (real percentage)">
              <Progress label="Downloading model" percent={42} stage="2.0 GiB of 4.7 GiB" detail="Current file: model-00001.safetensors" />
            </State>
            <State label="Stage + elapsed (no percentage)">
              <Progress label="Loading model" stage="Loading weights" since={new Date(Date.now() - 95_000).toISOString()} />
            </State>
            <State label="Loading">
              <Skeleton height={8} />
            </State>
          </div>
        </Card>

        <Card title="Data table" subtitle="Below 768 px it becomes a card list.">
          <div className="stack">
            <State label="Ready">
              <DataTable caption="Sample runs" columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} />
            </State>
            <State label="Loading">
              <SkeletonTable rows={2} />
            </State>
            <State label="Empty">
              <DataTable caption="No runs" columns={COLUMNS} rows={[]} rowKey={(r) => r.id} empty={<EmptyState icon={Inbox} title="No runs yet">Start a run to see it here.</EmptyState>} />
            </State>
            <State label="Error">
              <ErrorPanel error={apiError} title="Could not load runs" onRetry={() => undefined} />
            </State>
          </div>
        </Card>

        <div className="grid-2">
          <Card title="Stepper">
            <Stepper
              label="Sample steps"
              compactOnMobile={false}
              steps={[
                { id: 'a', label: 'Welcome', state: 'completed' },
                { id: 'b', label: 'System preflight', state: 'current' },
                { id: 'c', label: 'Local model', state: 'optional', meta: 'Optional' },
                { id: 'd', label: 'Connections', state: 'blocked' },
                { id: 'e', label: 'Validation', state: 'upcoming' },
              ]}
            />
          </Card>
          <Card title="Timeline">
            <Timeline
              label="Sample timeline"
              entries={[
                { key: 1, title: 'Preparing agent', time: '10:00:01', tone: 'info', icon: 'spinner' },
                { key: 2, title: 'Loading local model', detail: 'local.general.small', time: '10:00:04', tone: 'info', icon: 'cpu' },
                { key: 3, title: 'Asked: Approve 2 calls', time: '10:02:10', tone: 'warning', icon: 'hand', current: true },
              ]}
            />
          </Card>
        </div>

        <Card title="JSON Schema form">
          <div className="grid-2">
            <State label="Ready">
              <SchemaForm schema={SAMPLE_SCHEMA} value={config} onChange={setConfig} idPrefix="g1" options={{ timezones: ['UTC', 'Asia/Kolkata', 'America/New_York'] }} />
            </State>
            <State label="Error (validation)">
              <ErrorSummary errors={[{ path: '/spreadsheetId', message: 'Spreadsheet ID is required.' }]} />
              <SchemaForm schema={SAMPLE_SCHEMA} value={{}} onChange={() => undefined} idPrefix="g2" errors={[{ path: '/spreadsheetId', message: 'Spreadsheet ID is required.' }]} />
            </State>
            <State label="Disabled">
              <SchemaForm schema={SAMPLE_SCHEMA} value={config} onChange={() => undefined} idPrefix="g3" disabled />
            </State>
            <State label="Secret field (write-only)">
              <Field label="Auth token" help="Stored encrypted; never shown again.">
                <SecretInput saved={!replacing} replacing={replacing} value={secret} onChange={setSecret} onReplace={() => setReplacing(true)} name="token" />
              </Field>
            </State>
          </div>
        </Card>

        <Card title="Permission rows">
          <div className="grid-2">
            <State label="Approval (cloud and phone emphasized)">
              <PermissionList items={items} approvals={approvals} onApprove={(id, v) => setApprovals((a) => ({ ...a, [id]: v }))} changedIds={new Set(['cloud.openai'])} />
            </State>
            <State label="Read-only (granted)">
              <PermissionList items={items.slice(0, 2)} />
            </State>
            <State label="Disabled">
              <PermissionList items={items.slice(0, 1)} approvals={{}} onApprove={() => undefined} disabled />
            </State>
            <State label="Empty">
              <PermissionList items={[]} />
            </State>
          </div>
        </Card>

        <Card title="Dialogs, toasts and banners">
          <div className="stack">
            <div className="row">
              <Button onClick={() => setDialog(true)}>Open confirmation dialog</Button>
              <Button onClick={() => toast('Schedule turned off.', { label: 'Undo', onClick: () => toast('Schedule restored.') })}>Show toast with Undo</Button>
            </div>
            <Banner tone="danger" title="Crewquarters is not responding">Showing the last information loaded.</Banner>
            <Banner tone="warning" title="Live updates paused—reconnecting">Nothing is lost or started again.</Banner>
            <Banner tone="info" title="Chat is off until you enable it">Enabling loads the model.</Banner>
            <Banner tone="success" title="Google connected">Granted access listed below.</Banner>
          </div>
          <ConfirmDialog
            open={dialog}
            title="Place a live test call?"
            consequence="Twilio will call the number now. This is a real call."
            affected={['Agent: Caller']}
            confirmLabel="Place test call"
            onConfirm={() => setDialog(false)}
            onCancel={() => setDialog(false)}
          />
        </Card>

        <div className="grid-2">
          <Card title="Error panel">
            <ErrorPanel error={apiError} title="Chat could not start" onRetry={() => undefined} />
          </Card>
          <Card title="Empty state and skeleton">
            <div className="stack">
              <EmptyState icon={Inbox} title="Nothing needs you" action={<Button>Open Activity</Button>}>
                Questions from agents appear here.
              </EmptyState>
              <SkeletonBlock lines={2} />
            </div>
          </Card>
        </div>

        <Card title="Log viewer">
          <div className="grid-2">
            <State label="Ready (escaped text)">
              <LogViewer entries={LOGS} label="Sample logs" timeZone="UTC" />
            </State>
            <State label="Empty">
              <LogViewer entries={[]} label="Empty logs" />
            </State>
          </div>
        </Card>

        <Card title="Source drawer">
          <div className="grid-2">
            <State label="Ready (escaped passage)">
              <div style={{ display: 'flex', minHeight: 260 }}>
                <SourceDrawer citation={CITATION} onClose={() => undefined} />
              </div>
            </State>
            <State label="Degraded (document deleted)">
              <div style={{ display: 'flex', minHeight: 260 }}>
                <SourceDrawer citation={{ ...CITATION, documentAvailable: false }} onClose={() => undefined} />
              </div>
            </State>
          </div>
        </Card>

        <Card title="Crew Request preview and check list">
          <div className="grid-2">
            <State label="Preview blocks">
              <PreviewBlocks blocks={PREVIEW.blocks} caption="Sample preview" />
            </State>
            <State label="Check list">
              <CheckList
                label="Sample checks"
                rows={[
                  { id: 'a', name: 'CPU architecture', state: 'passed', explanation: 'linux/arm64' },
                  { id: 'b', name: 'NVIDIA container runtime', state: 'warning', explanation: 'Not checked with the fake runtime' },
                  { id: 'c', name: 'Free disk space', state: 'failed', explanation: '3 GiB free of 512 GiB', remediation: 'Free up disk space.' },
                  { id: 'd', name: 'Database', state: 'checking', explanation: 'Checking…' },
                ]}
              />
            </State>
          </div>
        </Card>

        <Card title="Result renderers">
          <div className="stack">
            <State label="Gmail digest (truncated, needs review, unsafe link dropped, HTML escaped)">{DIGEST ? <GmailDigestResult digest={DIGEST} /> : null}</State>
            <State label="Caller">{CALLER ? <CallerResult data={CALLER} timeZone="Asia/Kolkata" /> : null}</State>
          </div>
        </Card>

        <Advanced label="Key/value and advanced disclosure">
          <KeyValue items={[['Image digest', <span key="d" className="mono">sha256:0123…</span>], ['SDK protocol', '1']]} />
        </Advanced>
      </main>
    </div>
  );
}
