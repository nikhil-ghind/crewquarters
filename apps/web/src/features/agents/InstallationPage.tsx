import { useEffect, useMemo, useState } from 'react';
import { useBlocker, useNavigate, useParams } from 'react-router';
import { isApiError, type FieldError } from '../../api/errors';
import { useActionGuard } from '../../api/guards';
import {
  useCreateSchedule,
  useDeleteSchedule,
  useIntentKey,
  usePatchInstallation,
  usePatchSchedule,
  useUninstall,
} from '../../api/mutations';
import { useCatalogAgent, useCatalogVersion, useInstallation, useRuns, useSchedules } from '../../api/queries';
import type { InstallationOut, ScheduleOut } from '../../api/schema';
import { Button } from '../../components/Button';
import { ConfirmDialog } from '../../components/Dialog';
import { Banner, EmptyState, ErrorPanel, SkeletonTable } from '../../components/Feedback';
import { ErrorSummary } from '../../components/Field';
import { Advanced, Card, KeyValue, Page, PageHeader, RouteTabs } from '../../components/Layout';
import { PermissionList } from '../../components/PermissionRow';
import { QueryView } from '../../components/QueryView';
import { SchemaForm } from '../../components/SchemaForm';
import { StatusBadge } from '../../components/StatusBadge';
import { useFeedback } from '../../components/Toast';
import { formatDateTime, formatUtc } from '../../lib/format';
import { asSchema, fieldNameFromPath, validate } from '../../lib/jsonSchema';
import { addedPermissionIds, permissionItems } from '../../lib/permissions';
import { SCHEDULE_STATUS } from '../../lib/status';
import { RunsTable } from '../common/RunsTable';
import { useTimeZone } from '../common/useTimeZone';
import { draftFromCron, cronFor, newDraft, ScheduleEditor, summary, type ScheduleDraft } from '../schedules/ScheduleEditor';
import { installationStatus, ReadinessList } from './AgentBits';
import { resourcesText } from './AgentDetailPage';
import { RunNowButton } from './RunNowButton';
import { useFormOptions } from './useFormOptions';

const TABS = ['overview', 'runs', 'schedule', 'configuration', 'permissions', 'advanced'] as const;
type Tab = (typeof TABS)[number];
const TAB_LABELS: Record<Tab, string> = {
  overview: 'Overview',
  runs: 'Runs',
  schedule: 'Schedule',
  configuration: 'Configuration',
  permissions: 'Permissions',
  advanced: 'Advanced',
};

export default function InstallationPage() {
  const { installationId = '', tab } = useParams();
  const installation = useInstallation(installationId);
  const current: Tab = (TABS as readonly string[]).includes(tab ?? '') ? (tab as Tab) : 'overview';
  return (
    <Page>
      <QueryView query={installation} errorTitle="Could not load this agent">
        {(inst) => <InstallationView inst={inst} tab={current} />}
      </QueryView>
    </Page>
  );
}

function InstallationView({ inst, tab }: { inst: InstallationOut; tab: Tab }) {
  const base = `/agents/${encodeURIComponent(inst.id)}`;
  const patch = usePatchInstallation(inst.id);
  const [key, resetKey] = useIntentKey();
  const guard = useActionGuard();
  const { toast } = useFeedback();
  return (
    <>
      <PageHeader
        title={inst.agentName}
        documentTitle={inst.agentName}
        purpose={`Version ${inst.agentVersion}`}
        breadcrumbs={[{ label: 'Crew', to: '/agents/installed' }, { label: inst.agentName }]}
        status={
          <>
            <StatusBadge status={installationStatus(inst)} context="Readiness" />
            <label className="check-row" style={{ padding: 0, minHeight: 32 }}>
              <input
                type="checkbox"
                checked={inst.enabled}
                disabled={patch.isPending || !!guard.offline}
                onChange={(e) =>
                  patch.mutate(
                    { key, body: { version: inst.version, enabled: e.target.checked } },
                    {
                      onSuccess: (out) => {
                        resetKey();
                        toast(out.enabled ? `${inst.agentName} enabled.` : `${inst.agentName} disabled.`);
                      },
                    },
                  )
                }
              />
              <span>Enabled</span>
            </label>
          </>
        }
        actions={<RunNowButton installation={inst} />}
      />
      {patch.isError ? <ErrorPanel error={patch.error} title="Could not change this agent" /> : null}
      {inst.needsReapproval ? (
        <Banner
          tone="warning"
          title="New version needs your approval"
          action={
            <Button variant="secondary" onClick={() => document.getElementById('reapprove')?.scrollIntoView()}>
              Review permissions
            </Button>
          }
        >
          This agent cannot run until you review and approve its changed permissions.
        </Banner>
      ) : null}
      <RouteTabs
        label={`${inst.agentName} sections`}
        tabs={TABS.map((t) => ({ to: t === 'overview' ? base : `${base}/${t}`, label: TAB_LABELS[t], end: true }))}
      />
      {tab === 'overview' ? <OverviewTab inst={inst} /> : null}
      {tab === 'runs' ? <RunsTab inst={inst} /> : null}
      {tab === 'schedule' ? <ScheduleTab inst={inst} /> : null}
      {tab === 'configuration' ? <ConfigurationTab inst={inst} /> : null}
      {tab === 'permissions' ? <PermissionsTab inst={inst} /> : null}
      {tab === 'advanced' ? <AdvancedTab inst={inst} /> : null}
    </>
  );
}

