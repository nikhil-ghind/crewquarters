import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { isApiError, type FieldError } from '../../api/errors';
import { useActionGuard } from '../../api/guards';
import { useCreateSchedule, useInstallAgent, useIntentKey } from '../../api/mutations';
import { useCatalogAgent, useConnections, useInstallation, useKnowledgeBases, useModels, useProviderProfiles, useSettings } from '../../api/queries';
import type { CatalogAgentOut, InstallationOut } from '../../api/schema';
import { Button, ButtonLink } from '../../components/Button';
import { Banner, ErrorPanel } from '../../components/Feedback';
import { ErrorSummary, Field } from '../../components/Field';
import { Card, KeyValue, Page, PageHeader } from '../../components/Layout';
import { LocalityChip } from '../../components/LocalityChip';
import { PermissionList } from '../../components/PermissionRow';
import { QueryView } from '../../components/QueryView';
import { SchemaForm, schemaFieldId } from '../../components/SchemaForm';
import { StatusBadge } from '../../components/StatusBadge';
import { Stepper, type StepItem } from '../../components/Stepper';
import { fieldNameFromPath, validate } from '../../lib/jsonSchema';
import { permissionItems, usesCloud } from '../../lib/permissions';
import { readDraft, writeDraft } from '../../lib/storage';
import { CONNECTION_STATUS, MODEL_DOWNLOAD_STATUS, PROVIDER_NAMES } from '../../lib/status';
import { useTimeZone } from '../common/useTimeZone';
import { cronFor, newDraft, ScheduleEditor, summary, type ScheduleDraft } from '../schedules/ScheduleEditor';
import { CloudUseBadge, installationStatus, ReadinessList, triggerText } from './AgentBits';
import { resourcesText } from './AgentDetailPage';
import { allApproved, configSchema, defaultBindings, initialConfig, profileFamilies, requiredConnectionsText, variantsFor } from './install';
import { RunNowButton } from './RunNowButton';
import { useFormOptions } from './useFormOptions';

const STEPS = [
  { id: 'compatibility', label: 'Compatibility' },
  { id: 'permissions', label: 'Permissions' },
  { id: 'configuration', label: 'Configuration' },
  { id: 'requirements', label: 'Requirements' },
  { id: 'schedule', label: 'Schedule (optional)' },
  { id: 'review', label: 'Review and install' },
] as const;
type StepId = (typeof STEPS)[number]['id'];

interface Draft {
  step: StepId;
  config: Record<string, unknown>;
  bindings: Record<string, string>;
  approvals: Record<string, boolean>;
  scheduleOn: boolean;
  schedule: ScheduleDraft;
}

export default function InstallWizard() {
  const { agentId = '' } = useParams();
  const agent = useCatalogAgent(agentId);
  // Wait for settings so timezone fields default to the owner's timezone, not the browser's.
  const settings = useSettings();
  return (
    <Page>
      <QueryView query={agent} errorTitle="Could not load this agent">
        {(a) => (settings.isPending ? null : <Wizard agent={a} />)}
      </QueryView>
    </Page>
  );
}

