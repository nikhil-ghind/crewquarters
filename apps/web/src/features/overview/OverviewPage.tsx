import { AlertTriangle, Bot, CheckCircle2, Circle, Cpu, Play } from 'lucide-react';
import { Link } from 'react-router';
import { useAcknowledgeRun } from '../../api/mutations';
import {
  useAttention,
  useConnections,
  useInputRequests,
  useInstallations,
  useMemory,
  useModels,
  useRuns,
  useSchedules,
  useSystemStatus,
} from '../../api/queries';
import { isTerminal, type AttentionItem, type RunOut } from '../../api/schema';
import { Button, ButtonLink } from '../../components/Button';
import { EmptyState, ErrorPanel, SkeletonBlock } from '../../components/Feedback';
import { Card, Page, PageHeader, Section } from '../../components/Layout';
import { LocalityChip } from '../../components/LocalityChip';
import { Elapsed, ResourceMeter } from '../../components/Meters';
import { StatusBadge } from '../../components/StatusBadge';
import { formatBytes, formatDateTime, formatDuration, formatRelative, formatUtc } from '../../lib/format';
import { ACTIVE_RUN_STATES, MODEL_MEMORY_STATUS, RUN_STATUS } from '../../lib/status';
import { InputRequestCard } from '../common/InputRequestCard';
import { runDuration } from '../common/RunsTable';
import { useTimeZone } from '../common/useTimeZone';
import { memoryUsed } from '../../shell/TopBar';

function AttentionCard({ item }: { item: AttentionItem }) {
  const ack = useAcknowledgeRun();
  const link =
    item.kind === 'failed_run' && item.runId
      ? { to: `/runs/${encodeURIComponent(item.runId)}`, label: 'Review run' }
      : item.kind === 'schedule_blocked'
        ? { to: '/schedules', label: 'Fix schedule' }
        : item.kind === 'model_action' && item.modelId
          ? { to: `/models/${encodeURIComponent(item.modelId)}`, label: 'Open model' }
          : { to: '/activity/approvals', label: 'Open' };
  return (
    <article className="card card-compact card-attention stack-sm">
      <span className="row field-label">
        <AlertTriangle size={16} aria-hidden="true" style={{ color: 'var(--color-warning)' }} />
        <span className="break-anywhere">{item.title}</span>
      </span>
      <p className="muted break-anywhere">{item.detail}</p>
      <div className="row">
        <ButtonLink to={link.to} variant="secondary">
          {link.label}
        </ButtonLink>
        {item.kind === 'failed_run' && item.runId ? (
          <Button variant="tertiary" busy={ack.isPending} onClick={() => item.runId && ack.mutate({ runId: item.runId })}>
            Dismiss
          </Button>
        ) : null}
      </div>
    </article>
  );
}

function NeedsAttention() {
  const attention = useAttention();
  const requests = useInputRequests();
  const connections = useConnections();
  const timeZone = useTimeZone();
  const google = connections.data?.find((c) => c.provider === 'google' && c.status === 'NEEDS_ATTENTION');
  const other = (attention.data?.items ?? []).filter((i) => i.kind !== 'input_request');
  const pending = requests.data ?? [];
  if (pending.length === 0 && other.length === 0 && !google) return null;
  return (
    <Section title="Needs you" id="needs-attention">
      <div className="stack">
        {pending.map((r) => (
          <InputRequestCard key={r.id} request={r} showRunLink timeZone={timeZone} headingLevel={3} />
        ))}
        <div className="grid-3">
          {google ? (
            <article className="card card-compact card-attention stack-sm">
              <span className="row field-label">
                <AlertTriangle size={16} aria-hidden="true" style={{ color: 'var(--color-warning)' }} />
                Google access expired
              </span>
              <p className="muted">{google.detail ?? 'Agents that use Gmail or Sheets cannot run until you reconnect.'}</p>
              <div className="row">
                <ButtonLink to="/connections/google" variant="secondary">
                  Reconnect Google
                </ButtonLink>
              </div>
            </article>
          ) : null}
          {other.map((item, i) => (
            <AttentionCard key={`${item.kind}-${item.runId ?? item.scheduleId ?? item.modelId ?? i}`} item={item} />
          ))}
        </div>
      </div>
    </Section>
  );
}

