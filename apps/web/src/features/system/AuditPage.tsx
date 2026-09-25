import { ScrollText } from 'lucide-react';
import { useId, useState } from 'react';
import { useAudit, type AuditFilters } from '../../api/queries';
import type { AuditEventOut } from '../../api/schema';
import { Button } from '../../components/Button';
import { DataTable, type Column } from '../../components/DataTable';
import { EmptyState, SkeletonTable } from '../../components/Feedback';
import { Page, PageHeader } from '../../components/Layout';
import { QueryView } from '../../components/QueryView';
import { StatusBadge } from '../../components/StatusBadge';
import { formatDateTime, formatUtc, shortId } from '../../lib/format';
import { useTimeZone } from '../common/useTimeZone';
import { SystemTabs } from './SystemTabs';

const CATEGORIES: [string, string][] = [
  ['', 'All events'],
  ['auth.*', 'Sign-in and sessions'],
  ['agent.*', 'Agent installs and changes'],
  ['permissions.*', 'Permission approvals'],
  ['run.*', 'Runs'],
  ['schedule.*', 'Schedules'],
  ['input.*', 'Crew Request answers'],
  ['connection.*', 'Connections and secrets'],
  ['model.*', 'Model residency'],
  ['cloud.*', 'Cloud calls'],
  ['callback.*', 'Callback validation'],
  ['settings.*', 'Settings'],
  ['chat.*', 'Chat'],
];

const OUTCOME = {
  success: { label: 'Succeeded', tone: 'success', icon: 'check' },
  denied: { label: 'Denied', tone: 'warning', icon: 'ban' },
  failure: { label: 'Failed', tone: 'danger', icon: 'x' },
} as const;

/** Metadata only; values are rendered as plain text and long values are shortened. */
function metadataText(meta: Record<string, unknown>): string {
  return Object.entries(meta)
    .map(([k, v]) => {
      const value = typeof v === 'string' ? v : JSON.stringify(v);
      return `${k}=${value && value.length > 60 ? `${value.slice(0, 57)}…` : value}`;
    })
    .join(' · ');
}

export default function AuditPage() {
  const timeZone = useTimeZone();
  const [category, setCategory] = useState('');
  const [outcome, setOutcome] = useState<AuditFilters['outcome'] | ''>('');
  const [cursors, setCursors] = useState<string[]>([]);
  const filters: AuditFilters = { action: category || undefined, outcome: outcome || undefined, cursor: cursors[cursors.length - 1] };
  const audit = useAudit(filters);
  const ids = { category: useId(), outcome: useId() };

  const columns: Column<AuditEventOut>[] = [
    { key: 'time', header: 'Time', cell: (e) => <span title={formatUtc(e.createdAt)}>{formatDateTime(e.createdAt, timeZone)}</span> },
    { key: 'action', header: 'Action', primary: true, cell: (e) => <span className="mono">{e.action}</span> },
    { key: 'actor', header: 'Actor', cell: (e) => `${e.actorType}${e.actorId ? ` ${shortId(e.actorId)}` : ''}` },
    { key: 'target', header: 'Target', cell: (e) => (e.targetType ? `${e.targetType}${e.targetId ? ` ${shortId(e.targetId)}` : ''}` : '—') },
    {
      key: 'outcome',
      header: 'Outcome',
      cell: (e) => <StatusBadge status={OUTCOME[e.outcome as keyof typeof OUTCOME] ?? { label: e.outcome, tone: 'neutral', icon: 'info' }} />,
    },
    { key: 'meta', header: 'Details', cell: (e) => <span className="muted break-anywhere">{metadataText(e.metadata) || '—'}</span> },
  ];

  return (
    <Page>
      <PageHeader title="Audit" purpose="Security-relevant history. Shows metadata only, never secret values or full prompts." />
      <SystemTabs />
      <div className="row" style={{ alignItems: 'flex-end' }}>
        <div className="field" style={{ minWidth: 220 }}>
          <label className="field-label" htmlFor={ids.category}>
            Category
          </label>
          <select
            id={ids.category}
            className="select"
            value={category}
            onChange={(e) => {
              setCategory(e.target.value);
              setCursors([]);
            }}
          >
            {CATEGORIES.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </div>
        <div className="field" style={{ minWidth: 160 }}>
          <label className="field-label" htmlFor={ids.outcome}>
            Outcome
          </label>
          <select
            id={ids.outcome}
            className="select"
            value={outcome}
            onChange={(e) => {
              setOutcome(e.target.value as AuditFilters['outcome'] | '');
              setCursors([]);
            }}
          >
            <option value="">Any</option>
            <option value="success">Succeeded</option>
            <option value="denied">Denied</option>
            <option value="failure">Failed</option>
          </select>
        </div>
      </div>
      <QueryView
        query={audit}
        errorTitle="Could not load audit history"
        loading={<SkeletonTable label="Loading audit events" />}
        isEmpty={(d) => d.items.length === 0}
        empty={<EmptyState icon={ScrollText} title="No matching events">Try another category or outcome.</EmptyState>}
      >
        {(page) => (
          <div className="stack">
            <DataTable caption="Audit events, newest first" columns={columns} rows={page.items} rowKey={(e) => e.id} />
            <div className="row">
              {cursors.length > 0 ? <Button onClick={() => setCursors((c) => c.slice(0, -1))}>Newer events</Button> : null}
              {page.nextCursor ? <Button onClick={() => setCursors((c) => [...c, page.nextCursor ?? ''])}>Older events</Button> : null}
            </div>
          </div>
        )}
      </QueryView>
    </Page>
  );
}
