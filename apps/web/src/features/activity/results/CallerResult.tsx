import type { ReactNode } from 'react';
import { Banner } from '../../../components/Feedback';
import { DataTable, type Column } from '../../../components/DataTable';
import { StatusBadge } from '../../../components/StatusBadge';
import { formatTime, formatUtc } from '../../../lib/format';
import { CALL_STATUS, CONSENT_STATUS, SHEET_WRITE_STATUS, type CallStatus } from '../../../lib/status';

export interface CallerRow {
  row: number;
  name: string;
  phoneMasked: string;
  consent: 'validated' | 'skipped';
  skipReason: string | null;
  callStatus: CallStatus | null;
  transcript: string | null;
  sheetWrite: 'written' | 'pending_retry' | 'failed' | null;
  completedAt: string | null;
  error: string | null;
}

export interface CallerResultData {
  operatorDecision: 'approved' | 'cancelled' | 'not_required';
  summary: { called: number; answered: number; responsesCaptured: number; skipped: number; failed: number };
  rows: CallerRow[];
}

const CALL_STATES = new Set(Object.keys(CALL_STATUS));

function str(v: unknown): string | null {
  return typeof v === 'string' ? v : null;
}

export function parseCaller(result: Record<string, unknown> | null): CallerResultData | null {
  if (!result || !Array.isArray(result.rows) || typeof result.summary !== 'object' || result.summary === null) return null;
  const s = result.summary as Record<string, unknown>;
  const n = (v: unknown) => (typeof v === 'number' ? v : 0);
  const decision = result.operatorDecision;
  return {
    operatorDecision: decision === 'approved' || decision === 'cancelled' ? decision : 'not_required',
    summary: { called: n(s.called), answered: n(s.answered), responsesCaptured: n(s.responsesCaptured), skipped: n(s.skipped), failed: n(s.failed) },
    rows: result.rows.flatMap((raw) => {
      if (typeof raw !== 'object' || raw === null) return [];
      const r = raw as Record<string, unknown>;
      const call = str(r.callStatus);
      const sheet = str(r.sheetWrite);
      return [
        {
          row: typeof r.row === 'number' ? r.row : 0,
          name: str(r.name) ?? '',
          phoneMasked: str(r.phoneMasked) ?? '',
          consent: r.consent === 'validated' ? 'validated' : 'skipped',
          skipReason: str(r.skipReason),
          callStatus: call && CALL_STATES.has(call) ? (call as CallStatus) : null,
          transcript: str(r.transcript),
          sheetWrite: sheet === 'written' || sheet === 'pending_retry' || sheet === 'failed' ? sheet : null,
          completedAt: str(r.completedAt),
          error: str(r.error),
        },
      ];
    }),
  };
}

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

/**
 * Run summary plus a row table. No-answer is a call outcome, not an agent failure;
 * a pending Sheets write is retried without placing the call again.
 */
export function CallerResult({ data, timeZone }: { data: CallerResultData; timeZone: string }) {
  const columns: Column<CallerRow>[] = [
    {
      key: 'recipient',
      header: 'Recipient',
      primary: true,
      cell: (r) => (
        <span className="stack-sm" style={{ gap: 0 }}>
          <span className="break-anywhere">{r.name || `Row ${r.row}`}</span>
          <span className="mono muted">{r.phoneMasked}</span>
        </span>
      ),
      sortValue: (r) => r.row,
    },
    {
      key: 'consent',
      header: 'Consent',
      cell: (r) => (
        <span className="stack-sm" style={{ gap: 2 }}>
          <StatusBadge status={CONSENT_STATUS[r.consent]} />
          {r.consent === 'skipped' && r.skipReason ? <span className="muted">{r.skipReason}</span> : null}
        </span>
      ),
    },
    {
      key: 'call',
      header: 'Call state',
      cell: (r) =>
        r.consent === 'skipped' || (data.operatorDecision === 'cancelled' && !r.callStatus) ? (
          <span className="muted">Not called</span>
        ) : (
          <StatusBadge status={CALL_STATUS[r.callStatus ?? 'queued']} />
        ),
    },
    {
      key: 'response',
      header: 'Response',
      cell: (r) =>
        r.transcript ? (
          <q className="break-anywhere">{r.transcript}</q>
        ) : r.callStatus === 'answered_no_speech' || r.callStatus === 'answered_speech' ? (
          <span className="muted">No speech captured</span>
        ) : (
          <span className="muted">—</span>
        ),
    },
    {
      key: 'sheet',
      header: 'Sheet write',
      cell: (r) => (r.sheetWrite ? <StatusBadge status={SHEET_WRITE_STATUS[r.sheetWrite]} /> : <span className="muted">—</span>),
    },
    {
      key: 'time',
      header: 'Time',
      cell: (r) =>
        r.completedAt ? (
          <time dateTime={r.completedAt} title={formatUtc(r.completedAt)}>
            {formatTime(r.completedAt, timeZone)}
          </time>
        ) : (
          <span className="muted">—</span>
        ),
    },
  ];
  const pendingSheet = data.rows.some((r) => r.sheetWrite === 'pending_retry' || r.sheetWrite === 'failed');
  return (
    <div className="stack">
      {data.operatorDecision === 'cancelled' ? (
        <Banner tone="info" title="You cancelled this run before any calls">
          No calls were placed.
        </Banner>
      ) : null}
      <div className="stat-grid" role="group" aria-label="Call summary">
        <Stat label="Called" value={data.summary.called} />
        <Stat label="Answered" value={data.summary.answered} />
        <Stat label="Responses captured" value={data.summary.responsesCaptured} />
        <Stat label="Skipped" value={data.summary.skipped} />
        <Stat label="Failed" value={data.summary.failed} />
      </div>
      {pendingSheet ? (
        <Banner tone="warning" title="Some results are not in the sheet yet">
          Rows marked “Pending retry” are written again automatically. Retrying a sheet write never calls anyone again.
        </Banner>
      ) : null}
      <DataTable caption="Calls and responses" columns={columns} rows={data.rows} rowKey={(r) => String(r.row)} />
    </div>
  );
}
