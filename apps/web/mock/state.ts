/// <reference types="node" />
/** In-memory state of the mock control API and the views derived from it. */
import type {
  AgentVersionOut,
  AuditEventOut,
  BackupOut,
  CatalogAgentOut,
  ChatMessageOut,
  ChatSessionOut,
  InputRequestOut,
  InstallationOut,
  ModelOut,
  OccurrenceOut,
  ReadinessCheck,
  RunEventOut,
  RunOut,
  RunState,
  ScheduleOut,
  UserOut,
  ConnectionOut,
  DocumentOut,
  KnowledgeBaseOut,
  ProviderProfileOut,
} from '../src/api/schema.ts';
import type { StoredCitation } from '../src/lib/knowledge.ts';
import { GiB, MANIFESTS, MODELS, type ManifestFixture } from './fixtures.ts';
import { ApiErr, type Obj, type Resp } from './http.ts';

export type RunEvent = RunEventOut & { occurredAt: string };
export type RunRec = RunOut & { events: RunEvent[]; config: Obj };
export type ModelRec = ModelOut & { generative: boolean };
export type InstRec = Omit<InstallationOut, 'readiness'>;
export type ScheduleRec = Omit<ScheduleOut, 'ready' | 'blockers' | 'nextOccurrences' | 'nextRunAt' | 'agentName'>;
export type ChatRec = ChatSessionOut & { messages: ChatMessageOut[] };
export type DocRec = DocumentOut;
export type Provider = ConnectionOut['provider'];

export interface CatalogEntry {
  manifest: ManifestFixture;
  versions: AgentVersionOut[];
}

export interface Flags {
  offline: boolean;
  runtimeDown: boolean;
  lowDisk: boolean;
  googleDeny: boolean;
  sseFail: number;
  backupsDisabled: boolean;
}

export interface State {
  gen: number;
  owner: { user: UserOut; password: string } | null;
  sessions: Map<string, { csrf: string; createdAt: number }>;
  settings: {
    timezone: string;
    idleUnloadSeconds: number;
    setupCompleted: boolean;
    setupState: Obj;
    versions: Record<string, number>;
  };
  catalog: Map<string, CatalogEntry>;
  installations: Map<string, InstRec>;
  runs: Map<string, RunRec>;
  inputs: Map<string, InputRequestOut>;
  schedules: Map<string, ScheduleRec>;
  models: Map<string, ModelRec>;
  connections: Map<Provider, ConnectionOut>;
  profiles: Map<string, ProviderProfileOut>;
  kbs: Map<string, KnowledgeBaseOut>;
  docs: Map<string, DocRec>;
  chats: Map<string, ChatRec>;
  audit: AuditEventOut[];
  /** Newest first. */
  backups: BackupOut[];
  idempotency: Map<string, { hash: string; resp: Resp }>;
  oauthStates: Map<string, string[]>;
  lastTestCallAt: number;
  speedMs: number;
  flags: Flags;
  sseLog: { runId: string; lastEventId: string | null; after: string | null; at: string }[];
}

let counter = 0;
/** Deterministic uuid-shaped ids. */
export function newId(): string {
  counter += 1;
  const hex = counter.toString(16).padStart(12, '0');
  return `0192a000-0000-7000-8000-${hex}`;
}
export function resetIds(): void {
  counter = 0;
}

export const nowIso = (offsetMs = 0): string => new Date(Date.now() + offsetMs).toISOString();

export const TOTAL_MEMORY = 128 * GiB;
export const SYSTEM_RESERVE = 24 * GiB;
export const MAX_SERVING = 96 * GiB;
export const SAFETY_MARGIN = 8 * GiB;
export const BASELINE_USED = 9 * GiB;

export function emptyState(gen: number): State {
  return {
    gen,
    owner: null,
    sessions: new Map(),
    settings: {
      timezone: 'UTC',
      idleUnloadSeconds: 600,
      setupCompleted: false,
      setupState: {},
      versions: { timezone: 0, idleUnloadSeconds: 0, callbackBaseUrl: 0, setupCompleted: 0, setupState: 0 },
    },
    catalog: new Map(),
    installations: new Map(),
    runs: new Map(),
    inputs: new Map(),
    schedules: new Map(),
    models: new Map(),
    connections: new Map(),
    profiles: new Map(),
    kbs: new Map(),
    docs: new Map(),
    chats: new Map(),
    audit: [],
    backups: [],
    idempotency: new Map(),
    oauthStates: new Map(),
    lastTestCallAt: 0,
    speedMs: 300,
    flags: { offline: false, runtimeDown: false, lowDisk: false, googleDeny: false, sseFail: 0, backupsDisabled: false },
    sseLog: [],
  };
}

