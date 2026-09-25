import { Ban, RotateCw } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router';
import { isApiError, remediation } from '../../api/errors';
import { useActionGuard } from '../../api/guards';
import { useAcknowledgeRun, useCancelRun, useIntentKey, useRetryRun } from '../../api/mutations';
import { useCatalogVersion, useInputRequests, useModels, useRun } from '../../api/queries';
import type { RunEventOut, RunOut } from '../../api/schema';
import { isTerminal } from '../../api/schema';
import type { StreamStatus } from '../../api/sse';
import { useModelEvents, useRunEvents } from '../../api/streams';
import { Button } from '../../components/Button';
import { ConfirmDialog } from '../../components/Dialog';
import { Banner, CopyButton, ErrorPanel, SkeletonBlock } from '../../components/Feedback';
import { Advanced, Card, KeyValue, Page, PageHeader, RawJson } from '../../components/Layout';
import { LocalityChip } from '../../components/LocalityChip';
import { LogViewer, type LogEntry, type LogLevel } from '../../components/LogViewer';
import { Elapsed, Progress } from '../../components/Meters';
import { QueryView } from '../../components/QueryView';
import { StatusBadge } from '../../components/StatusBadge';
import { Timeline, type TimelineEntry } from '../../components/Stepper';
import { useFeedback } from '../../components/Toast';
import { formatDuration, formatTime, formatUtc, shortId } from '../../lib/format';
import { INPUT_STATUS, RUN_STATUS, type StatusSpec } from '../../lib/status';
import { InputRequestCard } from '../common/InputRequestCard';
import { runDuration } from '../common/RunsTable';
import { useTimeZone } from '../common/useTimeZone';
import { ModelProgress } from '../models/ModelProgress';
import { ResultView } from './results/ResultView';

export default function RunDetailPage() {
  const { runId = '' } = useParams();
  const run = useRun(runId);
  return (
    <Page>
      <QueryView query={run} errorTitle="Could not load this run" loading={<SkeletonBlock lines={5} label="Loading run" />}>
        {(r) => <RunView run={r} />}
      </QueryView>
    </Page>
  );
}

const STREAM_TEXT: Record<StreamStatus, string> = {
  connecting: 'Connecting to live updates…',
  live: 'Live',
  reconnecting: 'Live updates paused—reconnecting',
  polling: 'Live updates paused—checking every few seconds',
  ended: 'Finished',
  closed: '',
};

function str(v: unknown): string {
  return typeof v === 'string' ? v : '';
}

export function eventEntry(event: RunEventOut, timeZone: string): TimelineEntry | null {
  const p = event.payload;
  const occurred = (event as RunEventOut & { occurredAt?: string | null }).occurredAt ?? event.createdAt;
  const base = { key: event.sequence, time: formatTime(occurred, timeZone), timeTitle: formatUtc(occurred) };
  switch (event.type) {
    case 'run.state_changed': {
      const to = str(p.to) as keyof typeof RUN_STATUS;
      const spec: StatusSpec | undefined = RUN_STATUS[to];
      if (!spec) return null;
      const reason = str(p.reason) || (p.errorCode ? `Code ${str(p.errorCode)}` : '');
      return { ...base, title: spec.label, detail: reason || undefined, tone: spec.tone, icon: spec.icon };
    }
    case 'run.progress': {
      const pct = typeof p.percent === 'number' ? ` · ${Math.round(p.percent)}%` : '';
      return { ...base, title: `${str(p.message)}${pct}`, detail: str(p.step) || undefined, tone: 'info', icon: 'play' };
    }
    case 'run.input_requested':
      return { ...base, title: `Asked: ${str(p.title) || 'a question'}`, tone: 'warning', icon: 'hand' };
    case 'run.input_answered':
      return { ...base, title: `Answer submitted: ${str(p.title) || str(p.key)}`, tone: 'success', icon: 'check' };
    case 'run.input_closed': {
      const state = str(p.state) as keyof typeof INPUT_STATUS;
      return { ...base, title: `Request ${INPUT_STATUS[state]?.label.toLowerCase() ?? 'closed'}: ${str(p.title) || str(p.key)}`, tone: 'neutral', icon: 'ban' };
    }
    case 'run.result':
      return { ...base, title: str(p.summary) || (p.status === 'succeeded' ? 'Result ready' : 'Run failed'), tone: p.status === 'succeeded' ? 'success' : 'danger', icon: p.status === 'succeeded' ? 'check' : 'x' };
    case 'run.error':
      return { ...base, title: str(p.message) || 'Error', detail: str(p.code) ? `Code ${str(p.code)}` : undefined, tone: 'danger', icon: 'x' };
    case 'run.artifact':
      return { ...base, title: `Produced ${str(p.name)}`, tone: 'neutral', icon: 'info' };
    default:
      return null;
  }
}

