import { Link, useNavigate } from 'react-router';
import type { RunOut } from '../../api/schema';
import { DataTable, type Column } from '../../components/DataTable';
import { LocalityChip } from '../../components/LocalityChip';
import { StatusBadge } from '../../components/StatusBadge';
import { formatDateTime, formatDuration, formatUtc, secondsBetween, shortId } from '../../lib/format';
import { RUN_STATUS } from '../../lib/status';

export function runTitle(run: Pick<RunOut, 'agentName' | 'id'>): string {
  return `${run.agentName} run ${shortId(run.id)}`;
}

export function runDuration(run: RunOut): number | null {
  return secondsBetween(run.startedAt ?? run.createdAt, run.finishedAt);
}

export function TriggerLabel({ run }: { run: RunOut }) {
  return <>{run.trigger === 'schedule' ? 'Scheduled' : run.trigger === 'agent' ? 'Started by another agent' : 'Manual'}</>;
}

interface RunsTableProps {
  runs: RunOut[];
  caption: string;
  timeZone?: string;
  showAgent?: boolean;
}

export function RunsTable({ runs, caption, timeZone, showAgent = true }: RunsTableProps) {
  const navigate = useNavigate();
  const columns: Column<RunOut>[] = [
    {
      key: 'run',
      header: 'Run',
      primary: true,
      cell: (r) => (
        <Link to={`/runs/${encodeURIComponent(r.id)}`} className="break-anywhere">
          {showAgent ? r.agentName : 'Run'} <span className="mono muted">#{shortId(r.id)}</span>
        </Link>
      ),
      sortValue: (r) => r.agentName,
    },
    {
      key: 'state',
      header: 'Status',
      cell: (r) => <StatusBadge status={RUN_STATUS[r.state]} />,
      sortValue: (r) => RUN_STATUS[r.state].label,
    },
    { key: 'trigger', header: 'Trigger', cell: (r) => <TriggerLabel run={r} /> },
    {
      key: 'started',
      header: 'Started',
      cell: (r) => <span title={formatUtc(r.startedAt ?? r.createdAt)}>{formatDateTime(r.startedAt ?? r.createdAt, timeZone)}</span>,
      sortValue: (r) => r.startedAt ?? r.createdAt,
    },
    { key: 'duration', header: 'Duration', cell: (r) => formatDuration(runDuration(r)), sortValue: (r) => runDuration(r) },
    { key: 'where', header: 'Processing', cell: (r) => <LocalityChip provider={r.usesCloud ? 'cloud' : 'local'} /> },
  ];
  return (
    <DataTable
      caption={caption}
      columns={columns}
      rows={runs}
      rowKey={(r) => r.id}
      onRowClick={(r) => navigate(`/runs/${encodeURIComponent(r.id)}`)}
    />
  );
}