export const S: { current: State } = { current: emptyState(0) };
export const st = (): State => S.current;

// --- Catalog -------------------------------------------------------------------------

export function versionFrom(m: ManifestFixture, version: string, permissions: Obj): AgentVersionOut {
  const digest = Array.from(`${m.agentId}:${version}`).reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);
  return {
    id: newId(),
    version,
    image: m.image,
    imageDigest: `sha256:${digest.toString(16).padStart(8, '0').repeat(8)}`,
    sdkProtocol: '1',
    architectures: m.architectures,
    triggers: m.triggers,
    permissions,
    resources: m.resources,
    configurationSchema: m.configurationSchema,
    resultSchema: m.resultSchema,
    compatible: true,
    compatibilityIssues: [],
    createdAt: nowIso(-86400000),
  };
}

export function loadCatalog(s: State): void {
  for (const m of MANIFESTS) {
    s.catalog.set(m.agentId, { manifest: m, versions: [versionFrom(m, m.version, m.permissions)] });
  }
}

export function latestVersion(entry: CatalogEntry): AgentVersionOut {
  const v = entry.versions[entry.versions.length - 1];
  if (!v) throw new ApiErr(500, 'INTERNAL', 'Catalog entry has no versions.');
  return v;
}

export function catalogOut(entry: CatalogEntry): CatalogAgentOut {
  const latest = latestVersion(entry);
  const m = entry.manifest;
  return {
    agentId: m.agentId,
    name: m.name,
    summary: m.summary,
    publisher: m.publisher,
    source: 'bundled',
    trustStatus: 'curated',
    currentVersion: latest.version,
    versions: entry.versions.map((v) => v.version),
    latest,
    installed: [...st().installations.values()].some((i) => i.agentId === m.agentId),
  };
}

export function findVersion(agentId: string, version?: string | null): { entry: CatalogEntry; version: AgentVersionOut } {
  const entry = st().catalog.get(agentId);
  if (!entry) throw new ApiErr(404, 'NOT_FOUND', `Agent ${agentId} was not found.`);
  const v = version ? entry.versions.find((x) => x.version === version) : latestVersion(entry);
  if (!v) throw new ApiErr(404, 'NOT_FOUND', `Version ${version ?? ''} of ${agentId} was not found.`);
  return { entry, version: v };
}

// --- Models --------------------------------------------------------------------------

export function loadModels(s: State, installed: boolean): void {
  for (const f of MODELS) {
    const isInstalled = installed && f.id !== 'local.general.large';
    s.models.set(f.id, {
      id: f.id,
      displayName: f.displayName,
      family: f.family,
      backend: f.generative ? 'vllm' : 'fastembed',
      downloadState: isInstalled ? 'INSTALLED' : 'NOT_INSTALLED',
      memoryState: 'NOT_LOADED',
      stage: null,
      diskBytes: f.diskBytes,
      download: { bytesDone: isInstalled ? f.diskBytes : 0, bytesTotal: f.diskBytes, currentFile: null, revision: 'r1' },
      expectedMemoryBytes: f.expectedMemoryBytes,
      reservedBytes: 0,
      contextLimit: f.contextLimit,
      capabilities: f.capabilities,
      validation: f.generative ? 'Validated on GB10 (vLLM 0.x, bf16)' : 'CPU embedding model',
      license: f.license,
      error: null,
      loadStartedAt: null,
      readyAt: null,
      idleUnloadAt: null,
      activeLeases: [],
      generative: f.generative,
    });
  }
}

export function modelOut(m: ModelRec): ModelOut {
  const { generative: _g, ...out } = m;
  void _g;
  return out;
}

export function memoryOut(): Obj {
  const models = [...st().models.values()];
  const reserved = models.reduce((sum, m) => sum + m.reservedBytes, 0);
  return {
    totalBytes: TOTAL_MEMORY,
    availableBytes: TOTAL_MEMORY - BASELINE_USED - reserved,
    systemReserveBytes: SYSTEM_RESERVE,
    maxServingBytes: MAX_SERVING,
    safetyMarginBytes: SAFETY_MARGIN,
    reservedBytes: reserved,
    models: models
      .filter((m) => m.reservedBytes > 0)
      .map((m) => ({ id: m.id, displayName: m.displayName, reservedBytes: m.reservedBytes, memoryState: m.memoryState })),
  };
}

// --- Connections -----------------------------------------------------------------------

