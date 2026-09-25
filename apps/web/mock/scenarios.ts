/// <reference types="node" />
/** Named starting states: fresh (first run), ready (default), populated (demo data). */
import { digestResult, KB_PASSAGES, MIGRATION_HEAD, PLATFORM_VERSION } from './fixtures.ts';
import type { Obj } from './http.ts';
import { askCallerApproval } from './sim.ts';
import {
  appendEvent,
  backupName,
  backupRec,
  capabilitiesFor,
  emptyState,
  findVersion,
  loadCatalog,
  loadConnections,
  loadModels,
  newId,
  nowIso,
  resetIds,
  S,
  setRunState,
  st,
  type InstRec,
  type RunRec,
} from './state.ts';

export const SCENARIOS = ['fresh', 'ready', 'populated'] as const;
export type Scenario = (typeof SCENARIOS)[number];

export const OWNER = { username: 'owner', password: 'correct-horse-battery' };
export const BOOTSTRAP_TOKEN = 'CQ-DEMO-SETUP';

export function defaultsFrom(schema: Obj): Obj {
  const out: Obj = {};
  const props = (schema.properties ?? {}) as Record<string, Obj>;
  for (const [k, v] of Object.entries(props)) if (v.default !== undefined) out[k] = structuredClone(v.default);
  return out;
}

export function createInstallation(agentId: string, config: Obj): InstRec {
  const { entry, version } = findVersion(agentId);
  const now = nowIso();
  const fullConfig = { ...defaultsFrom(version.configurationSchema), ...config };
  const inst: InstRec = {
    id: newId(),
    agentId,
    agentName: entry.manifest.name,
    agentVersion: version.version,
    agentVersionId: version.id,
    config: fullConfig,
    requestedPermissions: version.permissions,
    approvedPermissions: version.permissions,
    capabilities: capabilitiesFor(version.permissions, fullConfig),
    modelBindings: Array.isArray(version.permissions.llmProfiles) && version.permissions.llmProfiles.length > 0
      ? { 'local.general': typeof fullConfig.modelProfile === 'string' ? fullConfig.modelProfile : 'local.general.small' }
      : {},
    needsReapproval: false,
    enabled: true,
    version: 1,
    createdAt: now,
    updatedAt: now,
  };
  st().installations.set(inst.id, inst);
  return inst;
}

export function createRun(inst: InstRec, trigger: 'manual' | 'schedule' = 'manual', scheduleId: string | null = null): RunRec {
  const { version } = findVersion(inst.agentId, inst.agentVersion);
  const resources = version.resources as { activeTimeoutSeconds?: number; maxInputWaitSeconds?: number };
  const now = nowIso();
  const run: RunRec = {
    id: newId(),
    installationId: inst.id,
    agentId: inst.agentId,
    agentName: inst.agentName,
    agentVersion: inst.agentVersion,
    trigger,
    scheduleId,
    scheduledFor: trigger === 'schedule' ? now : null,
    state: 'QUEUED',
    currentAttempt: 1,
    result: null,
    error: null,
    retryable: false,
    cancelRequested: false,
    acknowledgedAt: null,
    activeSecondsUsed: 0,
    inputWaitSecondsUsed: 0,
    activeTimeoutSeconds: resources.activeTimeoutSeconds ?? 600,
    maxInputWaitSeconds: resources.maxInputWaitSeconds ?? 0,
    usesCloud: Array.isArray(inst.approvedPermissions.cloudProviders) && inst.approvedPermissions.cloudProviders.length > 0,
    pendingInputCount: 0,
    createdAt: now,
    startedAt: null,
    finishedAt: null,
    updatedAt: now,
    events: [],
    config: inst.config,
  };
  st().runs.set(run.id, run);
  appendEvent(run, 'run.state_changed', { from: null, to: 'QUEUED' });
  return run;
}

function backdate(run: RunRec, minutesAgo: number, durationSeconds: number): void {
  const start = Date.now() - minutesAgo * 60000;
  run.createdAt = new Date(start).toISOString();
  run.startedAt = new Date(start + 2000).toISOString();
  run.finishedAt = new Date(start + 2000 + durationSeconds * 1000).toISOString();
  run.updatedAt = run.finishedAt;
  run.activeSecondsUsed = durationSeconds;
  run.events.forEach((e, i) => {
    const t = new Date(start + i * 1500).toISOString();
    e.createdAt = t;
    e.occurredAt = t;
  });
}

function addKnowledge(): void {
  const s = st();
  const kbId = newId();
  s.kbs.set(kbId, {
    id: kbId,
    name: 'Team handbook',
    embeddingProfile: 'local.embedding.jina-v2-small-en',
    embeddingDimension: 512,
    createdAt: nowIso(-7 * 86400000),
  });
  const docs: [string, string, number][] = [
    ['refund-policy.md', 'text/markdown', 18_432],
    ['support-handbook.pdf', 'application/pdf', 412_000],
  ];
  for (const [name, mime, bytes] of docs) {
    const id = newId();
    s.docs.set(id, {
      id,
      knowledgeBaseId: kbId,
      name,
      mime,
      bytes,
      sha256: id.replace(/-/g, '').padEnd(64, '0'),
      state: 'READY',
      extracted: { segments: 24, chunks: Math.max(2, Math.round(bytes / 3000)), tokens: 5200 },
      error: null,
      createdAt: nowIso(-6 * 86400000),
      updatedAt: nowIso(-6 * 86400000),
    });
  }
  void KB_PASSAGES;
}

