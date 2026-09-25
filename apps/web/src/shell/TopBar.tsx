import { useQueryClient } from '@tanstack/react-query';
import { ArrowUpRight, Bell, ChevronDown, Cpu, LogOut, Menu, Settings, UserRound } from 'lucide-react';
import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router';
import { api, mutate } from '../api/client';
import { useConnectivity } from '../api/connectivity';
import { useAttention, useMemory, useModels, useRuns, useSystemStatus } from '../api/queries';
import { session } from '../api/session';
import { SegmentedMeter, ResourceMeter } from '../components/Meters';
import { StatusBadge } from '../components/StatusBadge';
import { formatBytes } from '../lib/format';
import { DEVICE_STATUS } from '../lib/status';
import { useMe } from './AuthGate';
import type { MemoryOut } from '../api/schema';

/** Non-modal popover: closes on Escape, outside click, or focus leaving it. */
function Popover({
  label,
  trigger,
  children,
  className,
}: {
  label: string;
  trigger: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);
  return (
    <div
      className="topbar-item"
      ref={ref}
      onBlur={(e) => {
        if (open && ref.current && !ref.current.contains(e.relatedTarget)) setOpen(false);
      }}
    >
      <button
        ref={buttonRef}
        type="button"
        className="topbar-button"
        aria-expanded={open}
        aria-controls={id}
        aria-label={label}
        onClick={() => setOpen((o) => !o)}
      >
        {trigger}
      </button>
      {open ? (
        <div id={id} className={`popover ${className ?? ''}`} role="region" aria-label={label}>
          {children}
        </div>
      ) : null}
    </div>
  );
}

export function memoryUsed(m: MemoryOut): { used: number; total: number } | null {
  if (m.totalBytes === null || m.availableBytes === null || m.totalBytes <= 0) return null;
  return { used: m.totalBytes - m.availableBytes, total: m.totalBytes };
}

function MemoryBreakdown({ memory, activeAgents }: { memory: MemoryOut; activeAgents: number }) {
  const total = memory.totalBytes ?? 0;
  const available = memory.availableBytes ?? 0;
  const reserve = memory.systemReserveBytes;
  const models = memory.reservedBytes;
  const other = Math.max(0, total - available - models);
  return (
    <div className="stack-sm">
      <h2 style={{ fontSize: 16, lineHeight: '24px' }}>Unified memory</h2>
      {total > 0 ? (
        <SegmentedMeter
          label="Unified memory breakdown"
          max={total}
          segments={[
            { label: `Loaded models ${formatBytes(models)}`, value: models, className: 'segment-models' },
            { label: `System and agents ${formatBytes(other)}`, value: other, className: 'segment-reserve' },
          ]}
        />
      ) : null}
      <dl className="kv">
        <dt>System reserve</dt>
        <dd>{formatBytes(reserve)} kept for the OS, database and agents</dd>
        <dt>Loaded models</dt>
        <dd>{formatBytes(models)} reserved</dd>
        <dt>Active agents</dt>
        <dd>
          {activeAgents} running{activeAgents > 0 ? ' (within the system reserve)' : ''}
        </dd>
        <dt>Available now</dt>
        <dd>{memory.availableBytes === null ? 'Not reported' : formatBytes(available)}</dd>
        <dt>Model serving cap</dt>
        <dd>{formatBytes(memory.maxServingBytes)}</dd>
      </dl>
      <p className="muted" style={{ fontSize: 12 }}>
        Not all memory can hold model weights: the system reserve and a safety margin of{' '}
        {formatBytes(memory.safetyMarginBytes)} per load stay free.
      </p>
      <Link to="/models">Manage models</Link>
    </div>
  );
}