function GettingStarted({ hasModel, hasAgent, hasRun }: { hasModel: boolean; hasAgent: boolean; hasRun: boolean }) {
  const steps = [
    { done: hasModel, title: 'Install a model', text: 'Download a local model to this device.', to: '/models', action: 'Open Models', icon: Cpu },
    { done: hasAgent, title: 'Add an agent', text: 'Pick an agent from the marketplace and approve its permissions.', to: '/agents/marketplace', action: 'Open Marketplace', icon: Bot },
    { done: hasRun, title: 'Run it', text: 'Start a run and watch its progress live.', to: '/agents/installed', action: 'Open your crew', icon: Play },
  ];
  return (
    <Section title="Get started" id="get-started">
      <ol className="grid-3" style={{ listStyle: 'none' }}>
        {steps.map((s) => (
          <li key={s.title} className="card stack-sm">
            <span className="row">
              {s.done ? (
                <CheckCircle2 size={20} aria-hidden="true" style={{ color: 'var(--color-success)' }} />
              ) : (
                <Circle size={20} aria-hidden="true" />
              )}
              <h3>{s.title}</h3>
              <span className="sr-only">{s.done ? '(done)' : '(not done)'}</span>
            </span>
            <p className="muted">{s.text}</p>
            {!s.done ? (
              <ButtonLink to={s.to} variant="secondary">
                {s.action}
              </ButtonLink>
            ) : null}
          </li>
        ))}
      </ol>
    </Section>
  );
}

function ActiveRunCard({ run }: { run: RunOut }) {
  return (
    <article className="card card-compact stack-sm">
      <div className="row-between">
        <Link to={`/runs/${encodeURIComponent(run.id)}`} className="field-label">
          {run.agentName}
        </Link>
        <StatusBadge status={RUN_STATUS[run.state]} />
      </div>
      <span className="muted">
        Started {formatRelative(run.startedAt ?? run.createdAt)} · <Elapsed since={run.startedAt ?? run.createdAt} /> elapsed
      </span>
      <LocalityChip provider={run.usesCloud ? 'cloud' : 'local'} />
    </article>
  );
}