function Wizard({ agent }: { agent: CatalogAgentOut }) {
  const version = agent.latest;
  const navigate = useNavigate();
  const timeZone = useTimeZone();
  const models = useModels();
  const connections = useConnections();
  const kbs = useKnowledgeBases();
  const profiles = useProviderProfiles();
  const options = useFormOptions();
  const guard = useActionGuard();
  const install = useInstallAgent();
  const createSchedule = useCreateSchedule();
  const [installKey, resetInstallKey] = useIntentKey();
  const [scheduleKey] = useIntentKey();
  const draftKey = `install.${agent.agentId}.${version.version}`;
  const schema = useMemo(() => configSchema(version), [version]);
  const items = useMemo(() => permissionItems(version.permissions), [version.permissions]);
  const cloud = usesCloud(version.permissions);

  // Non-secret form state survives refresh and OAuth redirects (section 13.1).
  const [draft, setDraft] = useState<Draft>(
    () =>
      readDraft<Draft>(draftKey) ?? {
        step: 'compatibility',
        config: initialConfig(version, timeZone),
        bindings: {},
        approvals: {},
        scheduleOn: false,
        schedule: newDraft(timeZone),
      },
  );
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [installed, setInstalled] = useState<InstallationOut | null>(null);
  const [finished, setFinished] = useState(false);
  const update = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }));

  useEffect(() => writeDraft(draftKey, draft), [draftKey, draft]);
  useEffect(() => {
    if (models.data && Object.keys(draft.bindings).length === 0) {
      const bindings = defaultBindings(version, models.data);
      if (Object.keys(bindings).length > 0) update({ bindings });
    }
  }, [models.data, version, draft.bindings]);

  const index = STEPS.findIndex((s) => s.id === draft.step);
  const go = (step: StepId) => {
    update({ step });
    window.scrollTo({ top: 0 });
  };

  const requiredConnections = requiredConnectionsText(version);
  const connectionIssues = requiredConnections.filter((p) => connections.data?.find((c) => c.provider === p)?.status !== 'CONNECTED');
  const families = profileFamilies(version);

  const stepItems: StepItem[] = STEPS.map((s, i) => ({
    id: s.id,
    label: s.label,
    state: i === index ? 'current' : i < index ? 'completed' : s.id === 'schedule' ? 'optional' : 'upcoming',
  }));

  const configErrors = () => validate(schema, draft.config);

  const doInstall = () => {
    const local = configErrors();
    if (local.length > 0) {
      setErrors(local);
      go('configuration');
      return;
    }
    install.mutate(
      {
        key: installKey,
        body: {
          agentId: agent.agentId,
          version: version.version,
          config: draft.config,
          approvedPermissions: version.permissions,
          modelBindings: draft.bindings,
          enabled: true,
        },
      },
      {
        onSuccess: (inst) => void afterInstall(inst),
        onError: (e) => {
          if (isApiError(e) && e.fieldErrors.length > 0) {
            setErrors(e.fieldErrors.map((f) => ({ path: `/${fieldNameFromPath(f.path)}`, message: f.message })));
          }
        },
      },
    );
  };

  const afterInstall = async (inst: InstallationOut) => {
    setInstalled(inst);
    resetInstallKey();
    if (draft.scheduleOn) {
      try {
        await createSchedule.mutateAsync({
          key: scheduleKey,
          body: {
            installationId: inst.id,
            cron: cronFor(draft.schedule),
            timezone: draft.schedule.timezone,
            misfirePolicy: draft.schedule.misfirePolicy,
            enabled: true,
          },
        });
      } catch {
        // Reported on the review step; the agent itself is installed.
        return;
      }
    }
    writeDraft(draftKey, null);
    setFinished(true);
    window.scrollTo({ top: 0 });
  };

  const nextDisabled: string | null =
    draft.step === 'compatibility' && !version.compatible
      ? 'This agent cannot run on this device.'
      : draft.step === 'permissions' && !allApproved(version, draft.approvals)
        ? 'Approve each permission to continue.'
        : null;

  const onNext = () => {
    if (draft.step === 'configuration') {
      const local = configErrors();
      setErrors(local);
      if (local.length > 0) {
        document.querySelector<HTMLElement>('.error-summary')?.focus();
        return;
      }
    }
    const next = STEPS[index + 1];
    if (next) go(next.id);
  };

  if (finished && installed) return <Installed agent={agent} installation={installed} scheduled={draft.scheduleOn} />;

  return (
    <>
      <PageHeader
        title={`Install ${agent.name}`}
        purpose="Check compatibility, approve permissions, configure, then install."
        breadcrumbs={[
          { label: 'Crew', to: '/agents/installed' },
          { label: 'Marketplace', to: '/agents/marketplace' },
          { label: agent.name, to: `/agents/marketplace/${encodeURIComponent(agent.agentId)}` },
          { label: 'Install' },
        ]}
      />
      <div className="setup-layout">
        <Stepper steps={stepItems} label="Install steps" onSelect={(id) => go(id as StepId)} />
        <div className="stack-lg form-width" style={{ minWidth: 0 }}>
          {draft.step === 'compatibility' ? (
            <Card title="Compatibility">
              <ul className="stack-sm" style={{ listStyle: 'none' }}>
                <li className="row-between">
                  <span>Image architecture ({version.architectures.join(', ')})</span>
                  <StatusBadge status={version.compatible ? { label: 'Compatible', tone: 'success', icon: 'check' } : { label: 'Not compatible', tone: 'danger', icon: 'x' }} />
                </li>
                <li className="row-between">
                  <span>SDK protocol {version.sdkProtocol}</span>
                  <StatusBadge status={{ label: 'Supported', tone: 'success', icon: 'check' }} />
                </li>
                {families.map((f) => {
                  const installedVariant = variantsFor(f, models.data).find((m) => m.downloadState === 'INSTALLED');
                  return (
                    <li key={f} className="row-between">
                      <span>Model capability {f}</span>
                      <StatusBadge
                        status={
                          installedVariant
                            ? { label: 'Installed on disk', tone: 'success', icon: 'disk' }
                            : { label: 'Model not installed', tone: 'warning', icon: 'alert' }
                        }
                      />
                    </li>
                  );
                })}
                {requiredConnections.map((p) => {
                  const c = connections.data?.find((x) => x.provider === p);
                  return (
                    <li key={p} className="row-between">
                      <span>{PROVIDER_NAMES[p] ?? p} connection</span>
                      <StatusBadge status={CONNECTION_STATUS[c?.status ?? 'NOT_CONNECTED']} />
                    </li>
                  );
                })}
                <li className="row-between">
                  <span>Resources: {resourcesText(version.resources)}</span>
                  <StatusBadge status={{ label: 'Within limits', tone: 'success', icon: 'check' }} />
                </li>
              </ul>
              {!version.compatible ? (
                <Banner tone="danger" title="Not compatible">
                  {version.compatibilityIssues.join(' ')}
                </Banner>
              ) : null}
              {connectionIssues.length > 0 ? (
                <p className="muted" style={{ marginTop: 12 }}>
                  You can install now; the agent stays “Needs attention” until {connectionIssues.map((p) => PROVIDER_NAMES[p] ?? p).join(' and ')} is
                  connected.
                </p>
              ) : null}
            </Card>
          ) : null}

          {draft.step === 'permissions' ? (
            <Card title="Review permissions" subtitle="Approve each capability. Cloud and phone permissions are highlighted.">
              <PermissionList items={items} approvals={draft.approvals} onApprove={(id, v) => update({ approvals: { ...draft.approvals, [id]: v } })} />
            </Card>
          ) : null}

          {draft.step === 'configuration' ? (
            <Card title="Configure">
              <div className="stack">
                <ErrorSummary errors={errors} idFor={(p) => schemaFieldId('install', fieldNameFromPath(p))} />
                <SchemaForm
                  schema={schema}
                  value={draft.config}
                  onChange={(config) => update({ config })}
                  errors={errors}
                  options={options}
                  idPrefix="install"
                  onBlurField={() => setErrors((prev) => (prev.length ? configErrors() : prev))}
                />
              </div>
            </Card>
          ) : null}

          {draft.step === 'requirements' ? (
            <Card title="Requirements" subtitle="Choose which resources this agent uses.">
              <div className="stack">
                {families.map((family) => {
                  const variants = variantsFor(family, models.data);
                  return (
                    <Field key={family} label={`Local model for ${family}`} help="Loaded automatically when a run needs it.">
                      <select
                        className="select"
                        value={draft.bindings[family] ?? ''}
                        onChange={(e) => update({ bindings: { ...draft.bindings, [family]: e.target.value } })}
                      >
                        {variants.map((m) => (
                          <option key={m.id} value={m.id}>
                            {m.displayName} — {MODEL_DOWNLOAD_STATUS[m.downloadState].label}
                          </option>
                        ))}
                      </select>
                    </Field>
                  );
                })}
                {requiredConnections.map((p) => {
                  const c = connections.data?.find((x) => x.provider === p);
                  return (
                    <div key={p} className="row-between">
                      <span>
                        {PROVIDER_NAMES[p] ?? p}
                        {c && 'account' in c && c.account ? ` · ${c.account}` : ''}
                      </span>
                      <StatusBadge status={CONNECTION_STATUS[c?.status ?? 'NOT_CONNECTED']} />
                    </div>
                  );
                })}
                {(version.permissions as { knowledge?: unknown[] }).knowledge?.length ? (
                  <p className="muted">
                    Knowledge base: chosen in Configuration ({kbs.data?.length ?? 0} available).
                  </p>
                ) : null}
                {cloud.length > 0 ? (
                  <Banner tone="info" title="Cloud provider profile">
                    {cloud.map((p) => {
                      const has = profiles.data?.some((pp) => pp.provider === p && pp.enabled);
                      return (
                        <span key={p} style={{ display: 'block' }}>
                          {PROVIDER_NAMES[p] ?? p}: {has ? 'an enabled key is saved' : 'no enabled key yet — add one in Connections'}
                        </span>
                      );
                    })}
                  </Banner>
                ) : null}
                {families.length === 0 && requiredConnections.length === 0 && cloud.length === 0 ? (
                  <p className="muted">This agent needs no model or connections.</p>
                ) : null}
              </div>
            </Card>
          ) : null}

          {draft.step === 'schedule' ? (
            <Card title="Schedule (optional)">
              {version.triggers.includes('schedule') ? (
                <div className="stack">
                  <label className="check-row">
                    <input type="checkbox" checked={draft.scheduleOn} onChange={(e) => update({ scheduleOn: e.target.checked })} />
                    <span>Run this agent on a schedule</span>
                  </label>
                  {draft.scheduleOn ? <ScheduleEditor draft={draft.schedule} onChange={(schedule) => update({ schedule })} /> : null}
                </div>
              ) : (
                <p className="muted">This agent runs on demand only ({triggerText(version.triggers)}).</p>
              )}
            </Card>
          ) : null}

          {draft.step === 'review' ? (
            <Card title="Review and install">
              <div className="stack">
                <KeyValue
                  items={[
                    ['Agent', `${agent.name} v${version.version}`],
                    ['Image digest', <span key="d" className="mono break-anywhere">{version.imageDigest}</span>],
                    ['Model', families.length ? Object.values(draft.bindings).join(', ') || 'Default' : 'No model'],
                    [
                      'Model behavior',
                      families.length
                        ? 'Loads the local model when a run starts (cold start can take a few minutes) and unloads it after the idle timeout.'
                        : '—',
                    ],
                    ['Processing', cloud.length ? <CloudUseBadge key="c" permissions={version.permissions} /> : <LocalityChip key="l" long />],
                    ['Schedule', draft.scheduleOn ? summary(draft.schedule) : 'None (run on demand)'],
                  ]}
                />
                {cloud.length > 0 ? (
                  <Banner tone="warning" title="Data leaves this device">
                    Runs may send email content, documents or prompts to {cloud.map((p) => PROVIDER_NAMES[p] ?? p).join(', ')}. Local models are
                    never replaced by a cloud provider automatically.
                  </Banner>
                ) : null}
                <h3>Permissions you approved</h3>
                <PermissionList items={items} />
                {install.isError ? (
                  <Banner tone="danger" role="alert" title="Installation incomplete">
                    {agent.name} was not added to your crew. Your choices are kept; fix the problem and select Install agent again.
                  </Banner>
                ) : null}
                {install.isError ? <ErrorPanel error={install.error} title="Why it failed" /> : null}
                {installed && createSchedule.isError ? (
                  <Banner
                    tone="warning"
                    title="Installed, but the schedule was not created"
                    action={
                      <Button onClick={() => navigate(`/agents/${encodeURIComponent(installed.id)}/schedule`)}>Open Schedule tab</Button>
                    }
                  >
                    Add the schedule from the agent’s Schedule tab.
                  </Banner>
                ) : null}
              </div>
            </Card>
          ) : null}

          <div className="row sticky-action">
            {index > 0 ? (
              <Button variant="tertiary" onClick={() => go(STEPS[index - 1]?.id ?? 'compatibility')}>
                Back
              </Button>
            ) : null}
            {draft.step === 'review' ? (
              <Button
                variant="primary"
                busy={install.isPending || createSchedule.isPending}
                busyLabel="Installing…"
                disabledReason={guard.offline ?? (allApproved(version, draft.approvals) ? null : 'Approve each permission first.')}
                onClick={doInstall}
              >
                Install agent
              </Button>
            ) : (
              <Button variant="primary" disabledReason={nextDisabled} onClick={onNext}>
                Continue
              </Button>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

/**
 * The finish step: the agent is in the crew. Run now is the primary action when the
 * installation is ready; otherwise it stays disabled and says why, as everywhere else.
 */
function Installed({ agent, installation, scheduled }: { agent: CatalogAgentOut; installation: InstallationOut; scheduled: boolean }) {
  // Seeded from the install response, then kept fresh (readiness can change, e.g. a connection).
  const current = useInstallation(installation.id).data ?? installation;
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus(), []);
  const agentPath = `/agents/${encodeURIComponent(current.id)}`;
  return (
    <>
      <PageHeader
        title={`Install ${agent.name}`}
        breadcrumbs={[
          { label: 'Crew', to: '/agents/installed' },
          { label: 'Marketplace', to: '/agents/marketplace' },
          { label: agent.name, to: `/agents/marketplace/${encodeURIComponent(agent.agentId)}` },
          { label: 'Install' },
        ]}
      />
      <section className="card stack form-width" aria-labelledby="install-done">
        <div className="row-between">
          <h2 id="install-done" className="card-title" ref={heading} tabIndex={-1}>
            {agent.name} is in your crew
          </h2>
          <StatusBadge status={installationStatus(current)} context="Readiness" />
        </div>
        <p className="muted">
          {scheduled ? 'Its schedule is saved. ' : ''}
          {current.readiness.ready ? 'Everything it needs is ready. Start a run now or open the agent.' : 'It can run once the checks marked below are fixed.'}
        </p>
        {current.readiness.ready ? null : <ReadinessList installation={current} />}
        <div className="row">
          <RunNowButton installation={current} />
          <ButtonLink to={agentPath} variant="secondary">
            Open {agent.name}
          </ButtonLink>
        </div>
      </section>
    </>
  );
}
