import { ChevronRight } from 'lucide-react';
import type { StatusCheck } from '../../api/schema';
import { StatusBadge } from '../../components/StatusBadge';
import { formatRelative, formatUtc } from '../../lib/format';
import { CHECK_STATUS, type CheckState } from '../../lib/status';

export interface CheckRow {
  id: string;
  name: string;
  state: CheckState;
  explanation: string;
  technical?: string;
  remediation?: string;
  checkedAt?: string;
}

const NAMES: Record<string, string> = {
  architecture: 'CPU architecture',
  'appliance architecture': 'Appliance architecture (arm64)',
  postgresql: 'Database',
  pgvector: 'Vector search extension',
  disk: 'Free disk space',
  'runtime daemon': 'Agent runtime',
  'nvidia container runtime': 'NVIDIA container runtime',
  gpu: 'GPU',
  database: 'Database',
  migrations: 'Database migrations',
};

const REMEDIATION: Record<string, string> = {
  'appliance architecture': 'The DGX appliance profile needs an arm64 device. Development profiles may continue.',
  pgvector: 'Reinstall the platform services so the database migration can enable the vector extension.',
  disk: 'Free up disk space or move the data directory to a larger disk.',
  'runtime daemon': 'Restart the Crewquarters runtime service from the device menu, then retry.',
  'nvidia container runtime': 'Install the NVIDIA Container Toolkit, then retry.',
  gpu: 'Check that the GPU driver is installed and the device is detected.',
  postgresql: 'Restart the platform services, then retry.',
};

export function fromStatusCheck(check: StatusCheck): CheckRow {
  return {
    id: `${check.group}:${check.name}`,
    name: NAMES[check.name] ?? check.name.charAt(0).toUpperCase() + check.name.slice(1),
    state: check.status,
    explanation: check.detail,
    technical: `${check.group} / ${check.name}: ${check.detail}`,
    remediation: check.status === 'passed' ? undefined : REMEDIATION[check.name],
    checkedAt: check.checkedAt,
  };
}

/** A checklist rather than a spinner: each row has state, explanation and technical detail. */
export function CheckList({ rows, label }: { rows: CheckRow[]; label: string }) {
  return (
    <ul className="stack-sm" style={{ listStyle: 'none' }} aria-label={label}>
      {rows.map((row) => (
        <li key={row.id} className="card card-compact stack-sm">
          <div className="row-between">
            <span className="field-label">{row.name}</span>
            <StatusBadge status={CHECK_STATUS[row.state]} context={row.name} />
          </div>
          <span className="muted break-anywhere">{row.explanation}</span>
          {row.remediation ? <span>{row.remediation}</span> : null}
          {row.technical || row.checkedAt ? (
            <details>
              <summary className="row" style={{ cursor: 'pointer', minHeight: 32 }}>
                <ChevronRight size={14} aria-hidden="true" />
                Technical result
              </summary>
              <p className="mono muted break-anywhere">{row.technical}</p>
              {row.checkedAt ? (
                <p className="muted" title={formatUtc(row.checkedAt)}>
                  Checked {formatRelative(row.checkedAt)}
                </p>
              ) : null}
            </details>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
