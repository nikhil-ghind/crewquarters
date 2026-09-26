/**
 * The stable status vocabulary (PLAN.md sections 13.7, 13.8, 13.10, 13.3, 13.4, 13.12).
 * Every screen renders statuses through these maps so the same backend condition always
 * reads the same way, with an icon and a tone; color never carries meaning alone.
 * Do not add labels outside this file.
 */
import type {
  BackupStatus,
  ConnectionStatus,
  DocumentState,
  InputRequestOut,
  ModelOut,
  ReadinessCheck,
  RunState,
  StatusCheck,
  SystemStatusOut,
} from '../api/schema';

export type Tone = 'neutral' | 'info' | 'success' | 'warning' | 'danger';

export type StatusIcon =
  | 'clock'
  | 'spinner'
  | 'cpu'
  | 'play'
  | 'hand'
  | 'check'
  | 'x'
  | 'ban'
  | 'alert'
  | 'pause'
  | 'download'
  | 'disk'
  | 'plug'
  | 'unplug'
  | 'info'
  | 'minus';

export interface StatusSpec {
  label: string;
  tone: Tone;
  icon: StatusIcon;
  /** Longer explanation for tooltips/help. */
  hint?: string;
}

export const RUN_STATUS: Record<RunState, StatusSpec> = {
  QUEUED: {
    label: 'Waiting to start',
    tone: 'neutral',
    icon: 'clock',
    hint: 'The run is queued and starts when a worker is free.',
  },
  PREPARING: { label: 'Preparing agent', tone: 'info', icon: 'spinner' },
  LOADING_MODEL: {
    label: 'Loading local model',
    tone: 'info',
    icon: 'cpu',
    hint: 'The run holds a model lease and waits for the model to be ready.',
  },
  RUNNING: { label: 'Running', tone: 'info', icon: 'play' },
  WAITING_INPUT: { label: 'Needs your input', tone: 'warning', icon: 'hand' },
  CANCELLING: { label: 'Cancelling', tone: 'neutral', icon: 'spinner' },
  SUCCEEDED: { label: 'Completed', tone: 'success', icon: 'check' },
  FAILED: { label: 'Failed', tone: 'danger', icon: 'x' },
  CANCELLED: { label: 'Cancelled', tone: 'neutral', icon: 'ban' },
  INTERRUPTED: {
    label: 'Interrupted',
    tone: 'warning',
    icon: 'alert',
    hint: 'The device restarted or the agent container was lost.',
  },
};

export const ACTIVE_RUN_STATES: readonly RunState[] = [
  'QUEUED',
  'PREPARING',
  'LOADING_MODEL',
  'RUNNING',
  'WAITING_INPUT',
  'CANCELLING',
];

type DownloadState = ModelOut['downloadState'];
type MemoryState = ModelOut['memoryState'];

/** Disk installation: never combined with memory residency into one "Active" label. */
export const MODEL_DOWNLOAD_STATUS: Record<DownloadState, StatusSpec> = {
  NOT_INSTALLED: { label: 'Not installed', tone: 'neutral', icon: 'minus' },
  DOWNLOADING: { label: 'Downloading', tone: 'info', icon: 'download' },
  INSTALLED: { label: 'Installed on disk', tone: 'success', icon: 'disk' },
  DOWNLOAD_ERROR: { label: 'Error', tone: 'danger', icon: 'x' },
  DELETING: { label: 'Deleting', tone: 'neutral', icon: 'spinner' },
};

export const MODEL_MEMORY_STATUS: Record<MemoryState, StatusSpec> = {
  NOT_LOADED: { label: 'Not loaded', tone: 'neutral', icon: 'minus' },
  LOADING: { label: 'Loading', tone: 'info', icon: 'spinner' },
  READY: { label: 'Ready', tone: 'success', icon: 'check' },
  DRAINING: { label: 'Draining', tone: 'warning', icon: 'pause' },
  LOAD_ERROR: { label: 'Error', tone: 'danger', icon: 'x' },
  ERROR: { label: 'Error', tone: 'danger', icon: 'x' },
};

export const CONNECTION_STATUS: Record<ConnectionStatus, StatusSpec> = {
  NOT_CONNECTED: { label: 'Not connected', tone: 'neutral', icon: 'unplug' },
  CONNECTED: { label: 'Connected', tone: 'success', icon: 'plug' },
  NEEDS_ATTENTION: { label: 'Needs attention', tone: 'warning', icon: 'alert' },
  DISABLED: { label: 'Disabled', tone: 'neutral', icon: 'ban' },
  UNKNOWN: {
    label: 'Cannot check',
    tone: 'warning',
    icon: 'alert',
    hint: 'The connection service is not responding, so the status is unknown.',
  },
};

/** A saved cloud key that has not been tested yet. */
export const PROFILE_UNTESTED: StatusSpec = { label: 'Not tested yet', tone: 'neutral', icon: 'minus' };

export const DEVICE_STATUS: Record<SystemStatusOut['status'], StatusSpec> = {
  healthy: { label: 'Healthy', tone: 'success', icon: 'check' },
  degraded: { label: 'Degraded', tone: 'warning', icon: 'alert' },
  offline: { label: 'Offline', tone: 'danger', icon: 'x' },
};

export type CheckState = 'checking' | StatusCheck['status'];

/** Preflight and System status checks (section 13.4). */
export const CHECK_STATUS: Record<CheckState, StatusSpec> = {
  checking: { label: 'Checking', tone: 'info', icon: 'spinner' },
  passed: { label: 'Passed', tone: 'success', icon: 'check' },
  warning: { label: 'Warning', tone: 'warning', icon: 'alert' },
  failed: { label: 'Failed', tone: 'danger', icon: 'x' },
};

