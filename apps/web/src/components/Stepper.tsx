import { Ban, Check } from 'lucide-react';
import type { ReactNode } from 'react';
import { STEP_STATUS, type StepState, type Tone } from '../lib/status';
import { StatusIconGlyph } from './StatusBadge';

export interface StepItem {
  id: string;
  label: string;
  state: StepState;
  meta?: string;
}

interface StepperProps {
  steps: StepItem[];
  label: string;
  /** Allow jumping back to completed/optional steps. */
  onSelect?: (id: string) => void;
  compactOnMobile?: boolean;
}

/**
 * Ordered list with aria-current="step". Each step states its status in words for
 * screen readers (Completed, Current step, Optional, Blocked).
 */
export function Stepper({ steps, label, onSelect, compactOnMobile = true }: StepperProps) {
  return (
    <nav aria-label={label}>
      <ol className={`stepper${compactOnMobile ? ' stepper-compact' : ''}`}>
        {steps.map((step, index) => {
          const spec = STEP_STATUS[step.state];
          const clickable =
            !!onSelect && (step.state === 'completed' || step.state === 'optional' || step.state === 'current');
          return (
            <li
              key={step.id}
              className="stepper-item"
              data-state={step.state}
              aria-current={step.state === 'current' ? 'step' : undefined}
            >
              <span className="stepper-marker" aria-hidden="true">
                {step.state === 'completed' ? (
                  <Check size={16} />
                ) : step.state === 'blocked' ? (
                  <Ban size={14} />
                ) : (
                  index + 1
                )}
              </span>
              <span className="stepper-text stack-sm" style={{ gap: 0 }}>
                <button
                  type="button"
                  className="stepper-link"
                  disabled={!clickable}
                  onClick={() => onSelect?.(step.id)}
                >
                  {step.label}
                  <span className="sr-only"> ({spec.label})</span>
                </button>
                <span className="stepper-meta" aria-hidden="true">
                  {step.meta ?? (step.state === 'upcoming' ? '' : spec.label)}
                </span>
              </span>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

export interface TimelineEntry {
  key: string | number;
  title: ReactNode;
  detail?: ReactNode;
  time?: string;
  timeTitle?: string;
  tone: Tone;
  icon: Parameters<typeof StatusIconGlyph>[0]['icon'];
  current?: boolean;
}

/** Ordered structured events; the current step is emphasized. */
export function Timeline({ entries, label }: { entries: TimelineEntry[]; label: string }) {
  return (
    <ol className="timeline" aria-label={label}>
      {entries.map((entry) => (
        <li
          key={entry.key}
          className="timeline-item"
          data-current={entry.current ? 'true' : undefined}
          aria-current={entry.current ? 'step' : undefined}
        >
          <span className={`timeline-dot tone-${entry.tone}`} aria-hidden="true">
            <StatusIconGlyph icon={entry.icon} size={12} />
          </span>
          <div className="stack-sm" style={{ gap: 2, minWidth: 0 }}>
            <div className="row-between" style={{ flexWrap: 'wrap' }}>
              <span className="timeline-title break-anywhere">{entry.title}</span>
              {entry.time ? (
                <time className="timeline-time" title={entry.timeTitle}>
                  {entry.time}
                </time>
              ) : null}
            </div>
            {entry.detail ? <div className="muted break-anywhere">{entry.detail}</div> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}