/** Backups already on the device: newest first, like the API. */
function addBackups(populated: boolean): void {
  const s = st();
  const at = (hoursAgo: number) => new Date(Date.now() - hoursAgo * 3600000);
  const archive = (hoursAgo: number, label: string, source: 'api' | 'device', masterKey = false) => {
    const created = at(hoursAgo);
    return backupRec({
      id: backupName(created, label),
      status: 'succeeded',
      source,
      createdAt: created.toISOString(),
      finishedAt: new Date(created.getTime() + 95_000).toISOString(),
      sizeBytes: 41_943_040 + hoursAgo * 1024,
      includesMasterKey: masterKey,
      platformVersion: PLATFORM_VERSION,
      migrationHead: MIGRATION_HEAD,
      documentCount: 2,
      sha256: `${label}`.padEnd(64, '0').replace(/[^0-9a-f]/g, 'a'),
      downloadable: !masterKey,
    });
  };
  s.backups = [archive(26, 'nightly', 'device')];
  if (!populated) return;
  const failedAt = at(20);
  s.backups.unshift(
    backupRec({
      id: backupName(failedAt, '9f3a1c'),
      status: 'failed',
      source: 'api',
      createdAt: failedAt.toISOString(),
      finishedAt: new Date(failedAt.getTime() + 4000).toISOString(),
      error: { code: 'BACKUP_FAILED', message: 'pg_dump exited with status 1 (disk full).' },
    }),
  );
  s.backups.push(archive(24 * 6, 'pre-upgrade', 'device', true));
}

export function applyScenario(scenario: Scenario): void {
  const prev = S.current;
  resetIds();
  const s = emptyState(prev.gen + 1);
  s.speedMs = prev.speedMs;
  S.current = s;
  loadCatalog(s);
  if (scenario === 'fresh') {
    loadModels(s, false);
    loadConnections(false);
    return;
  }
  s.owner = {
    user: { id: newId(), username: OWNER.username, email: 'owner@example.com', role: 'owner', createdAt: nowIso(-30 * 86400000) },
    password: OWNER.password,
  };
  s.settings.timezone = 'Asia/Kolkata';
  s.settings.setupCompleted = true;
  s.settings.setupState = { completed: ['welcome', 'preflight', 'owner', 'storage', 'services', 'model', 'connections', 'agents', 'validation'] };
  s.settings.versions = { timezone: 1, idleUnloadSeconds: 0, callbackBaseUrl: 0, setupCompleted: 1, setupState: 3 };
  loadModels(s, true);
  loadConnections(true);
  addKnowledge();
  addBackups(scenario === 'populated');
  if (scenario === 'ready') return;

  // populated
  const digest = createInstallation('daily-gmail-digest', { timezone: 'Asia/Kolkata' });
  const caller = createInstallation('caller', { spreadsheetId: '1AbCdEfGhIjKlMnOp' });
  const scheduleId = newId();
  s.schedules.set(scheduleId, {
    id: scheduleId,
    installationId: digest.id,
    cron: '0 10 * * *',
    timezone: 'Asia/Kolkata',
    misfirePolicy: 'fire_once',
    enabled: true,
    lastFiredAt: nowIso(-86400000),
    lastRunId: null,
    version: 1,
    createdAt: nowIso(-3 * 86400000),
    updatedAt: nowIso(-3 * 86400000),
  });

  const done = createRun(digest, 'schedule', scheduleId);
  setRunState(done, 'PREPARING');
  setRunState(done, 'RUNNING');
  appendEvent(done, 'run.progress', { percent: 50, step: 'classify', message: 'Classifying messages' });
  appendEvent(done, 'run.result', { status: 'succeeded', summary: 'Digest ready (message cap reached)' });
  done.result = digestResult('Asia/Kolkata', 8);
  setRunState(done, 'SUCCEEDED');
  backdate(done, 26 * 60, 94);
  const sched = s.schedules.get(scheduleId);
  if (sched) sched.lastRunId = done.id;

  const failed = createRun(digest);
  setRunState(failed, 'PREPARING');
  setRunState(failed, 'RUNNING');
  appendEvent(failed, 'run.log', { level: 'error', message: 'Gmail API returned 503 twice; giving up this attempt.' });
  appendEvent(failed, 'run.error', { code: 'FAKE_FAILURE', message: 'Gmail was temporarily unavailable.', retryable: true });
  failed.error = { code: 'FAKE_FAILURE', message: 'Gmail was temporarily unavailable.', retryable: true };
  failed.retryable = true;
  setRunState(failed, 'FAILED', { errorCode: 'FAKE_FAILURE' });
  backdate(failed, 180, 41);

  const waiting = createRun(caller);
  setRunState(waiting, 'PREPARING');
  setRunState(waiting, 'RUNNING');
  appendEvent(waiting, 'run.progress', { percent: 15, step: 'validate', message: 'Checking consent and E.164 numbers' });
  askCallerApproval(waiting);
}