function OverviewTab({ inst }: { inst: InstallationOut }) {
  const catalog = useCatalogAgent(inst.agentId);
  const schedules = useSchedules();
  const runs = useRuns({ installationId: inst.id, limit: 1 });
  const timeZone = useTimeZone();
  const mine = (schedules.data ?? []).filter((s) => s.installationId === inst.id);
  const last = runs.data?.items[0];
  const configEntries = Object.entries(inst.config).slice(0, 8);
  return (
    <div className="grid-2">
      <Card title="Readiness">
        <ReadinessList installation={inst} />
      </Card>
      <Card title="About">
        <div className="stack-sm">
          <p>{catalog.data?.summary ?? ''}</p>
          <KeyValue
            items={[
              [
                'Next scheduled run',
                mine[0]?.nextRunAt ? (
                  <span key="n" title={formatUtc(mine[0].nextRunAt)}>
                    {formatDateTime(mine[0].nextRunAt, timeZone)}
                  </span>
                ) : (
                  'Not scheduled'
                ),
              ],
              ['Last run', last ? `${formatDateTime(last.createdAt, timeZone)}` : 'Never run'],
            ]}
          />
        </div>
      </Card>
      <Card title="Configuration summary">
        {configEntries.length === 0 ? (
          <p className="muted">Default configuration.</p>
        ) : (
          <KeyValue items={configEntries.map(([k, v]) => [k, <span key={k} className="break-anywhere">{Array.isArray(v) ? v.join(', ') : String(v)}</span>])} />
        )}
      </Card>
    </div>
  );
}

function RunsTab({ inst }: { inst: InstallationOut }) {
  const runs = useRuns({ installationId: inst.id, limit: 50 });
  const timeZone = useTimeZone();
  return (
    <QueryView
      query={runs}
      errorTitle="Could not load runs"
      loading={<SkeletonTable />}
      isEmpty={(d) => d.items.length === 0}
      empty={<EmptyState title="No runs yet" action={<RunNowButton installation={inst} variant="secondary" />}>Start a run to see it here.</EmptyState>}
    >
      {(d) => <RunsTable runs={d.items} caption={`${inst.agentName} runs`} timeZone={timeZone} showAgent={false} />}
    </QueryView>
  );
}

function ScheduleTab({ inst }: { inst: InstallationOut }) {
  const schedules = useSchedules();
  const timeZone = useTimeZone();
  const mine = (schedules.data ?? []).filter((s) => s.installationId === inst.id);
  const [editing, setEditing] = useState<ScheduleOut | 'new' | null>(null);
  return (
    <div className="stack">
      {schedules.isPending ? <SkeletonTable rows={1} /> : null}
      {schedules.isError ? <ErrorPanel error={schedules.error} title="Could not load schedules" /> : null}
      {schedules.isSuccess && mine.length === 0 && editing === null ? (
        <EmptyState
          title="Not scheduled"
          action={
            <Button variant="secondary" onClick={() => setEditing('new')}>
              Add a schedule
            </Button>
          }
        >
          Schedules create runs at set times; nothing runs in between.
        </EmptyState>
      ) : null}
      {mine.map((s) =>
        editing !== null && editing !== 'new' && editing.id === s.id ? (
          <ScheduleForm key={s.id} inst={inst} schedule={s} onDone={() => setEditing(null)} />
        ) : (
          <Card
            key={s.id}
            title={summary(draftFromCron(s.cron, s.timezone, s.misfirePolicy))}
            actions={
              <>
                <StatusBadge status={s.enabled ? SCHEDULE_STATUS.enabled : SCHEDULE_STATUS.disabled} />
                <Button variant="secondary" onClick={() => setEditing(s)}>
                  Edit schedule
                </Button>
              </>
            }
          >
            <KeyValue
              items={[
                ['Next runs', s.nextOccurrences.map((o) => formatDateTime(o.at, s.timezone)).join(' · ') || '—'],
                ['If the device is off', s.misfirePolicy === 'fire_once' ? 'Run once when device returns' : 'Skip missed run'],
                ['Ready now', s.ready ? 'Yes' : s.blockers.map((b) => b.detail).join('; ')],
                ['Owner time', formatDateTime(new Date().toISOString(), timeZone)],
              ]}
            />
          </Card>
        ),
      )}
      {editing === 'new' ? <ScheduleForm inst={inst} onDone={() => setEditing(null)} /> : null}
    </div>
  );
}