export const INPUT_STATUS: Record<InputRequestOut['state'], StatusSpec> = {
  pending: { label: 'Needs your input', tone: 'warning', icon: 'hand' },
  answered: { label: 'Answer submitted', tone: 'success', icon: 'check' },
  cancelled: { label: 'Cancelled', tone: 'neutral', icon: 'ban' },
  expired: { label: 'Expired', tone: 'danger', icon: 'clock' },
};

export const READINESS_STATUS: Record<ReadinessCheck['status'], StatusSpec> = {
  ok: { label: 'Ready', tone: 'success', icon: 'check' },
  missing: { label: 'Missing', tone: 'danger', icon: 'x' },
  needs_attention: { label: 'Needs attention', tone: 'warning', icon: 'alert' },
};

export const READINESS_NAMES: Record<ReadinessCheck['name'], string> = {
  enabled: 'Enabled',
  permissions: 'Permissions',
  configuration: 'Configuration',
  architecture: 'Architecture',
  connection: 'Connections',
  model: 'Model',
};

export const DOCUMENT_STATUS: Record<DocumentState, StatusSpec> = {
  PENDING: { label: 'Waiting to start', tone: 'neutral', icon: 'clock' },
  PROCESSING: { label: 'Indexing', tone: 'info', icon: 'spinner' },
  READY: { label: 'Indexed', tone: 'success', icon: 'check' },
  FAILED: { label: 'Failed', tone: 'danger', icon: 'x' },
};

export const BACKUP_STATUS: Record<BackupStatus, StatusSpec> = {
  queued: { label: 'Waiting to start', tone: 'neutral', icon: 'clock' },
  running: { label: 'Backing up', tone: 'info', icon: 'spinner' },
  succeeded: { label: 'Completed', tone: 'success', icon: 'check' },
  failed: { label: 'Failed', tone: 'danger', icon: 'x' },
};

export const SCHEDULE_STATUS = {
  enabled: { label: 'Enabled', tone: 'success', icon: 'check' },
  disabled: { label: 'Disabled', tone: 'neutral', icon: 'ban' },
} as const satisfies Record<string, StatusSpec>;

export const AGENT_STATUS = {
  ready: { label: 'Ready', tone: 'success', icon: 'check' },
  notReady: { label: 'Needs attention', tone: 'warning', icon: 'alert' },
  disabled: { label: 'Disabled', tone: 'neutral', icon: 'ban' },
  reapproval: { label: 'Needs reapproval', tone: 'warning', icon: 'alert' },
} as const satisfies Record<string, StatusSpec>;

/** Caller result call states (section 13.12). */
export type CallStatus =
  | 'answered_speech'
  | 'answered_no_speech'
  | 'busy'
  | 'no_answer'
  | 'failed'
  | 'canceled'
  | 'timeout'
  | 'queued'
  | 'ringing';

export const CALL_STATUS: Record<CallStatus, StatusSpec> = {
  queued: { label: 'Queued', tone: 'neutral', icon: 'clock' },
  ringing: { label: 'Ringing', tone: 'info', icon: 'spinner' },
  answered_speech: { label: 'Answered', tone: 'success', icon: 'check' },
  answered_no_speech: { label: 'Answered', tone: 'success', icon: 'check' },
  no_answer: { label: 'No answer', tone: 'neutral', icon: 'minus' },
  timeout: { label: 'No answer', tone: 'neutral', icon: 'minus' },
  busy: { label: 'Busy', tone: 'neutral', icon: 'minus' },
  failed: { label: 'Failed', tone: 'danger', icon: 'x' },
  canceled: { label: 'Cancelled', tone: 'neutral', icon: 'ban' },
};

export const SHEET_WRITE_STATUS: Record<'written' | 'pending_retry' | 'failed', StatusSpec> = {
  written: { label: 'Written', tone: 'success', icon: 'check' },
  pending_retry: { label: 'Pending retry', tone: 'warning', icon: 'clock' },
  failed: { label: 'Failed', tone: 'danger', icon: 'x' },
};

export const CONSENT_STATUS: Record<'validated' | 'skipped', StatusSpec> = {
  validated: { label: 'Validated', tone: 'success', icon: 'check' },
  skipped: { label: 'Skipped', tone: 'neutral', icon: 'minus' },
};

export type StepState = 'completed' | 'current' | 'optional' | 'blocked' | 'upcoming';

export const STEP_STATUS: Record<StepState, StatusSpec> = {
  completed: { label: 'Completed', tone: 'success', icon: 'check' },
  current: { label: 'Current step', tone: 'info', icon: 'play' },
  optional: { label: 'Optional', tone: 'neutral', icon: 'minus' },
  blocked: { label: 'Blocked', tone: 'danger', icon: 'ban' },
  upcoming: { label: 'Not started', tone: 'neutral', icon: 'clock' },
};

export const PROVIDER_NAMES: Record<string, string> = {
  local: 'Local',
  cloud: 'approved provider',
  google: 'Google',
  twilio: 'Twilio',
  github: 'GitHub',
  openai: 'OpenAI',
  anthropic: 'Anthropic',
};

/** Browser notifications for Crew Requests (per browser; section 13.7). */
export type NotifyState = 'on' | 'off' | 'blocked' | 'insecure' | 'unsupported';

export const NOTIFY_STATUS: Record<NotifyState, StatusSpec> = {
  on: { label: 'On', tone: 'success', icon: 'check' },
  off: { label: 'Off', tone: 'neutral', icon: 'minus' },
  blocked: { label: 'Blocked by the browser', tone: 'warning', icon: 'ban' },
  insecure: { label: 'Needs HTTPS', tone: 'warning', icon: 'alert' },
  unsupported: { label: 'Not supported', tone: 'neutral', icon: 'info' },
};