const DISPLAY: Record<Provider, string> = { google: 'Google', twilio: 'Twilio', openai: 'OpenAI', anthropic: 'Anthropic' };

export function setConnection(provider: Provider, patch: Partial<ConnectionOut>): ConnectionOut {
  const s = st();
  const current: ConnectionOut = s.connections.get(provider) ?? {
    provider,
    displayName: DISPLAY[provider],
    status: 'NOT_CONNECTED',
    grantedCapabilities: [],
    lastCheckedAt: null,
    account: null,
    detail: null,
  };
  const next = { ...current, ...patch };
  s.connections.set(provider, next);
  return next;
}

export function loadConnections(connected: boolean): void {
  for (const p of ['anthropic', 'google', 'openai', 'twilio'] as const) setConnection(p, {});
  if (connected) {
    setConnection('google', {
      status: 'CONNECTED',
      grantedCapabilities: ['gmail.readonly', 'spreadsheets'],
      lastCheckedAt: nowIso(-120000),
      account: 'demo@example.com',
    });
    setConnection('twilio', {
      status: 'CONNECTED',
      grantedCapabilities: ['call.fixed_script'],
      lastCheckedAt: nowIso(-300000),
      account: 'AC••••••••••••••••••••••••••••7f3a',
      detail: 'Caller number ••••0100. Callback status verified.',
    });
  }
}

// --- Readiness -------------------------------------------------------------------------

function connectorsOf(permissions: Obj): string[] {
  const c = permissions.connectors;
  return typeof c === 'object' && c !== null ? Object.keys(c) : [];
}

function llmProfilesOf(permissions: Obj): string[] {
  const p = permissions.llmProfiles;
  return Array.isArray(p) ? p.filter((x): x is string => typeof x === 'string') : [];
}

export function readinessFor(inst: InstRec): { ready: boolean; checks: ReadinessCheck[] } {
  const s = st();
  const checks: ReadinessCheck[] = [];
  checks.push(
    inst.enabled
      ? { name: 'enabled', status: 'ok', detail: 'Enabled' }
      : { name: 'enabled', status: 'missing', detail: 'The agent is disabled.' },
  );
  checks.push(
    inst.needsReapproval
      ? { name: 'permissions', status: 'needs_attention', detail: 'A new version requests changed permissions; review and approve them.' }
      : { name: 'permissions', status: 'ok', detail: 'All requested permissions are approved.' },
  );
  const { version } = findVersion(inst.agentId, inst.agentVersion);
  const schema = version.configurationSchema as { required?: string[] };
  const missing = (schema.required ?? []).filter((k) => inst.config[k] === undefined || inst.config[k] === '');
  checks.push(
    missing.length
      ? { name: 'configuration', status: 'missing', detail: `Missing: ${missing.join(', ')}` }
      : { name: 'configuration', status: 'ok', detail: 'Configuration is valid.' },
  );
  checks.push({ name: 'architecture', status: 'ok', detail: 'Image supports linux/arm64.' });
  for (const provider of connectorsOf(inst.approvedPermissions)) {
    const c = s.connections.get(provider as Provider);
    const status = c?.status ?? 'NOT_CONNECTED';
    checks.push(
      status === 'CONNECTED'
        ? { name: 'connection', status: 'ok', detail: `${DISPLAY[provider as Provider] ?? provider} connected`, resource: provider }
        : status === 'NEEDS_ATTENTION'
          ? { name: 'connection', status: 'needs_attention', detail: `Reconnect ${DISPLAY[provider as Provider] ?? provider}`, resource: provider }
          : { name: 'connection', status: 'missing', detail: `Connect ${DISPLAY[provider as Provider] ?? provider}`, resource: provider },
    );
  }
  if (llmProfilesOf(inst.approvedPermissions).length > 0) {
    const profile = typeof inst.config.modelProfile === 'string' ? inst.config.modelProfile : 'local.general.small';
    const model = s.models.get(profile);
    checks.push(
      model?.downloadState === 'INSTALLED'
        ? { name: 'model', status: 'ok', detail: `${model.displayName} is installed; it loads when a run needs it.`, resource: profile }
        : { name: 'model', status: 'missing', detail: `Install the model ${profile}.`, resource: profile },
    );
  }
  return { ready: checks.every((c) => c.status === 'ok'), checks };
}