function ScheduleForm({ inst, schedule, onDone }: { inst: InstallationOut; schedule?: ScheduleOut; onDone: () => void }) {
  const timeZone = useTimeZone();
  const [draft, setDraft] = useState<ScheduleDraft>(() =>
    schedule ? draftFromCron(schedule.cron, schedule.timezone, schedule.misfirePolicy) : newDraft(timeZone),
  );
  const create = useCreateSchedule();
  const patch = usePatchSchedule();
  const remove = useDeleteSchedule();
  const [key] = useIntentKey();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const busy = create.isPending || patch.isPending;
  const save = () => {
    const body = { cron: cronFor(draft), timezone: draft.timezone, misfirePolicy: draft.misfirePolicy };
    if (schedule) {
      patch.mutate({ id: schedule.id, body: { version: schedule.version, ...body } }, { onSuccess: onDone });
    } else {
      create.mutate({ key, body: { installationId: inst.id, enabled: true, ...body } }, { onSuccess: onDone });
    }
  };
  return (
    <Card title={schedule ? 'Edit schedule' : 'New schedule'}>
      <div className="stack form-width">
        <ScheduleEditor draft={draft} onChange={setDraft} disabled={busy} />
        {create.isError ? <ErrorPanel error={create.error} title="Could not create the schedule" /> : null}
        {patch.isError ? <ErrorPanel error={patch.error} title="Could not save the schedule" /> : null}
        <div className="row">
          <Button variant="primary" busy={busy} busyLabel="Saving…" onClick={save}>
            Save schedule
          </Button>
          <Button onClick={onDone}>Cancel</Button>
          {schedule ? (
            <Button variant="tertiary" onClick={() => setConfirmDelete(true)}>
              Delete schedule
            </Button>
          ) : null}
        </div>
      </div>
      {schedule ? (
        <ConfirmDialog
          open={confirmDelete}
          title="Delete this schedule?"
          consequence="No more runs will be created by this schedule. Past runs stay in Activity."
          confirmLabel="Delete schedule"
          destructive
          busy={remove.isPending}
          onConfirm={() => remove.mutate({ id: schedule.id }, { onSuccess: onDone })}
          onCancel={() => setConfirmDelete(false)}
        />
      ) : null}
    </Card>
  );
}

function ConfigurationTab({ inst }: { inst: InstallationOut }) {
  const catalog = useCatalogVersion(inst.agentId, inst.agentVersion);
  const patch = usePatchInstallation(inst.id);
  const [key, resetKey] = useIntentKey();
  const options = useFormOptions();
  const { toast } = useFeedback();
  const [value, setValue] = useState<Record<string, unknown>>(inst.config);
  const [errors, setErrors] = useState<FieldError[]>([]);
  const schema = useMemo(() => asSchema(catalog.data?.configurationSchema), [catalog.data]);
  const dirty = JSON.stringify(value) !== JSON.stringify(inst.config);
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname);

  useEffect(() => {
    if (!dirty) return;
    const onUnload = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener('beforeunload', onUnload);
    return () => window.removeEventListener('beforeunload', onUnload);
  }, [dirty]);

  if (catalog.isPending) return <SkeletonTable />;
  if (catalog.isError) return <ErrorPanel error={catalog.error} title="Could not load the configuration form" />;

  const save = () => {
    const local = validate(schema, value);
    setErrors(local);
    if (local.length > 0) return;
    patch.mutate(
      { key, body: { version: inst.version, config: value } },
      {
        onSuccess: () => {
          resetKey();
          toast('Configuration saved.');
        },
        onError: (e) => {
          if (isApiError(e)) setErrors(e.fieldErrors.map((f) => ({ path: `/${fieldNameFromPath(f.path)}`, message: f.message })));
        },
      },
    );
  };

  return (
    <Card title="Configuration">
      <div className="stack form-width">
        <ErrorSummary errors={errors} />
        <SchemaForm schema={schema} value={value} onChange={setValue} errors={errors} options={options} idPrefix="config" />
        {patch.isError && !(isApiError(patch.error) && patch.error.fieldErrors.length > 0) ? (
          <ErrorPanel error={patch.error} title="Could not save the configuration" />
        ) : null}
        <div className="row">
          <Button variant="primary" busy={patch.isPending} busyLabel="Saving…" disabledReason={dirty ? null : 'No unsaved changes.'} onClick={save}>
            Save configuration
          </Button>
          {dirty ? (
            <Button variant="tertiary" onClick={() => setValue(inst.config)}>
              Discard changes
            </Button>
          ) : null}
        </div>
      </div>
      <ConfirmDialog
        open={blocker.state === 'blocked'}
        title="Leave without saving?"
        consequence="Your configuration changes have not been saved and will be lost."
        confirmLabel="Leave without saving"
        cancelLabel="Stay on this page"
        onConfirm={() => blocker.proceed?.()}
        onCancel={() => blocker.reset?.()}
      />
    </Card>
  );
}

