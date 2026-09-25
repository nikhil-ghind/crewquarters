import { Loader2 } from 'lucide-react';
import { useEffect, useId, useState } from 'react';
import { formatDuration, secondsBetween } from '../lib/format';

interface ResourceMeterProps {
  label: string;
  value: number;
  max: number;
  /** Text equivalent, e.g. "42.0 GiB of 128.0 GiB". */
  valueText: string;
  /** Thresholds come from the API when available (fractions of max). */
  warnAt?: number;
  dangerAt?: number;
  hideLabel?: boolean;
  compact?: boolean;
}

/** Numeric value, units, accessible meter semantics and a visible text equivalent. */
export function ResourceMeter({
  label,
  value,
  max,
  valueText,
  warnAt,
  dangerAt,
  hideLabel = false,
  compact = false,
}: ResourceMeterProps) {
  const id = useId();
  const ratio = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0;
  const tone =
    dangerAt !== undefined && ratio >= dangerAt
      ? 'tone-danger-fill'
      : warnAt !== undefined && ratio >= warnAt
        ? 'tone-warning-fill'
        : '';
  return (
    <div className="meter">
      <div className={hideLabel ? 'sr-only' : 'meter-label'}>
        <span id={id}>{label}</span>
        {!compact ? <span className="muted">{valueText}</span> : null}
      </div>
      <div
        className="meter-track"
        role="meter"
        aria-labelledby={id}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuenow={value}
        aria-valuetext={valueText}
      >
        <div className={`meter-fill ${tone}`} style={{ width: `${ratio * 100}%` }} />
      </div>
      {compact ? <span className="muted" style={{ fontSize: 12 }}>{valueText}</span> : null}
    </div>
  );
}

export interface MeterSegment {
  label: string;
  value: number;
  className: string;
}

/** Stacked breakdown (e.g. unified memory: reserve / models / agents / available). */
export function SegmentedMeter({ label, segments, max }: { label: string; segments: MeterSegment[]; max: number }) {
  const id = useId();
  const used = segments.reduce((sum, s) => sum + s.value, 0);
  return (
    <div className="meter">
      <span id={id} className="sr-only">
        {label}
      </span>
      <div
        className="meter-track"
        role="meter"
        aria-labelledby={id}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuenow={used}
        aria-valuetext={segments.map((s) => `${s.label}`).join(', ')}
      >
        {segments.map((s) => (
          <div
            key={s.label}
            className={`meter-fill ${s.className}`}
            style={{ width: `${max > 0 ? (s.value / max) * 100 : 0}%` }}
            title={s.label}
          />
        ))}
      </div>
    </div>
  );
}

function useElapsed(since: string | null | undefined): number | null {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!since) return;
    const t = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => window.clearInterval(t);
  }, [since]);
  return secondsBetween(since);
}

export function Elapsed({ since, until }: { since: string | null | undefined; until?: string | null }) {
  const live = useElapsed(until ? null : since);
  const seconds = until ? secondsBetween(since, until) : live;
  return <span className="nowrap">{formatDuration(seconds)}</span>;
}

interface ProgressProps {
  label: string;
  /** Real percentage only; omit when none exists (section 13.15). */
  percent?: number | null;
  /** Current stage, e.g. "Loading weights". */
  stage?: string | null;
  /** Start of the current operation, for elapsed time when there is no percentage. */
  since?: string | null;
  detail?: string | null;
}

/** Determinate only with a real percentage; otherwise stage + elapsed time. */
export function Progress({ label, percent, stage, since, detail }: ProgressProps) {
  const determinate = typeof percent === 'number' && Number.isFinite(percent);
  if (determinate) {
    const value = Math.min(100, Math.max(0, percent));
    return (
      <div className="progress">
        <div className="row-between">
          <span className="field-label">{label}</span>
          <span className="muted">{value.toFixed(0)}%</span>
        </div>
        <div
          className="progress-track"
          role="progressbar"
          aria-label={label}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(value)}
          aria-valuetext={`${value.toFixed(0)}%${stage ? `, ${stage}` : ''}`}
        >
          <div className="progress-fill" style={{ width: `${value}%` }} />
        </div>
        {stage || detail ? (
          <span className="muted">{[stage, detail].filter(Boolean).join(' · ')}</span>
        ) : null}
      </div>
    );
  }
  return (
    <div className="progress" role="status" aria-label={label}>
      <div className="progress-stage">
        <Loader2 size={16} className="spin" aria-hidden="true" />
        <span className="field-label">{stage || label}</span>
        {since ? (
          <span className="muted">
            · <Elapsed since={since} /> elapsed
          </span>
        ) : null}
      </div>
      {detail ? <span className="muted">{detail}</span> : null}
    </div>
  );
}