function RunView({ run }: { run: RunOut }) {
  const timeZone = useTimeZone();
  const { events, history, status: streamStatus } = useRunEvents(run.id, run.state);
  const requests = useInputRequests({ runId: run.id, state: 'pending' });
  const version = useCatalogVersion(run.agentId, run.agentVersion);
  // While the run waits for its model, follow model load stages closely.
  const models = useModels(run.state === 'LOADING_MODEL' ? { refetchInterval: 1_000 } : undefined);
  const cancel = useCancelRun();
  const retry = useRetryRun();
  const ack = useAcknowledgeRun();
  const [cancelKey, resetCancelKey] = useIntentKey();
  const [retryKey, resetRetryKey] = useIntentKey();
  const guard = useActionGuard();
  const { announce } = useFeedback();
  const [confirmCancel, setConfirmCancel] = useState(false);
  const spec = RUN_STATUS[run.state];
  const terminal = isTerminal(run.state);
  const lastState = useRef(run.state);

  // Announce state changes politely; failures assertively (section 13.18).
  useEffect(() => {
    if (lastState.current !== run.state) {
      lastState.current = run.state;
      announce(`Run is now: ${spec.label}.`, run.state === 'FAILED' ? 'assertive' : 'polite');
    }
  }, [run.state, spec.label, announce]);

  const entries = useMemo(
    () => events.map((e) => eventEntry(e, timeZone)).filter((e): e is TimelineEntry => e !== null),
    [events, timeZone],
  );
  if (entries.length > 0 && !terminal) {
    const last = entries[entries.length - 1];
    if (last) entries[entries.length - 1] = { ...last, current: true };
  }
  const logs: LogEntry[] = useMemo(
    () =>
      events
        .filter((e) => e.type === 'run.log')
        .map((e) => ({
          id: e.sequence,
          time: e.createdAt,
          level: (['debug', 'info', 'warning', 'error'].includes(str(e.payload.level)) ? str(e.payload.level) : 'info') as LogLevel,
          message: str(e.payload.message),
        })),
    [events],
  );
  const metrics = events.filter((e) => e.type === 'run.metric');
  const progress = [...events].reverse().find((e) => e.type === 'run.progress');
  const loadingModel = (models.data ?? []).find(
    (m) => m.memoryState === 'LOADING' && (m.activeLeases ?? []).some((l) => l.holderId === run.id),
  ) ?? (run.state === 'LOADING_MODEL' ? (models.data ?? []).find((m) => m.memoryState === 'LOADING') : undefined);
  useModelEvents(loadingModel?.id, run.state === 'LOADING_MODEL');
  const leaseModel = (models.data ?? []).find((m) => (m.activeLeases ?? []).some((l) => l.holderId === run.id));
  const error = run.error;
  const errorCode = typeof error?.code === 'string' ? error.code : null;
  const errorMessage = typeof error?.message === 'string' ? error.message : null;
  const canCancel = !terminal && run.state !== 'CANCELLING';
  const canRetry = run.state === 'INTERRUPTED' || (run.state === 'FAILED' && run.retryable);

  return (
    <>
      <PageHeader
        title={run.agentName}
        documentTitle={`${run.agentName} run`}
        purpose={
          <>
            Run <span className="mono">#{shortId(run.id)}</span> · {run.trigger === 'schedule' ? 'Scheduled' : 'Started manually'}
            {run.currentAttempt > 1 ? ` · attempt ${run.currentAttempt}` : ''}
          </>
        }
        breadcrumbs={[{ label: 'Activity', to: '/activity/runs' }, { label: `${run.agentName} #${shortId(run.id)}` }]}
        status={
          <>
            <StatusBadge status={spec} context="Run state" />
            <span className="muted">
              {terminal ? 'Took ' : 'Elapsed '}
              {terminal ? formatDuration(runDuration(run)) : <Elapsed since={run.startedAt ?? run.createdAt} />}
            </span>
            <LocalityChip provider={run.usesCloud ? 'cloud' : 'local'} long />
            {!terminal && STREAM_TEXT[streamStatus] ? (
              <span className={`badge ${streamStatus === 'live' ? 'tone-success' : streamStatus === 'connecting' ? 'tone-neutral' : 'tone-warning'}`} role="status">
                {STREAM_TEXT[streamStatus]}
              </span>
            ) : null}
          </>
        }
        actions={
          <>
            {canRetry ? (
              <Button
                variant="primary"
                icon={<RotateCw size={16} aria-hidden="true" />}
                busy={retry.isPending}
                busyLabel="Retrying…"
                disabledReason={guard.runtime}
                onClick={() => retry.mutate({ runId: run.id, key: retryKey }, { onSuccess: resetRetryKey })}
              >
                Retry run
              </Button>
            ) : null}
            {canCancel ? (
              <Button icon={<Ban size={16} aria-hidden="true" />} onClick={() => setConfirmCancel(true)} disabledReason={guard.offline}>
                Cancel run
              </Button>
            ) : null}
            {(run.state === 'FAILED' || run.state === 'INTERRUPTED') && !run.acknowledgedAt ? (
              <Button variant="tertiary" busy={ack.isPending} onClick={() => ack.mutate({ runId: run.id })}>
                Mark as reviewed
              </Button>
            ) : null}
          </>
        }
      />
      {retry.isError ? <ErrorPanel error={retry.error} title="The run was not retried" /> : null}

      {(requests.data ?? []).map((r) => (
        <InputRequestCard key={r.id} request={r} timeZone={timeZone} />
      ))}

      {run.state === 'QUEUED' ? (
        <Banner tone="neutral" title="Waiting to start">
          The run starts when a worker is free. It keeps its place if the device restarts.
        </Banner>
      ) : null}
      {run.state === 'LOADING_MODEL' ? (
        <Card title="Loading local model" subtitle="The run holds a model lease and continues when the model is ready.">
          {loadingModel ? (
            <ModelProgress model={loadingModel} />
          ) : (
            <Progress label="Loading local model" stage="Waiting for the model to be ready" since={run.updatedAt} />
          )}
          <p className="muted" style={{ marginTop: 8 }}>
            The model is already installed on disk; loading it into memory can take a few minutes the first time.
          </p>
        </Card>
      ) : null}
      {run.state === 'RUNNING' || run.state === 'PREPARING' ? (
        <Card title={run.state === 'PREPARING' ? 'Preparing agent' : 'Progress'}>
          <Progress
            label={run.state === 'PREPARING' ? 'Preparing agent' : 'Run progress'}
            percent={typeof progress?.payload.percent === 'number' ? progress.payload.percent : null}
            stage={str(progress?.payload.message) || (run.state === 'PREPARING' ? 'Starting the agent container' : 'Working')}
            since={run.startedAt ?? run.createdAt}
          />
        </Card>
      ) : null}
      {run.state === 'FAILED' ? (
        <div className="error-panel" role="alert">
          <p className="error-panel-title">Failed</p>
          <p>{errorMessage ?? 'The agent stopped with an error.'}</p>
          <p className="muted">
            {run.retryable ? 'You can retry this run. Actions that already completed are not repeated.' : 'This failure cannot be retried; change the configuration and start a new run.'}
          </p>
          {errorCode ? (
            <span className="row">
              <span className="diag-code">Diagnostic code: {errorCode}</span>
              <CopyButton text={errorCode} label="Copy code" />
            </span>
          ) : null}
        </div>
      ) : null}
      {run.state === 'INTERRUPTED' ? (
        <Banner tone="warning" title="Interrupted">
          The device restarted or the agent container was lost. Anything the agent had already finished (listed in the
          timeline) is not repeated when you retry.
        </Banner>
      ) : null}
      {run.state === 'CANCELLED' ? (
        <Banner tone="neutral" title="Cancelled">
          The run was stopped. Actions completed before cancelling are listed in the timeline; nothing else happens.
        </Banner>
      ) : null}

      {run.result ? (
        <Card title="Result">
          <ResultView result={run.result} resultSchema={version.data?.resultSchema} timeZone={timeZone} />
        </Card>
      ) : null}

      <div className="grid-2">
        <Card title="Timeline">
          {history.isPending ? <SkeletonBlock lines={3} label="Loading timeline" /> : null}
          {history.isError ? <ErrorPanel error={history.error} title="Could not load the timeline" onRetry={() => void history.refetch()} /> : null}
          {entries.length > 0 ? <Timeline entries={entries} label="Run timeline" /> : history.isSuccess ? <p className="muted">No events yet.</p> : null}
        </Card>
        <Card title="Resources">
          <KeyValue
            items={[
              ['Model', leaseModel ? `${leaseModel.displayName} (local)` : run.usesCloud ? 'Cloud provider (approved)' : 'Local on this device'],
              ['Duration', formatDuration(runDuration(run))],
              ['Active time used', `${formatDuration(run.activeSecondsUsed)} of ${formatDuration(run.activeTimeoutSeconds)}`],
              ['Waiting for answers', `${formatDuration(run.inputWaitSecondsUsed)} of ${formatDuration(run.maxInputWaitSeconds)}`],
              ...metrics.map((m): [string, string] => [str(m.payload.name), `${String(m.payload.value)}${m.payload.unit ? ` ${str(m.payload.unit)}` : ''}`]),
            ]}
          />
        </Card>
      </div>

      <Advanced label="Logs">
        <LogViewer entries={logs} label="Run logs" timeZone={timeZone} />
      </Advanced>
      {run.result || run.error ? (
        <Advanced label="Raw result">
          <RawJson value={run.result ?? run.error} label="Raw run result" />
        </Advanced>
      ) : null}

      <ConfirmDialog
        open={confirmCancel}
        title="Cancel this run?"
        consequence={
          run.state === 'WAITING_INPUT'
            ? 'The agent stops waiting and ends. Nothing it was asking about will happen.'
            : 'The agent is stopped. Actions it already completed (listed in the timeline) are not undone.'
        }
        confirmLabel="Cancel run"
        cancelLabel="Keep running"
        destructive
        busy={cancel.isPending}
        busyLabel="Cancelling…"
        onConfirm={() =>
          cancel.mutate(
            { runId: run.id, key: cancelKey },
            {
              onSuccess: () => {
                setConfirmCancel(false);
                resetCancelKey();
              },
            },
          )
        }
        onCancel={() => setConfirmCancel(false)}
      >
        {cancel.isError ? (
          <Banner tone="danger" role="alert">
            {isApiError(cancel.error) ? remediation(cancel.error) : 'The run could not be cancelled.'}
          </Banner>
        ) : null}
      </ConfirmDialog>
    </>
  );
}
