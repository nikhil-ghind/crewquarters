import {
  AlertTriangle,
  Ban,
  Check,
  Clock,
  Cpu,
  Download,
  Hand,
  HardDrive,
  Info,
  Loader2,
  Minus,
  Pause,
  Play,
  Plug,
  Unplug,
  X,
  type LucideIcon,
} from 'lucide-react';
import type { StatusIcon, StatusSpec, Tone } from '../lib/status';

const ICONS: Record<StatusIcon, LucideIcon> = {
  clock: Clock,
  spinner: Loader2,
  cpu: Cpu,
  play: Play,
  hand: Hand,
  check: Check,
  x: X,
  ban: Ban,
  alert: AlertTriangle,
  pause: Pause,
  download: Download,
  disk: HardDrive,
  plug: Plug,
  unplug: Unplug,
  info: Info,
  minus: Minus,
};

export function StatusIconGlyph({ icon, size = 16 }: { icon: StatusIcon; size?: number }) {
  const Icon = ICONS[icon];
  return <Icon size={size} aria-hidden="true" className={icon === 'spinner' ? 'spin' : undefined} />;
}

export function toneClass(tone: Tone): string {
  return `tone-${tone}`;
}

interface StatusBadgeProps {
  status: StatusSpec;
  /** Optional prefix for screen readers, e.g. "Run state". */
  context?: string;
  title?: string;
}

/** Text + icon + semantic tone, from the shared vocabulary in lib/status.ts. */
export function StatusBadge({ status, context, title }: StatusBadgeProps) {
  return (
    <span className={`badge ${toneClass(status.tone)}`} title={title ?? status.hint}>
      <StatusIconGlyph icon={status.icon} size={14} />
      {context ? <span className="sr-only">{context}: </span> : null}
      <span>{status.label}</span>
    </span>
  );
}
