import { AlertOctagon, AlertTriangle, Check, Copy, Info, RotateCw, type LucideIcon } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { remediation, toApiError } from '../api/errors';
import type { Tone } from '../lib/status';
import { Button } from './Button';
import { Advanced } from './Layout';

const BANNER_ICONS: Record<Tone, LucideIcon> = {
  neutral: Info,
  info: Info,
  success: Check,
  warning: AlertTriangle,
  danger: AlertOctagon,
};

interface BannerProps {
  tone: Tone;
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  /** "alert" only for blocking failures; others are polite status. */
  role?: 'status' | 'alert' | 'none';
  className?: string;
}

export function Banner({ tone, title, children, action, role = 'status', className }: BannerProps) {
  const Icon = BANNER_ICONS[tone];
  return (
    <div
      className={`banner tone-${tone} ${className ?? ''}`}
      role={role === 'none' ? undefined : role}
    >
      <Icon size={20} aria-hidden="true" style={{ flex: 'none', marginTop: 1 }} />
      <div className="banner-body stack-sm" style={{ gap: 2 }}>
        {title ? <p className="banner-title">{title}</p> : null}
        {children ? <div>{children}</div> : null}
      </div>
      {action ? <div className="row">{action}</div> : null}
    </div>
  );
}

interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  /** Why it is empty. */
  children?: ReactNode;
  /** One next action. */
  action?: ReactNode;
}

export function EmptyState({ icon: Icon = Info, title, children, action }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <Icon size={24} className="empty-icon" aria-hidden="true" />
      <h3>{title}</h3>
      {children ? <p className="muted">{children}</p> : null}
      {action ? <div className="row">{action}</div> : null}
    </div>
  );
}

export function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="tertiary"
      icon={copied ? <Check size={16} aria-hidden="true" /> : <Copy size={16} aria-hidden="true" />}
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(
          () => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 2000);
          },
          () => setCopied(false),
        );
      }}
    >
      {copied ? 'Copied' : label}
    </Button>
  );
}

interface ErrorPanelProps {
  error: unknown;
  /** What was being attempted, e.g. "Could not load runs". */
  title?: string;
  onRetry?: () => void;
  retryLabel?: string;
}

/** What happened, user action, retry, diagnostic code, Advanced detail (section 13.15). */
export function ErrorPanel({ error, title = 'Something went wrong', onRetry, retryLabel = 'Try again' }: ErrorPanelProps) {
  const apiError = toApiError(error);
  const code = apiError.requestId ? `${apiError.code} · ${apiError.requestId}` : apiError.code;
  return (
    <div className="error-panel" role="alert">
      <p className="error-panel-title">
        <AlertOctagon size={18} aria-hidden="true" />
        {title}
      </p>
      <p>{remediation(apiError)}</p>
      <div className="row">
        {onRetry ? (
          <Button icon={<RotateCw size={16} aria-hidden="true" />} onClick={onRetry}>
            {retryLabel}
          </Button>
        ) : null}
        <span className="diag-code">
          Diagnostic code: <span>{code}</span>
        </span>
        <CopyButton text={code} label="Copy code" />
      </div>
      {apiError.message !== remediation(apiError) || Object.keys(apiError.details).length > 0 ? (
        <Advanced label="Technical detail">
          <p className="break-anywhere">{apiError.message}</p>
          {Object.keys(apiError.details).length > 0 ? (
            <pre className="raw-json mono" tabIndex={0}>
              {JSON.stringify(apiError.details, null, 2)}
            </pre>
          ) : null}
        </Advanced>
      ) : null}
    </div>
  );
}

export function Skeleton({ width = '100%', height = 16 }: { width?: number | string; height?: number }) {
  return <span className="skeleton" style={{ width, height }} aria-hidden="true" />;
}

/** Skeleton that matches a card/table layout; used only during initial load. */
export function SkeletonBlock({ lines = 3, label = 'Loading' }: { lines?: number; label?: string }) {
  return (
    <div className="card stack-sm" role="status" aria-live="polite">
      <span className="sr-only">{label}…</span>
      <Skeleton width="40%" height={20} />
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} width={`${90 - i * 12}%`} />
      ))}
    </div>
  );
}

export function SkeletonTable({ rows = 4, label = 'Loading' }: { rows?: number; label?: string }) {
  return (
    <div className="table-wrap" role="status" aria-live="polite">
      <span className="sr-only">{label}…</span>
      <div className="stack-sm" style={{ padding: 16 }}>
        <Skeleton height={20} />
        {Array.from({ length: rows }, (_, i) => (
          <Skeleton key={i} height={28} />
        ))}
      </div>
    </div>
  );
}