function PermissionsTab({ inst }: { inst: InstallationOut }) {
  const catalog = useCatalogAgent(inst.agentId);
  const patch = usePatchInstallation(inst.id);
  const [key, resetKey] = useIntentKey();
  const guard = useActionGuard();
  const { toast } = useFeedback();
  const [approvals, setApprovals] = useState<Record<string, boolean>>({});
  const latest = catalog.data?.latest;
  const requested = inst.needsReapproval && latest ? latest.permissions : inst.requestedPermissions;
  const added = addedPermissionIds(requested, inst.approvedPermissions);
  const items = permissionItems(requested);
  const all = items.every((i) => approvals[i.id]);
  return (
    <div className="stack">
      <Card title="Current grants" subtitle={`Approved for version ${inst.agentVersion}`}>
        <PermissionList items={permissionItems(inst.approvedPermissions)} />
        <p className="muted" style={{ marginTop: 12 }}>
          Capabilities in the run token: <span className="mono">{inst.capabilities.join(', ') || 'none'}</span>
        </p>
      </Card>
      {inst.needsReapproval ? (
        <Card title={`Approve version ${latest?.version ?? 'update'}`} subtitle="Changed permissions are highlighted. The agent cannot run until you approve.">
          <div id="reapprove" className="stack">
            <PermissionList items={items} changedIds={added} approvals={approvals} onApprove={(id, v) => setApprovals((a) => ({ ...a, [id]: v }))} />
            {patch.isError ? <ErrorPanel error={patch.error} title="Could not approve the update" /> : null}
            <div className="row">
              <Button
                variant="primary"
                busy={patch.isPending}
                busyLabel="Approving…"
                disabledReason={guard.offline ?? (all ? null : 'Approve each permission to update.')}
                onClick={() =>
                  patch.mutate(
                    {
                      key,
                      body: {
                        version: inst.version,
                        agentVersion: latest?.version ?? null,
                        approvedPermissions: requested,
                      },
                    },
                    {
                      onSuccess: () => {
                        resetKey();
                        toast('Update approved.');
                      },
                    },
                  )
                }
              >
                Approve and update
              </Button>
            </div>
          </div>
        </Card>
      ) : null}
    </div>
  );
}

function AdvancedTab({ inst }: { inst: InstallationOut }) {
  const catalog = useCatalogVersion(inst.agentId, inst.agentVersion);
  const uninstall = useUninstall();
  const [key] = useIntentKey();
  const navigate = useNavigate();
  const schedules = useSchedules();
  const [confirm, setConfirm] = useState(false);
  const v = catalog.data;
  const mine = (schedules.data ?? []).filter((s) => s.installationId === inst.id);
  return (
    <div className="stack">
      <Advanced label="Technical details" defaultOpen>
        <KeyValue
          items={[
            ['Installation ID', <span key="i" className="mono">{inst.id}</span>],
            ['Agent version', inst.agentVersion],
            ['Image digest', <span key="d" className="mono break-anywhere">{v?.imageDigest ?? '—'}</span>],
            ['SDK protocol', v?.sdkProtocol ?? '—'],
            ['Resource limits', v ? resourcesText(v.resources) : '—'],
            ['Model bindings', Object.entries(inst.modelBindings).map(([k, b]) => `${k} → ${b}`).join(', ') || 'None'],
          ]}
        />
      </Advanced>
      <Card title="Uninstall">
        <p className="muted">Removes the agent from your crew. Past runs and results stay in Activity.</p>
        <div className="row" style={{ marginTop: 12 }}>
          <Button onClick={() => setConfirm(true)}>Uninstall {inst.agentName}</Button>
        </div>
      </Card>
      <ConfirmDialog
        open={confirm}
        title={`Uninstall ${inst.agentName}?`}
        consequence="The agent and its schedules are removed. Runs in progress are cancelled. History stays available."
        affected={mine.map((s) => `Schedule: ${summary(draftFromCron(s.cron, s.timezone, s.misfirePolicy))}`)}
        confirmLabel="Uninstall agent"
        destructive
        busy={uninstall.isPending}
        onConfirm={() => uninstall.mutate({ id: inst.id, key }, { onSuccess: () => void navigate('/agents/installed') })}
        onCancel={() => setConfirm(false)}
      >
        {uninstall.isError ? <ErrorPanel error={uninstall.error} title="Could not uninstall" /> : null}
      </ConfirmDialog>
    </div>
  );
}
