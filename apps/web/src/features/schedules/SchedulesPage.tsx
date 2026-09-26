import { CalendarClock } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router';
import { useDeleteSchedule, usePatchSchedule } from '../../api/mutations';
import { useSchedules } from '../../api/queries';
import type { ScheduleOut } from '../../api/schema';
import { Button, ButtonLink } from '../../components/Button';
import { ConfirmDialog } from '../../components/Dialog';
import { DataTable, type Column } from '../../components/DataTable';
import { Banner, EmptyState, ErrorPanel, SkeletonTable } from '../../components/Feedback';
import { Page, PageHeader } from '../../components/Layout';
import { QueryView } from '../../components/QueryView';
import { StatusBadge } from '../../components/StatusBadge';
import { useFeedback } from '../../components/Toast';
import { currentTimeIn, formatDateTime, formatUtc } from '../../lib/format';
import { READINESS_NAMES } from '../../lib/status';
import { RunNowFor } from '../agents/RunNowButton';
import { useTimeZone } from '../common/useTimeZone';
import { draftFromCron, summary } from './ScheduleEditor';

export default function SchedulesPage() {
  const schedules = useSchedules();
  const patch = usePatchSchedule();
  const remove = useDeleteSchedule();
  const timeZone = useTimeZone();
  const { toast } = useFeedback();
  const [toDelete, setToDelete] = useState<ScheduleOut | null>(null);
  const [toggleError, setToggleError] = useState<unknown>(null);

  // Enable/disable is reversible metadata: no confirmation, optimistic, with Undo.
  const toggle = (s: ScheduleOut, enabled: boolean) => {
    setToggleError(null);
    patch.mutate(
      { id: s.id, body: { version: s.version, enabled } },
      {
        onSuccess: (updated) =>
          toast(`${s.agentName} schedule ${enabled ? 'enabled' : 'turned off'}.`, {
            label: 'Undo',
            onClick: () => patch.mutate({ id: s.id, body: { version: updated.version, enabled: !enabled } }),
          }),
        onError: (e) => setToggleError(e),
      },
    );
  };

  const columns: Column<ScheduleOut>[] = [
    {
      key: 'agent',
      header: 'Agent',
      primary: true,
      cell: (s) => <Link to={`/agents/${encodeURIComponent(s.installationId)}/schedule`}>{s.agentName}</Link>,
      sortValue: (s) => s.agentName,
    },
    { key: 'recurrence', header: 'Recurrence', cell: (s) => summary(draftFromCron(s.cron, s.timezone, s.misfirePolicy)) },
    {
      key: 'next',
      header: 'Next run',
      cell: (s) =>
        s.enabled && s.nextRunAt ? (
          <span title={formatUtc(s.nextRunAt)}>
            {formatDateTime(s.nextRunAt, s.timezone)}
            {s.timezone !== timeZone ? <span className="muted" style={{ display: 'block' }}>{formatDateTime(s.nextRunAt, timeZone)} your time</span> : null}
          </span>
        ) : (
          <span className="muted">{s.enabled ? '—' : 'Off'}</span>
        ),
      sortValue: (s) => s.nextRunAt,
    },
    {
      key: 'ready',
      header: 'Ready',
      cell: (s) =>
        s.ready ? (
          <StatusBadge status={{ label: 'Ready', tone: 'success', icon: 'check' }} />
        ) : (
          <span className="stack-sm" style={{ gap: 2 }}>
            <StatusBadge status={{ label: 'Needs attention', tone: 'warning', icon: 'alert' }} />
            {s.blockers.map((b) => (
              <span key={`${b.name}-${b.resource ?? ''}`} className="muted">
                {READINESS_NAMES[b.name]}: {b.detail}
              </span>
            ))}
          </span>
        ),
    },
    {
      key: 'last',
      header: 'Last result',
      cell: (s) =>
        s.lastRunId ? (
          <Link to={`/runs/${encodeURIComponent(s.lastRunId)}`}>
            {s.lastFiredAt ? formatDateTime(s.lastFiredAt, timeZone) : 'Open last run'}
          </Link>
        ) : (
          <span className="muted">Not run yet</span>
        ),
    },
    {
      key: 'enabled',
      header: 'Enabled',
      cell: (s) => (
        <label className="check-row" style={{ padding: 0 }}>
          <input type="checkbox" checked={s.enabled} onChange={(e) => toggle(s, e.target.checked)} />
          <span>{s.enabled ? 'On' : 'Off'}</span>
          <span className="sr-only"> — {s.agentName} schedule</span>
        </label>
      ),
    },
    {
      key: 'actions',
      header: 'Actions',
      cell: (s) => (
        <span className="row">
          <RunNowFor installationId={s.installationId} />
          <ButtonLink to={`/agents/${encodeURIComponent(s.installationId)}/schedule`} variant="tertiary">
            Edit<span className="sr-only"> {s.agentName} schedule</span>
          </ButtonLink>
          <Button variant="tertiary" onClick={() => setToDelete(s)}>
            Delete<span className="sr-only"> {s.agentName} schedule</span>
          </Button>
        </span>
      ),
    },
  ];

  return (
    <Page>
      <PageHeader
        title="Schedules"
        purpose={`Schedules create runs at set times; nothing runs in between. Your time: ${currentTimeIn(timeZone)} (${timeZone}).`}
        actions={
          <ButtonLink to="/agents/installed" variant="primary">
            Schedule an agent
          </ButtonLink>
        }
      />
      {toggleError ? <ErrorPanel error={toggleError} title="The change was undone because the device refused it" /> : null}
      <QueryView
        query={schedules}
        errorTitle="Could not load schedules"
        loading={<SkeletonTable label="Loading schedules" />}
        isEmpty={(d) => d.length === 0}
        empty={
          <EmptyState icon={CalendarClock} title="No schedules" action={<ButtonLink to="/agents/installed">Open your crew</ButtonLink>}>
            Add a schedule from an agent’s Schedule tab.
          </EmptyState>
        }
      >
        {(list) => (
          <>
            {list.some((s) => s.enabled && !s.ready) ? (
              <Banner tone="warning" title="Some schedules will fail readiness">
                Fix the listed connections or models before their next run.
              </Banner>
            ) : null}
            <DataTable caption="Schedules" columns={columns} rows={list} rowKey={(s) => s.id} initialSort={{ key: 'next', direction: 'asc' }} />
          </>
        )}
      </QueryView>
      <ConfirmDialog
        open={toDelete !== null}
        title="Delete this schedule?"
        consequence={`${toDelete?.agentName ?? 'The agent'} will no longer run automatically. Past runs stay in Activity.`}
        confirmLabel="Delete schedule"
        destructive
        busy={remove.isPending}
        onConfirm={() => toDelete && remove.mutate({ id: toDelete.id }, { onSuccess: () => setToDelete(null) })}
        onCancel={() => setToDelete(null)}
      >
        {remove.isError ? <ErrorPanel error={remove.error} title="Could not delete" /> : null}
      </ConfirmDialog>
    </Page>
  );
}