export default function OverviewPage() {
  const timeZone = useTimeZone();
  const installations = useInstallations();
  const models = useModels();
  const memory = useMemory();
  const status = useSystemStatus();
  const active = useRuns({ state: [...ACTIVE_RUN_STATES], limit: 20 });
  const recent = useRuns({ state: ['SUCCEEDED', 'FAILED', 'CANCELLED', 'INTERRUPTED'], limit: 5 });
  const schedules = useSchedules();

  const hasModel = (models.data ?? []).some((m) => m.downloadState === 'INSTALLED');
  const hasAgent = (installations.data ?? []).length > 0;
  const hasRun = (recent.data?.items.length ?? 0) + (active.data?.items.length ?? 0) > 0;
  const isNew = installations.isSuccess && models.isSuccess && recent.isSuccess && (!hasModel || !hasAgent || !hasRun);
  const resident = models.data?.filter((m) => m.memoryState !== 'NOT_LOADED') ?? [];
  const used = memory.data ? memoryUsed(memory.data) : null;
  const disk = status.data?.checks.find((c) => c.group === 'storage');
  const upcoming = (schedules.data ?? [])
    .filter((s) => s.enabled)
    .flatMap((s) => s.nextOccurrences.map((o) => ({ schedule: s, at: o.at, zone: o.zoneAbbreviation })))
    .sort((a, b) => a.at.localeCompare(b.at))
    .slice(0, 3);
  const activeRuns = (active.data?.items ?? []).filter((r) => !isTerminal(r.state));

  return (
    <Page>
      <PageHeader
        title="Home"
        purpose="What needs you, what is running, and what just finished."
        actions={
          <ButtonLink to="/agents/installed" variant="primary" icon={<Play size={16} aria-hidden="true" />}>
            Run a crew member
          </ButtonLink>
        }
      />
      <NeedsAttention />
      {isNew ? <GettingStarted hasModel={hasModel} hasAgent={hasAgent} hasRun={hasRun} /> : null}
      <div className="grid-3">
        <Card title="Running now" actions={<Link to="/activity/runs">All runs</Link>}>
          {active.isError ? <ErrorPanel error={active.error} title="Could not load active runs" /> : null}
          {active.isPending ? (
            <SkeletonBlock lines={2} />
          ) : activeRuns.length === 0 ? (
            <p className="muted">Nothing is running right now.</p>
          ) : (
            <div className="stack-sm">
              {activeRuns.map((r) => (
                <ActiveRunCard key={r.id} run={r} />
              ))}
            </div>
          )}
        </Card>
        <Card title="Just finished" actions={<Link to="/activity/runs">History</Link>}>
          {recent.isPending ? (
            <SkeletonBlock lines={3} />
          ) : recent.isError ? (
            <ErrorPanel error={recent.error} title="Could not load recent runs" onRetry={() => void recent.refetch()} />
          ) : (recent.data?.items.length ?? 0) === 0 ? (
            <EmptyState title="No results yet">Completed runs appear here.</EmptyState>
          ) : (
            <ul className="stack-sm" style={{ listStyle: 'none' }}>
              {recent.data?.items.slice(0, 3).map((r) => (
                <li key={r.id} className="stack-sm" style={{ gap: 4, borderBottom: '1px solid var(--color-border)', paddingBottom: 8 }}>
                  <span className="field-label">{r.agentName}</span>
                  <span className="muted">
                    {formatDateTime(r.finishedAt ?? r.updatedAt, timeZone)} · {formatDuration(runDuration(r))}
                  </span>
                  <span className="row">
                    <StatusBadge status={RUN_STATUS[r.state]} />
                    <LocalityChip provider={r.usesCloud ? 'cloud' : 'local'} />
                    <Link to={`/runs/${encodeURIComponent(r.id)}`}>
                      Open result<span className="sr-only"> of {r.agentName} run</span>
                    </Link>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Next up" actions={<Link to="/schedules">Schedules</Link>}>
          {schedules.isPending ? (
            <SkeletonBlock lines={2} />
          ) : upcoming.length === 0 ? (
            <p className="muted">No scheduled runs. Add a schedule from an agent’s Schedule tab.</p>
          ) : (
            <ul className="stack-sm" style={{ listStyle: 'none' }}>
              {upcoming.map((u) => (
                <li key={`${u.schedule.id}-${u.at}`} className="stack-sm" style={{ gap: 2 }}>
                  <span className="field-label">{u.schedule.agentName}</span>
                  <span className="muted" title={formatUtc(u.at)}>
                    {formatDateTime(u.at, timeZone)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <Card title="This device">
        <div className="stack">
          {used ? (
            <ResourceMeter
              label="Unified memory"
              value={used.used}
              max={used.total}
              valueText={`${formatBytes(used.used)} of ${formatBytes(used.total)} used`}
              warnAt={0.85}
              dangerAt={0.95}
            />
          ) : memory.isPending ? (
            <SkeletonBlock lines={1} />
          ) : (
            <p className="muted">Memory is not reported by this device.</p>
          )}
          <div className="row">
            <span className="field-label">Models in memory:</span>
            {resident.length === 0 ? (
              <span className="muted">None. Models load when chat or an agent needs them.</span>
            ) : (
              resident.map((m) => (
                <span key={m.id} className="row">
                  <Link to={`/models/${encodeURIComponent(m.id)}`}>{m.displayName}</Link>
                  <StatusBadge status={MODEL_MEMORY_STATUS[m.memoryState]} context="Memory" />
                  {m.idleUnloadAt && (m.activeLeases ?? []).length === 0 ? (
                    <span className="muted" title={formatUtc(m.idleUnloadAt)}>
                      Unloads {formatRelative(m.idleUnloadAt)} if unused
                    </span>
                  ) : null}
                </span>
              ))
            )}
          </div>
          <p className="muted">
            Storage: {disk ? disk.detail : '—'} · Agent containers running: {activeRuns.length}
          </p>
        </div>
      </Card>
    </Page>
  );
}