export function capabilitiesFor(permissions: Obj, config: Obj): string[] {
  const caps: string[] = [];
  for (const p of llmProfilesOf(permissions)) {
    caps.push(`llm.profile:${typeof config.modelProfile === 'string' ? config.modelProfile : `${p}.small`}`);
  }
  const connectors = permissions.connectors;
  if (typeof connectors === 'object' && connectors !== null) {
    for (const [prov, scopes] of Object.entries(connectors as Obj)) {
      if (Array.isArray(scopes)) for (const sc of scopes) caps.push(`${prov}.${String(sc)}`);
    }
  }
  if (Array.isArray(permissions.cloudProviders)) for (const c of permissions.cloudProviders) caps.push(`cloud.${String(c)}`);
  if (permissions.userInput === true) caps.push('user_input');
  return caps.sort();
}

export function installationOut(inst: InstRec): InstallationOut {
  return { ...inst, readiness: readinessFor(inst) };
}

// --- Schedules ---------------------------------------------------------------------------

function zoneParts(date: Date, timeZone: string): Record<string, string> {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    weekday: 'short',
    timeZoneName: 'short',
  }).formatToParts(date);
  const out: Record<string, string> = {};
  for (const p of parts) out[p.type] = p.value;
  return out;
}

function offsetMinutes(date: Date, timeZone: string): number {
  const p = zoneParts(date, timeZone);
  const asUtc = Date.UTC(Number(p.year), Number(p.month) - 1, Number(p.day), Number(p.hour), Number(p.minute), Number(p.second));
  return Math.round((asUtc - date.getTime()) / 60000);
}

export function validTimeZone(tz: string): boolean {
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

/** Prefer a letter abbreviation (IST, EDT) over "GMT+5:30" when some locale has one. */
function abbreviation(date: Date, timeZone: string): string {
  let fallback = timeZone;
  for (const locale of ['en-US', 'en-IN', 'en-GB', 'en-AU']) {
    const name = new Intl.DateTimeFormat(locale, { timeZone, timeZoneName: 'short' })
      .formatToParts(date)
      .find((x) => x.type === 'timeZoneName')?.value;
    if (!name) continue;
    if (!name.startsWith('GMT') && !name.startsWith('UTC')) return name;
    if (fallback === timeZone) fallback = name;
  }
  return fallback;
}

const CRON = /^(\d{1,2}) (\d{1,2}) \* \* (\*|[0-6](?:-[0-6])?(?:,[0-6])*)$/;

export function parseCron(cron: string): { minute: number; hour: number; days: Set<number> } {
  const m = CRON.exec(cron.trim());
  if (!m) {
    throw new ApiErr(422, 'INVALID_CRON', 'Use a cron like "0 10 * * *" (minute hour * * day-of-week).', {
      errors: [{ path: '/cron', message: 'Unsupported cron expression in the mock.' }],
    });
  }
  const minute = Number(m[1]);
  const hour = Number(m[2]);
  if (minute > 59 || hour > 23) throw new ApiErr(422, 'INVALID_CRON', 'Minute or hour is out of range.');
  const spec = m[3] ?? '*';
  const days = new Set<number>();
  if (spec === '*') [0, 1, 2, 3, 4, 5, 6].forEach((d) => days.add(d));
  else
    for (const part of spec.split(',')) {
      const [a, b] = part.split('-').map(Number);
      if (a === undefined) continue;
      for (let d = a; d <= (b ?? a); d++) days.add(d);
    }
  return { minute, hour, days };
}

const WEEKDAYS: Record<string, number> = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };

export function occurrences(cron: string, timeZone: string, count = 3, from = new Date()): OccurrenceOut[] {
  if (!validTimeZone(timeZone)) {
    throw new ApiErr(422, 'INVALID_TIMEZONE', `${timeZone} is not an IANA timezone.`, {
      errors: [{ path: '/timezone', message: 'Unknown timezone.' }],
    });
  }
  const { minute, hour, days } = parseCron(cron);
  const out: OccurrenceOut[] = [];
  const today = zoneParts(from, timeZone);
  for (let i = 0; i < 30 && out.length < count; i++) {
    const base = new Date(Date.UTC(Number(today.year), Number(today.month) - 1, Number(today.day) + i, hour, minute));
    let instant = base.getTime() - offsetMinutes(base, timeZone) * 60000;
    instant = base.getTime() - offsetMinutes(new Date(instant), timeZone) * 60000;
    const at = new Date(instant);
    if (at.getTime() <= from.getTime()) continue;
    const p = zoneParts(at, timeZone);
    if (!days.has(WEEKDAYS[p.weekday ?? 'Sun'] ?? 0)) continue;
    const off = offsetMinutes(at, timeZone);
    const sign = off >= 0 ? '+' : '-';
    const abs = Math.abs(off);
    const offStr = `${sign}${String(Math.floor(abs / 60)).padStart(2, '0')}:${String(abs % 60).padStart(2, '0')}`;
    out.push({
      at: at.toISOString(),
      local: `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:00${offStr}`,
      zoneAbbreviation: abbreviation(at, timeZone),
    });
  }
  return out;
}