export function TopBar({ onOpenNav }: { onOpenNav: () => void }) {
  const status = useSystemStatus();
  const memory = useMemory();
  const models = useModels();
  const attention = useAttention();
  const active = useRuns({ state: ['PREPARING', 'LOADING_MODEL', 'RUNNING', 'WAITING_INPUT'], limit: 50 });
  const { api: apiState } = useConnectivity();
  const { query: me } = useMe();
  const navigate = useNavigate();
  const client = useQueryClient();

  const reachability = apiState === 'offline' ? 'offline' : (status.data?.status ?? null);
  const resident = models.data?.find((m) => m.memoryState === 'READY' || m.memoryState === 'LOADING');
  const activeRuns = active.data?.items ?? [];
  const cloudActive = activeRuns.some((r) => r.usesCloud);
  const count = attention.data?.count ?? 0;
  const used = memory.data ? memoryUsed(memory.data) : null;

  const signOut = async () => {
    try {
      await mutate(api.DELETE('/api/v1/sessions/current'));
    } finally {
      session.anonymous();
      client.clear();
      void navigate('/login', { replace: true });
    }
  };

  return (
    <header className="topbar">
      <button type="button" className="btn btn-tertiary btn-icon mobile-only" aria-label="Open navigation" onClick={onOpenNav}>
        <Menu size={20} aria-hidden="true" />
      </button>
      {reachability ? (
        <Link to="/system/status" className="topbar-button" style={{ textDecoration: 'none' }}>
          <StatusBadge status={DEVICE_STATUS[reachability]} context="Device" />
        </Link>
      ) : null}
      {memory.data && used ? (
        <Popover
          label="Device memory"
          className=""
          trigger={
            <span className="topbar-memory">
              <ResourceMeter
                label="Unified memory"
                value={used.used}
                max={used.total}
                valueText={`${formatBytes(used.used)} of ${formatBytes(used.total)} used`}
                warnAt={0.85}
                dangerAt={0.95}
                compact
                hideLabel
              />
            </span>
          }
        >
          <MemoryBreakdown memory={memory.data} activeAgents={activeRuns.length} />
        </Popover>
      ) : null}
      <Link to={resident ? `/models/${encodeURIComponent(resident.id)}` : '/models'} className="topbar-button topbar-hide-mobile" style={{ textDecoration: 'none' }}>
        <Cpu size={16} aria-hidden="true" />
        <span className="topbar-hide-medium">
          {resident ? (
            <>
              {resident.displayName}
              <span className="muted"> · {resident.memoryState === 'READY' ? 'Ready' : 'Loading'}</span>
            </>
          ) : (
            'No model active'
          )}
        </span>
        <span className="sr-only">{resident ? '' : ''}</span>
      </Link>
      {cloudActive ? (
        <span className="badge chip-cloud topbar-hide-mobile" role="status">
          <ArrowUpRight size={14} aria-hidden="true" />
          Cloud request running
        </span>
      ) : null}
      <span className="topbar-spacer" />
      <Link
        to="/activity/approvals"
        className="topbar-button"
        style={{ textDecoration: 'none' }}
        aria-label={count > 0 ? `${count} Crew ${count === 1 ? 'Request needs' : 'Requests need'} you` : 'Crew Requests: nothing pending'}
      >
        <Bell size={18} aria-hidden="true" />
        {count > 0 ? <span className="count-badge">{count}</span> : null}
      </Link>
      <Popover
        label="Owner menu"
        className="owner-menu"
        trigger={
          <>
            <UserRound size={18} aria-hidden="true" />
            <span className="topbar-hide-mobile">{me.data?.user.username ?? 'Owner'}</span>
            <ChevronDown size={14} aria-hidden="true" />
          </>
        }
      >
        <p className="muted">Signed in as {me.data?.user.username ?? 'owner'}</p>
        <ul className="menu-list">
          <li>
            <Link className="menu-item" to="/system/settings">
              <Settings size={16} aria-hidden="true" /> Settings
            </Link>
          </li>
          <li>
            <button type="button" className="menu-item" onClick={() => void signOut()}>
              <LogOut size={16} aria-hidden="true" /> Sign out
            </button>
          </li>
        </ul>
      </Popover>
    </header>
  );
}
