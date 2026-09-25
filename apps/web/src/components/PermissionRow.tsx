import { ArrowUpRight, Database, Hand, HardDrive, Phone, Plug } from 'lucide-react';
import type { PermissionItem } from '../lib/permissions';
import { groupPermissions } from '../lib/permissions';

function iconFor(item: PermissionItem) {
  if (item.emphasis === 'cloud') return <ArrowUpRight size={20} aria-hidden="true" style={{ color: 'var(--color-cloud)' }} />;
  if (item.emphasis === 'phone') return <Phone size={20} aria-hidden="true" style={{ color: 'var(--color-warning)' }} />;
  switch (item.group) {
    case 'Local data':
      return <Database size={20} aria-hidden="true" />;
    case 'External services':
      return <Plug size={20} aria-hidden="true" />;
    case 'Model use':
      return <HardDrive size={20} aria-hidden="true" style={{ color: 'var(--color-local)' }} />;
    default:
      return <Hand size={20} aria-hidden="true" />;
  }
}

interface PermissionRowProps {
  item: PermissionItem;
  /** Approval control; omit for read-only display. */
  approved?: boolean;
  onApprove?: (approved: boolean) => void;
  changed?: boolean;
  disabled?: boolean;
}

/** Capability, plain-language impact, resource/provider and an approval control. */
export function PermissionRow({ item, approved, onApprove, changed = false, disabled = false }: PermissionRowProps) {
  const inputId = `perm-${item.id.replace(/[^a-z0-9]/gi, '-')}`;
  return (
    <li className="permission-row" data-emphasis={item.emphasis} data-changed={changed ? 'true' : undefined}>
      {iconFor(item)}
      <div className="stack-sm" style={{ gap: 2, minWidth: 0 }}>
        <span className="field-label">
          {item.capability}
          {changed ? <span className="badge tone-warning" style={{ marginLeft: 8 }}>New in this version</span> : null}
        </span>
        <span className="muted">{item.impact}</span>
        {item.resource ? (
          <span className="muted" style={{ fontSize: 12 }}>
            {item.emphasis === 'cloud' ? 'Destination' : 'Scope'}: {item.resource}
          </span>
        ) : null}
      </div>
      {onApprove ? (
        <label className="check-row" htmlFor={inputId} style={{ padding: 0 }}>
          <input
            id={inputId}
            type="checkbox"
            checked={!!approved}
            disabled={disabled}
            onChange={(e) => onApprove(e.target.checked)}
          />
          <span>
            Approve<span className="sr-only">: {item.capability}</span>
          </span>
        </label>
      ) : (
        <span className="badge tone-success">Granted</span>
      )}
    </li>
  );
}

interface PermissionListProps {
  items: PermissionItem[];
  approvals?: Record<string, boolean>;
  onApprove?: (id: string, approved: boolean) => void;
  changedIds?: Set<string>;
  disabled?: boolean;
}

/** Permissions grouped as Local data, External services, Model use, User interaction. */
export function PermissionList({ items, approvals, onApprove, changedIds, disabled }: PermissionListProps) {
  if (items.length === 0) {
    return <p className="muted">This agent requests no permissions beyond writing its own run events.</p>;
  }
  return (
    <div className="stack">
      {groupPermissions(items).map(({ group, items: groupItems }) => (
        <section key={group} className="permission-group" aria-label={group}>
          <h3>{group}</h3>
          <ul className="stack-sm" style={{ listStyle: 'none' }}>
            {groupItems.map((item) => (
              <PermissionRow
                key={item.id}
                item={item}
                approved={approvals?.[item.id]}
                onApprove={onApprove ? (v) => onApprove(item.id, v) : undefined}
                changed={changedIds?.has(item.id)}
                disabled={disabled}
              />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