export function scheduleOut(s: ScheduleRec): ScheduleOut {
  const inst = st().installations.get(s.installationId);
  const readiness = inst ? readinessFor(inst) : { ready: false, checks: [] };
  const next = s.enabled ? occurrences(s.cron, s.timezone, 3) : [];
  return {
    ...s,
    agentName: inst?.agentName ?? 'Removed agent',
    ready: readiness.ready,
    blockers: readiness.checks.filter((c) => c.status !== 'ok'),
    nextRunAt: next[0]?.at ?? null,
    nextOccurrences: next,
  };
}

// --- Runs, events, listeners --------------------------------------------------------------

export const TERMINAL: RunState[] = ['SUCCEEDED', 'FAILED', 'CANCELLED', 'INTERRUPTED'];
export const isTerminalState = (s: RunState): boolean => TERMINAL.includes(s);

const runListeners = new Map<string, Set<() => void>>();
const modelListeners = new Map<string, Set<() => void>>();

function listen(map: Map<string, Set<() => void>>, id: string, fn: () => void): () => void {
  const set = map.get(id) ?? new Set();
  set.add(fn);
  map.set(id, set);
  return () => set.delete(fn);
}

export const onRun = (id: string, fn: () => void) => listen(runListeners, id, fn);
export const onModel = (id: string, fn: () => void) => listen(modelListeners, id, fn);

export function notifyRun(id: string): void {
  setImmediate(() => runListeners.get(id)?.forEach((fn) => fn()));
}
export function notifyModel(id: string): void {
  setImmediate(() => modelListeners.get(id)?.forEach((fn) => fn()));
}

/** Close every open run SSE connection (test control "drop"). */
export const runStreamClosers = new Set<() => void>();

export function appendEvent(run: RunRec, type: string, payload: Obj): RunEvent {
  const last = run.events[run.events.length - 1];
  const at = nowIso();
  const event: RunEvent = {
    runId: run.id,
    sequence: (last?.sequence ?? 0) + 1,
    attempt: run.currentAttempt,
    type,
    payload,
    createdAt: at,
    occurredAt: at,
  };
  run.events.push(event);
  run.updatedAt = at;
  notifyRun(run.id);
  return event;
}

export function setRunState(run: RunRec, to: RunState, extra: Obj = {}): void {
  const from = run.state;
  if (from === to) return;
  run.state = to;
  if (to === 'PREPARING' && !run.startedAt) run.startedAt = nowIso();
  if (isTerminalState(to)) {
    run.finishedAt = nowIso();
    if (run.startedAt) run.activeSecondsUsed = Math.round((Date.now() - Date.parse(run.startedAt)) / 1000);
  }
  appendEvent(run, 'run.state_changed', { from, to, ...extra });
}

export function runOut(run: RunRec): RunOut {
  const { events: _e, config: _c, ...out } = run;
  void _e;
  void _c;
  const pending = [...st().inputs.values()].filter((i) => i.runId === run.id && i.state === 'pending').length;
  return { ...out, pendingInputCount: pending };
}

// --- Audit ---------------------------------------------------------------------------------

export function audit(action: string, target: { type?: string; id?: string } = {}, metadata: Obj = {}, outcome = 'success'): void {
  const s = st();
  s.audit.unshift({
    id: newId(),
    actorType: s.owner ? 'user' : 'anonymous',
    actorId: s.owner?.user.id ?? null,
    action,
    targetType: target.type ?? null,
    targetId: target.id ?? null,
    outcome,
    requestId: null,
    metadata,
    createdAt: nowIso(),
  });
}

export type { StoredCitation };

// --- Backups -------------------------------------------------------------------------

/** crewquarters-backup-<UTC stamp>-<label>, like the platform's archive names. */
export function backupName(at: Date, label: string): string {
  const stamp = at.toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z');
  return `crewquarters-backup-${stamp}-${label}`;
}

export function backupRec(patch: Partial<BackupOut> & Pick<BackupOut, 'id' | 'status' | 'source'>): BackupOut {
  return {
    createdAt: nowIso(),
    finishedAt: null,
    sizeBytes: null,
    includesMasterKey: false,
    platformVersion: null,
    migrationHead: null,
    documentCount: null,
    sha256: null,
    downloadable: false,
    error: null,
    ...patch,
  };
}
