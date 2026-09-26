/// <reference types="node" />
/**
 * Mock Crewquarters control API for Playwright e2e tests and UI development.
 *
 * Usage (from apps/web, Node 24 runs TypeScript natively):
 *   node mock/server.ts [--port 4010] [--static dist] [--scenario ready] [--speed 300]
 *
 * - `/api/v1/*`: the public control API (openapi.yaml shapes plus the connection,
 *   provider-profile, knowledge and citation routes of commit 142b289), with the real
 *   session cookie + X-CSRF-Token + Origin rules and Idempotency-Key replay.
 * - `--static dist`: also serves the built SPA with index.html fallback and the
 *   security headers recommended for the reverse proxy (mock/csp.ts).
 * - `/__mock/*`: test controls (no auth):
 *     POST /__mock/reset          {scenario: 'fresh'|'ready'|'populated'}
 *     POST /__mock/speed          {ms}            simulation step length (default 300)
 *     POST /__mock/offline        {on}            every /api/v1 request → 502 text/plain
 *     POST /__mock/runtime-down   {on}            failed "runtime daemon" check
 *     POST /__mock/low-disk       {on}            storage warning
 *     POST /__mock/sse            {mode:'drop'} | {mode:'fail', count}
 *     GET  /__mock/sse-log                        [{runId,lastEventId,after,at}]
 *     POST /__mock/backups-disabled {on}          backups not configured (CQ_BACKUP_DIR unset)
 *     POST /__mock/google-expired                 Google → NEEDS_ATTENTION
 *     POST /__mock/google-deny    {on}            consent returns ?result=error&code=OAUTH_DENIED
 *     GET  /api/v1/connections/google/callback    simulated Google consent + broker callback
 *                                                 (authorizationUrl from /google/start points here)
 *     POST /__mock/new-version    {agentId}       publish 0.2.0 with an added cloud permission
 *     POST /__mock/model-error    {modelId}       memoryState LOAD_ERROR
 *     POST /__mock/input-request                  a caller run WAITING_INPUT with a new pending
 *                                                 Crew Request → {runId, inputRequestId, title}
 *     GET  /__mock/state                          summary for debugging
 * Credentials: owner / correct-horse-battery. Bootstrap token (fresh): CQ-DEMO-SETUP.
 */
import { createHash, randomBytes } from 'node:crypto';
import { existsSync, readFileSync, statSync } from 'node:fs';
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { crc32, gzipSync } from 'node:zlib';
import { extname, join, normalize, resolve } from 'node:path';
import type { ChatMessageOut, InputRequestOut } from '../src/api/schema.ts';
import type { StoredCitation } from '../src/lib/knowledge.ts';
import { HTML_SECURITY_HEADERS } from './csp.ts';
import { BACKUP_EXCLUDES, BACKUP_INCLUDES, BACKUP_LOCATION, BACKUP_RETENTION, KB_PASSAGES, PLATFORM_VERSION } from './fixtures.ts';
import {
  ApiErr,
  asObj,
  cookie,
  deepEqual,
  errorBody,
  HANDLED,
  nextRequestId,
  noContent,
  notFound,
  ok,
  parseCookies,
  readBody,
  send,
  sleep,
  sseEvent,
  sseHeaders,
  str,
  type Obj,
  type Resp,
  type Result,
} from './http.ts';
import {
  applyScenario,
  BOOTSTRAP_TOKEN,
  createInstallation,
  createPendingInputRequest,
  createRun,
  defaultsFrom,
  SCENARIOS,
  type Scenario,
} from './scenarios.ts';
import {
  addLease,
  cancelRun,
  changeModel,
  continueAfterAnswer,
  ingest,
  installModel,
  loadModel,
  releaseLease,
  runBackup,
  startRun,
  unloadModel,
} from './sim.ts';
import {
  appendEvent,
  audit,
  backupName,
  backupRec,
  capabilitiesFor,
  catalogOut,
  findVersion,
  installationOut,
  isTerminalState,
  latestVersion,
  memoryOut,
  modelOut,
  newId,
  nowIso,
  occurrences,
  onModel,
  onRun,
  readinessFor,
  runOut,
  runStreamClosers,
  scheduleOut,
  setConnection,
  setRunState,
  st,
  validTimeZone,
  versionFrom,
  type ChatRec,
  type ModelRec,
  type RunRec,
} from './state.ts';

// --- Routing ------------------------------------------------------------------------------

interface Ctx {
  req: IncomingMessage;
  res: ServerResponse;
  method: string;
  path: string;
  query: URLSearchParams;
  cookies: Record<string, string>;
  body: Obj;
  raw: Buffer;
  requestId: string;
  params: Record<string, string>;
}

type Handler = (ctx: Ctx) => Result | Promise<Result>;
interface Route {
  method: string;
  re: RegExp;
  keys: string[];
  auth: boolean;
  handler: Handler;
}
const routes: Route[] = [];

function route(method: string, path: string, handler: Handler, auth = true): void {
  const keys: string[] = [];
  const re = new RegExp(
    `^${path.replace(/:([a-zA-Z]+)/g, (_m, k: string) => {
      keys.push(k);
      return '([^/]+)';
    })}$`,
  );
  routes.push({ method, re, keys, auth, handler });
}

const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
const P = '/api/v1';

function param(ctx: Ctx, key: string): string {
  return ctx.params[key] ?? '';
}

function page<T>(items: T[]): { items: T[]; nextCursor: null } {
  return { items, nextCursor: null };
}

function validation(errors: { path: string; message: string }[], code = 'VALIDATION_FAILED', message = 'The request is invalid.'): ApiErr {
  return new ApiErr(422, code, message, { errors });
}

// --- Auth ----------------------------------------------------------------------------------------

function sessionCookies(): { headers: Record<string, string[]>; csrf: string } {
  const token = randomBytes(24).toString('base64url');
  const csrf = randomBytes(24).toString('base64url');
  st().sessions.set(token, { csrf, createdAt: Date.now() });
  return {
    csrf,
    headers: {
      'Set-Cookie': [cookie('cq_session', token, { httpOnly: true, maxAge: 604800 }), cookie('cq_csrf', csrf, { maxAge: 604800 })],
    },
  };
}

function sessionOut(csrf: string): Obj {
  const owner = st().owner;
  if (!owner) throw new ApiErr(401, 'UNAUTHENTICATED', 'Sign in to continue.');
  return { user: owner.user, csrfToken: csrf, expiresAt: nowIso(604800000), idleExpiresAt: nowIso(43200000) };
}

function currentSession(ctx: Ctx): { token: string; csrf: string } {
  const token = ctx.cookies.cq_session;
  const sess = token ? st().sessions.get(token) : undefined;
  if (!token || !sess || !st().owner) throw new ApiErr(401, 'UNAUTHENTICATED', 'Sign in to continue.');
  return { token, csrf: sess.csrf };
}

function checkOrigin(ctx: Ctx): void {
  const host = ctx.req.headers.host ?? '';
  const own = `http://${host}`;
  let origin = ctx.req.headers.origin;
  if (!origin) {
    const ref = ctx.req.headers.referer;
    origin = ref ? ref.split('/').slice(0, 3).join('/') : undefined;
  }
  if (!origin || origin.replace(/\/$/, '') !== own) {
    throw new ApiErr(403, 'ORIGIN_REJECTED', 'Request origin is not allowed.');
  }
}

route('POST', `${P}/bootstrap`, (ctx) => {
  checkOrigin(ctx);
  const s = st();
  if (s.owner) throw new ApiErr(409, 'ALREADY_BOOTSTRAPPED', 'The owner account already exists.');
  const username = str(ctx.body.username) ?? '';
  const password = str(ctx.body.password) ?? '';
  const errors = [];
  if (!username.trim()) errors.push({ path: '/username', message: 'Enter a username.' });
  if (password.length < 12) errors.push({ path: '/password', message: 'Use at least 12 characters.' });
  if (errors.length) throw validation(errors);
  if (str(ctx.body.token) !== BOOTSTRAP_TOKEN) {
    audit('auth.bootstrap', {}, {}, 'denied');
    throw new ApiErr(403, 'INVALID_BOOTSTRAP_TOKEN', 'The setup code is invalid or expired.');
  }
  s.owner = {
    user: { id: newId(), username: username.trim(), email: str(ctx.body.email) ?? null, role: 'owner', createdAt: nowIso() },
    password,
  };
  audit('auth.bootstrap', { type: 'user', id: s.owner.user.id });
  const { headers, csrf } = sessionCookies();
  return ok(sessionOut(csrf), 201, headers);
}, false);

route('POST', `${P}/sessions`, (ctx) => {
  checkOrigin(ctx);
  const owner = st().owner;
  const username = (str(ctx.body.username) ?? '').trim().toLowerCase();
  if (!owner || owner.user.username.toLowerCase() !== username || owner.password !== str(ctx.body.password)) {
    audit('auth.login', {}, { username: username.slice(0, 64) }, 'denied');
    throw new ApiErr(401, 'INVALID_CREDENTIALS', 'The username or password is incorrect.');
  }
  audit('auth.login', { type: 'user', id: owner.user.id });
  const { headers, csrf } = sessionCookies();
  return ok(sessionOut(csrf), 201, headers);
}, false);

route('DELETE', `${P}/sessions/current`, (ctx) => {
  const { token } = currentSession(ctx);
  st().sessions.delete(token);
  audit('auth.logout');
  return noContent({ 'Set-Cookie': [cookie('cq_session', '', { maxAge: 0, httpOnly: true }), cookie('cq_csrf', '', { maxAge: 0 })] });
});

route('GET', `${P}/me`, (ctx) => ok(sessionOut(currentSession(ctx).csrf)));

route('GET', `${P}/bootstrap/status`, () => ok({ ownerExists: st().owner !== null }), false);

// --- Health, settings, status, audit --------------------------------------------------------------

route('GET', `${P}/health/live`, () => ok({ status: 'ok' }), false);
route('GET', `${P}/health/ready`, () => ok({ status: 'ok', checks: { database: 'ok', migrations: 'ok (0004)' } }), false);

const CALLBACK_BASE = 'https://crew.example.test';
function settingsOut(): Obj {
  const s = st().settings;
  return {
    timezone: s.timezone,
    idleUnloadSeconds: s.idleUnloadSeconds,
    callbackBaseUrl: CALLBACK_BASE,
    callbackUrls: {
      googleRedirectUri: `${CALLBACK_BASE}/api/v1/connections/google/callback`,
      twilioCallbackBase: `${CALLBACK_BASE}/api/v1/callbacks/twilio`,
    },
    setupCompleted: s.setupCompleted,
    setupState: s.setupState,
    versions: s.versions,
  };
}

route('GET', `${P}/settings`, () => ok(settingsOut()));
route('PATCH', `${P}/settings`, (ctx) => {
  const s = st().settings;
  const versions = asObj(ctx.body.versions);
  const updates = Object.entries(ctx.body).filter(([k]) => k !== 'versions');
  for (const [key, value] of updates) {
    if (key === 'callbackBaseUrl') {
      throw new ApiErr(422, 'SETTING_READ_ONLY', 'callbackBaseUrl comes from CQ_PUBLIC_BASE_URL and cannot be changed here.', {
        errors: [{ path: '/callbackBaseUrl', message: 'Read-only setting.' }],
      });
    }
    if (!(key in s.versions)) throw validation([{ path: `/${key}`, message: 'Unknown setting.' }]);
    const expected = versions[key];
    if (typeof expected === 'number' && expected !== s.versions[key]) {
      throw new ApiErr(409, 'VERSION_CONFLICT', `Setting ${key} changed; reload it.`, { key, currentVersion: s.versions[key] });
    }
    if (key === 'timezone' && (typeof value !== 'string' || !validTimeZone(value))) {
      throw validation([{ path: '/timezone', message: 'Unknown timezone.' }]);
    }
  }
  for (const [key, value] of updates) {
    if (value === null || value === undefined) continue;
    if (key === 'timezone' && typeof value === 'string') s.timezone = value;
    if (key === 'idleUnloadSeconds') s.idleUnloadSeconds = Number(value);
    if (key === 'setupCompleted') s.setupCompleted = value === true;
    if (key === 'setupState') s.setupState = asObj(value);
    s.versions[key] = (s.versions[key] ?? 0) + 1;
  }
  audit('settings.updated', { type: 'settings' }, { keys: updates.map(([k]) => k).sort() });
  return ok(settingsOut());
});

route('GET', `${P}/system/status`, () => {
  const s = st();
  const at = nowIso();
  const check = (group: string, name: string, status: 'passed' | 'warning' | 'failed', detail: string) => ({ group, name, status, detail, checkedAt: at });
  const checks = [
    check('device', 'architecture', 'passed', 'linux/arm64'),
    check('device', 'appliance architecture', 'passed', 'arm64'),
    check('database', 'postgresql', 'passed', 'Reachable'),
    check('database', 'pgvector', 'passed', 'Installed'),
    s.flags.lowDisk
      ? check('storage', 'disk', 'warning', '38 GiB free of 1863 GiB')
      : check('storage', 'disk', 'passed', '1204 GiB free of 1863 GiB'),
    s.flags.runtimeDown
      ? check('runtime', 'runtime daemon', 'failed', 'Unreachable: ConnectError')
      : check('runtime', 'runtime daemon', 'passed', 'Reachable'),
    check('runtime', 'nvidia container runtime', 'passed', 'CDI devices available'),
    check('models', 'gpu', 'passed', 'NVIDIA GB10'),
    check('network', 'callback tunnel', 'passed', `${CALLBACK_BASE} reachable`),
  ];
  const failed = checks.some((c) => c.status === 'failed');
  const warned = checks.some((c) => c.status === 'warning');
  return ok({
    status: failed || warned ? 'degraded' : 'healthy',
    profile: 'mock',
    version: '0.1.0',
    architecture: 'linux/arm64',
    checks,
    runtime: { adapter: 'mock' },
  });
});

route('GET', `${P}/system/memory`, () => ok(memoryOut()));

route('GET', `${P}/audit-events`, (ctx) => {
  let items = st().audit;
  const action = ctx.query.get('action');
  if (action) items = items.filter((a) => (action.endsWith('*') ? a.action.startsWith(action.slice(0, -1)) : a.action === action));
  const outcome = ctx.query.get('outcome');
  if (outcome) items = items.filter((a) => a.outcome === outcome);
  const since = ctx.query.get('since');
  if (since) items = items.filter((a) => a.createdAt >= since);
  const limit = Number(ctx.query.get('limit') ?? 50);
  return ok(page(items.slice(0, limit)));
});

// --- Backups and diagnostics -------------------------------------------------------------------

function backupsNotConfigured(): ApiErr {
  return new ApiErr(409, 'BACKUPS_NOT_CONFIGURED', 'Backups are not configured on this device (CQ_BACKUP_DIR). Use `crewquarters backup create` on the device.');
}

route('GET', `${P}/system/backups`, (ctx) => {
  const s = st();
  const limit = Math.min(200, Math.max(1, Number(ctx.query.get('limit') ?? 50)));
  const enabled = !s.flags.backupsDisabled;
  return ok({
    items: s.backups.slice(0, limit),
    nextCursor: null,
    enabled,
    location: enabled ? BACKUP_LOCATION : null,
    retention: BACKUP_RETENTION,
    includes: BACKUP_INCLUDES,
    excludes: BACKUP_EXCLUDES,
  });
});

route('POST', `${P}/system/backups`, () => {
  const s = st();
  if (s.flags.backupsDisabled) throw backupsNotConfigured();
  const live = s.backups.find((b) => b.status === 'queued' || b.status === 'running');
  if (live) throw new ApiErr(409, 'BACKUP_IN_PROGRESS', 'A backup is already queued or running. Wait for it to finish.', { backupId: live.id });
  const backup = backupRec({ id: backupName(new Date(), randomBytes(3).toString('hex')), status: 'queued', source: 'api' });
  s.backups.unshift(backup);
  audit('system.backup_requested', { type: 'backup', id: backup.id });
  void runBackup(backup.id);
  return ok({ ...backup }, 202);
});

route('GET', `${P}/system/backups/:id/download`, (ctx) => {
  const s = st();
  if (s.flags.backupsDisabled) throw backupsNotConfigured();
  const id = param(ctx, 'id');
  const backup = s.backups.find((b) => b.id === id && b.status === 'succeeded');
  if (!backup) throw notFound('Backup', id);
  if (backup.includesMasterKey) {
    audit('system.backup_downloaded', { type: 'backup', id }, {}, 'denied');
    throw new ApiErr(403, 'BACKUP_CONTAINS_MASTER_KEY', 'This backup contains the device master key and can only be copied on the device.');
  }
  const body = gzipSync(Buffer.from(`manifest.json\n${JSON.stringify({ format: 1, name: id, platformVersion: backup.platformVersion, migrationHead: backup.migrationHead })}\n`));
  audit('system.backup_downloaded', { type: 'backup', id }, { bytes: body.length });
  ctx.res.writeHead(200, {
    'Content-Type': 'application/gzip',
    'Content-Disposition': `attachment; filename="${id}.tar.gz"`,
    'Content-Length': String(body.length),
    'Cache-Control': 'no-store',
  });
  ctx.res.end(body);
  return HANDLED;
});

/** A minimal stored (uncompressed) zip archive, enough for a real unzip to open. */
function zipStore(files: [string, string][]): Buffer {
  const locals: Buffer[] = [];
  const centrals: Buffer[] = [];
  let offset = 0;
  for (const [name, text] of files) {
    const data = Buffer.from(text);
    const fileName = Buffer.from(name);
    const crc = crc32(data);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(fileName.length, 26);
    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(data.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(fileName.length, 28);
    central.writeUInt32LE(offset, 42);
    locals.push(local, fileName, data);
    centrals.push(central, fileName);
    offset += local.length + fileName.length + data.length;
  }
  const dir = Buffer.concat(centrals);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(files.length, 8);
  end.writeUInt16LE(files.length, 10);
  end.writeUInt32LE(dir.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, dir, end]);
}

route('GET', `${P}/system/diagnostics`, (ctx) => {
  const s = st();
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z');
  const summary = {
    generatedAt: nowIso(),
    platformVersion: PLATFORM_VERSION,
    profile: 'mock',
    models: [...s.models.values()].map((m) => ({ id: m.id, downloadState: m.downloadState, memoryState: m.memoryState })),
    connections: [...s.connections.values()].map((c) => ({ provider: c.provider, status: c.status })),
    flags: s.flags,
  };
  const body = zipStore([
    ['summary.json', JSON.stringify(summary, null, 2)],
    ['logs/recent.log', '[redacted mock log]\n'],
  ]);
  audit('system.diagnostics_downloaded');
  ctx.res.writeHead(200, {
    'Content-Type': 'application/zip',
    'Content-Disposition': `attachment; filename="crewquarters-diagnostics-${stamp}.zip"`,
    'Content-Length': String(body.length),
    'Cache-Control': 'no-store',
  });
  ctx.res.end(body);
  return HANDLED;
});

// --- Catalog and installations ---------------------------------------------------------------------

route('GET', `${P}/catalog/agents`, () => ok(page([...st().catalog.values()].sort((a, b) => a.manifest.agentId.localeCompare(b.manifest.agentId)).map(catalogOut))));
route('GET', `${P}/catalog/agents/:agentId`, (ctx) => {
  const entry = st().catalog.get(param(ctx, 'agentId'));
  if (!entry) throw notFound('Agent', param(ctx, 'agentId'));
  return ok(catalogOut(entry));
});
route('GET', `${P}/catalog/agents/:agentId/versions/:version`, (ctx) => ok(findVersion(param(ctx, 'agentId'), param(ctx, 'version')).version));

function validateConfig(schema: Obj, config: Obj): void {
  const required = Array.isArray(schema.required) ? (schema.required as string[]) : [];
  const props = asObj(schema.properties);
  const errors: { path: string; message: string }[] = [];
  for (const key of required) {
    if (config[key] === undefined || config[key] === null || config[key] === '') errors.push({ path: `/config/${key}`, message: 'This field is required.' });
  }
  for (const [key, value] of Object.entries(config)) {
    const p = asObj(props[key]);
    if (Array.isArray(p.enum) && value !== undefined && !p.enum.includes(value)) errors.push({ path: `/config/${key}`, message: 'Not an allowed value.' });
    if (typeof value === 'number') {
      if (typeof p.minimum === 'number' && value < p.minimum) errors.push({ path: `/config/${key}`, message: `Must be at least ${p.minimum}.` });
      if (typeof p.maximum === 'number' && value > p.maximum) errors.push({ path: `/config/${key}`, message: `Must be at most ${p.maximum}.` });
    }
    if (key === 'timezone' && typeof value === 'string' && !validTimeZone(value)) errors.push({ path: '/config/timezone', message: 'Unknown timezone.' });
  }
  if (errors.length) throw new ApiErr(422, 'CONFIG_INVALID', 'The configuration is not valid.', { errors });
}

function instOrThrow(id: string) {
  const inst = st().installations.get(id);
  if (!inst) throw notFound('Installation', id);
  return inst;
}

route('GET', `${P}/agent-installations`, () => ok(page([...st().installations.values()].map(installationOut))));
route('GET', `${P}/agent-installations/:id`, (ctx) => ok(installationOut(instOrThrow(param(ctx, 'id')))));

route('POST', `${P}/agent-installations`, (ctx) => {
  const agentId = str(ctx.body.agentId) ?? '';
  const { version } = findVersion(agentId, str(ctx.body.version) ?? null);
  if (!deepEqual(asObj(ctx.body.approvedPermissions), version.permissions)) {
    throw new ApiErr(409, 'PERMISSION_MISMATCH', 'Approved permissions must equal the permissions this version requests.', {
      requested: version.permissions,
    });
  }
  const config = { ...defaultsFrom(version.configurationSchema), ...asObj(ctx.body.config) };
  validateConfig(version.configurationSchema, config);
  const inst = createInstallation(agentId, config);
  inst.enabled = ctx.body.enabled !== false;
  const bindings = asObj(ctx.body.modelBindings);
  if (Object.keys(bindings).length) inst.modelBindings = bindings as Record<string, string>;
  audit('agent.installed', { type: 'installation', id: inst.id }, { agentId, version: version.version, capabilities: inst.capabilities });
  return ok(installationOut(inst), 201);
});

route('PATCH', `${P}/agent-installations/:id`, (ctx) => {
  const inst = instOrThrow(param(ctx, 'id'));
  if (ctx.body.version !== inst.version) {
    throw new ApiErr(409, 'VERSION_CONFLICT', 'This installation changed; reload it.', { currentVersion: inst.version });
  }
  const newVersion = str(ctx.body.agentVersion);
  const approved = ctx.body.approvedPermissions;
  if (newVersion) {
    const { version } = findVersion(inst.agentId, newVersion);
    if (!deepEqual(asObj(approved), version.permissions)) {
      throw new ApiErr(409, 'REAPPROVAL_REQUIRED', 'Approve the permissions this version requests before updating.', {
        requested: version.permissions,
      });
    }
    inst.agentVersion = version.version;
    inst.agentVersionId = version.id;
    inst.requestedPermissions = version.permissions;
    inst.approvedPermissions = version.permissions;
    inst.needsReapproval = false;
    audit('agent.updated', { type: 'installation', id: inst.id }, { version: version.version });
  } else if (approved !== undefined && approved !== null) {
    if (!deepEqual(asObj(approved), inst.requestedPermissions)) {
      throw new ApiErr(409, 'PERMISSION_MISMATCH', 'Approved permissions must equal the requested permissions.');
    }
  }
  if (ctx.body.config !== undefined && ctx.body.config !== null) {
    const { version } = findVersion(inst.agentId, inst.agentVersion);
    const config = asObj(ctx.body.config);
    validateConfig(version.configurationSchema, config);
    inst.config = config;
    audit('agent.configured', { type: 'installation', id: inst.id });
  }
  if (typeof ctx.body.enabled === 'boolean') inst.enabled = ctx.body.enabled;
  if (ctx.body.modelBindings) inst.modelBindings = asObj(ctx.body.modelBindings) as Record<string, string>;
  inst.capabilities = capabilitiesFor(inst.approvedPermissions, inst.config);
  inst.version += 1;
  inst.updatedAt = nowIso();
  return ok(installationOut(inst));
});

route('DELETE', `${P}/agent-installations/:id`, (ctx) => {
  const inst = instOrThrow(param(ctx, 'id'));
  const s = st();
  s.installations.delete(inst.id);
  for (const [id, sch] of s.schedules) if (sch.installationId === inst.id) s.schedules.delete(id);
  audit('agent.uninstalled', { type: 'installation', id: inst.id }, { agentId: inst.agentId });
  return noContent();
});

// --- Runs and input ---------------------------------------------------------------------------------

function runOrThrow(id: string): RunRec {
  const run = st().runs.get(id);
  if (!run) throw notFound('Run', id);
  return run;
}

route('POST', `${P}/runs`, (ctx) => {
  const inst = instOrThrow(str(ctx.body.installationId) ?? '');
  if (st().flags.runtimeDown) throw new ApiErr(503, 'RUNTIME_UNAVAILABLE', 'The agent runtime is unavailable.');
  const readiness = readinessFor(inst);
  if (!readiness.ready) {
    throw new ApiErr(409, 'NOT_READY', 'This agent is not ready to run.', { checks: readiness.checks.filter((c) => c.status !== 'ok') });
  }
  const run = createRun(inst);
  audit('run.created', { type: 'run', id: run.id }, { agentId: inst.agentId, trigger: 'manual' });
  startRun(run);
  return ok(runOut(run), 201);
});

route('GET', `${P}/runs`, (ctx) => {
  let runs = [...st().runs.values()].reverse();
  const inst = ctx.query.get('installationId');
  if (inst) runs = runs.filter((r) => r.installationId === inst);
  const states = ctx.query.getAll('state').flatMap((s) => s.split(','));
  if (states.length) runs = runs.filter((r) => states.includes(r.state));
  const trigger = ctx.query.get('trigger');
  if (trigger) runs = runs.filter((r) => r.trigger === trigger);
  const limit = Number(ctx.query.get('limit') ?? 50);
  return ok(page(runs.slice(0, limit).map(runOut)));
});

route('GET', `${P}/runs/:id`, (ctx) => ok(runOut(runOrThrow(param(ctx, 'id')))));

route('POST', `${P}/runs/:id/cancel`, (ctx) => {
  const run = runOrThrow(param(ctx, 'id'));
  if (isTerminalState(run.state)) throw new ApiErr(409, 'INVALID_STATE', 'This run has already finished.');
  cancelRun(run);
  audit('run.cancel_requested', { type: 'run', id: run.id });
  return ok(runOut(run));
});

route('POST', `${P}/runs/:id/retry`, (ctx) => {
  const run = runOrThrow(param(ctx, 'id'));
  if (!(run.state === 'INTERRUPTED' || (run.state === 'FAILED' && run.retryable))) {
    throw new ApiErr(409, 'NOT_RETRYABLE', 'Only interrupted or retryable failed runs can be retried.');
  }
  run.currentAttempt += 1;
  run.error = null;
  run.result = null;
  run.retryable = false;
  run.cancelRequested = false;
  run.finishedAt = null;
  run.acknowledgedAt = nowIso();
  setRunState(run, 'QUEUED', { reason: 'Retried by the owner' });
  audit('run.retried', { type: 'run', id: run.id }, { attempt: run.currentAttempt });
  startRun(run);
  return ok(runOut(run));
});

route('POST', `${P}/runs/:id/acknowledge`, (ctx) => {
  const run = runOrThrow(param(ctx, 'id'));
  run.acknowledgedAt = nowIso();
  return ok(runOut(run));
});

route('GET', `${P}/runs/:id/events/history`, (ctx) => {
  const run = runOrThrow(param(ctx, 'id'));
  const after = Number(ctx.query.get('after') ?? 0);
  const limit = Number(ctx.query.get('limit') ?? 200);
  return ok(run.events.filter((e) => e.sequence > after).slice(0, limit));
});

route('GET', `${P}/runs/:id/events`, (ctx) => {
  const run = runOrThrow(param(ctx, 'id'));
  const s = st();
  const lastEventId = (ctx.req.headers['last-event-id'] as string | undefined) ?? null;
  const after = ctx.query.get('after');
  s.sseLog.push({ runId: run.id, lastEventId, after, at: nowIso() });
  if (s.flags.sseFail > 0) {
    s.flags.sseFail -= 1;
    throw new ApiErr(503, 'STREAM_UNAVAILABLE', 'Live updates are temporarily unavailable.');
  }
  let cursor = Number(lastEventId ?? after ?? 0) || 0;
  const res = ctx.res;
  sseHeaders(res);
  res.write('retry: 1000\n\n');
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    unsubscribe();
    clearInterval(keepalive);
    runStreamClosers.delete(close);
    res.end();
  };
  const flush = () => {
    if (closed) return;
    for (const e of run.events) {
      if (e.sequence > cursor) {
        res.write(sseEvent(e.type, e, e.sequence));
        cursor = e.sequence;
      }
    }
    if (isTerminalState(run.state)) {
      res.write(sseEvent('end', { state: run.state }));
      close();
    }
  };
  const unsubscribe = onRun(run.id, flush);
  const keepalive = setInterval(() => res.write(': keepalive\n\n'), 15000);
  runStreamClosers.add(close);
  ctx.req.on('close', close);
  flush();
  return HANDLED;
});

function inputOut(req: InputRequestOut): InputRequestOut {
  return req;
}

route('GET', `${P}/input-requests`, (ctx) => {
  const state = ctx.query.get('state') ?? 'pending';
  const runId = ctx.query.get('runId');
  const items = [...st().inputs.values()]
    .filter((i) => (state === 'all' ? true : i.state === state) && (!runId || i.runId === runId))
    .reverse();
  return ok(page(items.map(inputOut)));
});

function validateAnswer(schema: Obj, value: unknown): string | null {
  if (schema.type === 'object') {
    const v = asObj(value);
    if (typeof value !== 'object' || value === null) return 'The answer must be an object.';
    for (const key of Array.isArray(schema.required) ? (schema.required as string[]) : []) {
      if (v[key] === undefined) return `"${key}" is required.`;
    }
    for (const [key, prop] of Object.entries(asObj(schema.properties))) {
      const p = asObj(prop);
      if (v[key] !== undefined && Array.isArray(p.enum) && !p.enum.includes(v[key])) return `"${key}" is not an allowed value.`;
    }
  }
  return null;
}

route('POST', `${P}/input-requests/:id/answer`, (ctx) => {
  const req = st().inputs.get(param(ctx, 'id'));
  if (!req) throw notFound('Input request', param(ctx, 'id'));
  // Same code as the control API (crewquarters_shared.runs.service.answer): state first, then version.
  if (req.state !== 'pending') throw new ApiErr(409, 'INPUT_ALREADY_CLOSED', `This request is already ${req.state}.`);
  if (ctx.body.version !== req.version) {
    throw new ApiErr(409, 'VERSION_CONFLICT', 'This request changed; reload it before answering.', { currentVersion: req.version });
  }
  const problem = validateAnswer(req.schema, ctx.body.value);
  if (problem) throw validation([{ path: '/value', message: problem }], 'INPUT_INVALID', 'The answer does not match the requested form.');
  req.state = 'answered';
  req.answer = ctx.body.value;
  req.answeredAt = nowIso();
  req.version += 1;
  const run = st().runs.get(req.runId);
  if (run) appendEvent(run, 'run.input_answered', { inputRequestId: req.id, key: req.key, title: req.title, state: 'answered' });
  audit('input.answered', { type: 'input_request', id: req.id }, { runId: req.runId, key: req.key });
  continueAfterAnswer(req.id, ctx.body.value);
  return ok(inputOut(req));
});

route('GET', `${P}/attention`, () => {
  const s = st();
  const items: Obj[] = [];
  for (const req of s.inputs.values()) {
    if (req.state === 'pending') {
      items.push({ kind: 'input_request', title: `${req.agentName ?? 'Agent'}: ${req.title}`, detail: req.prompt, runId: req.runId, inputRequestId: req.id, createdAt: req.createdAt });
    }
  }
  for (const run of s.runs.values()) {
    if ((run.state === 'FAILED' || run.state === 'INTERRUPTED') && !run.acknowledgedAt) {
      const e = asObj(run.error);
      items.push({ kind: 'failed_run', title: `${run.agentName} ${run.state === 'FAILED' ? 'failed' : 'was interrupted'}`, detail: str(e.message) ?? 'See the run for details.', runId: run.id, createdAt: run.finishedAt });
    }
  }
  for (const sch of s.schedules.values()) {
    const out = scheduleOut(sch);
    if (out.enabled && !out.ready) {
      items.push({ kind: 'schedule_blocked', title: `${out.agentName} schedule is blocked`, detail: out.blockers.map((b) => b.detail).join('; '), scheduleId: sch.id, createdAt: sch.updatedAt });
    }
  }
  for (const m of s.models.values()) {
    if (m.memoryState === 'LOAD_ERROR' || m.memoryState === 'ERROR' || m.downloadState === 'DOWNLOAD_ERROR') {
      items.push({ kind: 'model_action', title: `${m.displayName} needs attention`, detail: str(asObj(m.error).message) ?? 'The model failed.', modelId: m.id, createdAt: nowIso() });
    }
  }
  return ok({ count: items.length, items });
});

// --- Schedules ------------------------------------------------------------------------------------------

route('POST', `${P}/schedules/preview`, (ctx) => {
  const cron = str(ctx.body.cron) ?? '';
  const timezone = str(ctx.body.timezone) ?? '';
  const count = typeof ctx.body.count === 'number' ? ctx.body.count : 3;
  return ok({ cron, timezone, occurrences: occurrences(cron, timezone, count) });
});
route('GET', `${P}/schedules`, () => ok(page([...st().schedules.values()].reverse().map(scheduleOut))));
route('GET', `${P}/schedules/:id`, (ctx) => {
  const sch = st().schedules.get(param(ctx, 'id'));
  if (!sch) throw notFound('Schedule', param(ctx, 'id'));
  return ok(scheduleOut(sch));
});
route('POST', `${P}/schedules`, (ctx) => {
  const inst = instOrThrow(str(ctx.body.installationId) ?? '');
  const cron = str(ctx.body.cron) ?? '';
  const timezone = str(ctx.body.timezone) ?? '';
  occurrences(cron, timezone, 1);
  const id = newId();
  const now = nowIso();
  st().schedules.set(id, {
    id,
    installationId: inst.id,
    cron,
    timezone,
    misfirePolicy: ctx.body.misfirePolicy === 'skip' ? 'skip' : 'fire_once',
    enabled: ctx.body.enabled !== false,
    lastFiredAt: null,
    lastRunId: null,
    version: 1,
    createdAt: now,
    updatedAt: now,
  });
  audit('schedule.created', { type: 'schedule', id }, { cron, timezone });
  const sch = st().schedules.get(id);
  return ok(sch ? scheduleOut(sch) : null, 201);
});
route('PATCH', `${P}/schedules/:id`, (ctx) => {
  const sch = st().schedules.get(param(ctx, 'id'));
  if (!sch) throw notFound('Schedule', param(ctx, 'id'));
  if (ctx.body.version !== sch.version) throw new ApiErr(409, 'VERSION_CONFLICT', 'This schedule changed; reload it.', { currentVersion: sch.version });
  const cron = str(ctx.body.cron) ?? sch.cron;
  const timezone = str(ctx.body.timezone) ?? sch.timezone;
  occurrences(cron, timezone, 1);
  sch.cron = cron;
  sch.timezone = timezone;
  if (ctx.body.misfirePolicy === 'skip' || ctx.body.misfirePolicy === 'fire_once') sch.misfirePolicy = ctx.body.misfirePolicy;
  if (typeof ctx.body.enabled === 'boolean') sch.enabled = ctx.body.enabled;
  sch.version += 1;
  sch.updatedAt = nowIso();
  audit('schedule.updated', { type: 'schedule', id: sch.id });
  return ok(scheduleOut(sch));
});
route('DELETE', `${P}/schedules/:id`, (ctx) => {
  if (!st().schedules.delete(param(ctx, 'id'))) throw notFound('Schedule', param(ctx, 'id'));
  audit('schedule.deleted', { type: 'schedule', id: param(ctx, 'id') });
  return noContent();
});

// --- Models ------------------------------------------------------------------------------------------------

function modelOrThrow(id: string): ModelRec {
  const m = st().models.get(id);
  if (!m) throw notFound('Model', id);
  return m;
}

route('GET', `${P}/models`, () => ok(page([...st().models.values()].sort((a, b) => a.id.localeCompare(b.id)).map(modelOut))));
route('GET', `${P}/models/:id`, (ctx) => ok(modelOut(modelOrThrow(param(ctx, 'id')))));

route('POST', `${P}/models/:id/install`, (ctx) => {
  const m = modelOrThrow(param(ctx, 'id'));
  if (st().flags.lowDisk && (m.diskBytes ?? 0) > 10 * 1024 ** 3) throw new ApiErr(409, 'DISK_LOW', 'Not enough free disk space for this model.');
  if (m.downloadState === 'NOT_INSTALLED' || m.downloadState === 'DOWNLOAD_ERROR') void installModel(m);
  audit('model.install', { type: 'model', id: m.id });
  return ok(modelOut(m), 202);
});
route('POST', `${P}/models/:id/install/cancel`, (ctx) => {
  const m = modelOrThrow(param(ctx, 'id'));
  if (m.downloadState === 'DOWNLOADING') {
    const clear = ctx.body.clear === true;
    changeModel(m, {
      downloadState: 'NOT_INSTALLED',
      download: clear ? { bytesDone: 0, bytesTotal: m.diskBytes ?? null, currentFile: null } : m.download,
    });
  }
  audit('model.cancel_install', { type: 'model', id: m.id });
  return ok(modelOut(m));
});
route('POST', `${P}/models/:id/load`, (ctx) => {
  const m = modelOrThrow(param(ctx, 'id'));
  if (st().flags.runtimeDown) throw new ApiErr(503, 'RUNTIME_UNAVAILABLE', 'The agent runtime is unavailable.');
  if (m.downloadState !== 'INSTALLED') throw new ApiErr(409, 'MODEL_NOT_INSTALLED', 'Install the model before loading it.');
  const other = [...st().models.values()].find((o) => o.id !== m.id && o.generative && o.memoryState === 'READY');
  if (m.generative && other && (other.reservedBytes + (m.expectedMemoryBytes ?? 0)) > 48 * 1024 ** 3) {
    throw new ApiErr(409, 'ADMISSION_REJECTED', `Not enough memory while ${other.displayName} is loaded. Unload it first.`, {
      blockingModelId: other.id,
    });
  }
  addLease(m, 'manual', `manual-${m.id}`, 'Manual load');
  void loadModel(m).then(
    () => releaseLease(m, `manual-${m.id}`),
    () => releaseLease(m, `manual-${m.id}`),
  );
  audit('model.load', { type: 'model', id: m.id });
  return ok(modelOut(m), 202);
});
route('POST', `${P}/models/:id/unload`, (ctx) => {
  const m = modelOrThrow(param(ctx, 'id'));
  const force = ctx.body.force === true;
  if ((m.activeLeases ?? []).length > 0 && !force) {
    throw new ApiErr(409, 'MODEL_IN_USE', 'The model is in use.', { leases: m.activeLeases });
  }
  if (m.memoryState === 'READY' || m.memoryState === 'LOAD_ERROR' || m.memoryState === 'ERROR') void unloadModel(m);
  audit('model.unload', { type: 'model', id: m.id }, force ? { force } : {});
  return ok(modelOut(m), 202);
});
route('DELETE', `${P}/models/:id`, (ctx) => {
  const m = modelOrThrow(param(ctx, 'id'));
  if (m.memoryState !== 'NOT_LOADED') throw new ApiErr(409, 'MODEL_LOADED', 'Unload the model before deleting its files.');
  changeModel(m, { downloadState: 'NOT_INSTALLED', download: { bytesDone: 0, bytesTotal: m.diskBytes ?? null, currentFile: null } });
  audit('model.delete', { type: 'model', id: m.id });
  return ok(modelOut(m));
});
route('GET', `${P}/models/:id/events`, (ctx) => {
  const m = modelOrThrow(param(ctx, 'id'));
  const res = ctx.res;
  sseHeaders(res);
  res.write('retry: 2000\n\n');
  let seq = 0;
  const push = () => {
    seq += 1;
    res.write(sseEvent('model.state', modelOut(m), seq));
  };
  const unsubscribe = onModel(m.id, push);
  const keepalive = setInterval(() => res.write(': keepalive\n\n'), 15000);
  ctx.req.on('close', () => {
    unsubscribe();
    clearInterval(keepalive);
  });
  push();
  return HANDLED;
});

// --- Connections and provider profiles ---------------------------------------------------------------------

route('GET', `${P}/connections`, () => ok(page([...st().connections.values()].sort((a, b) => a.provider.localeCompare(b.provider)))));

route('POST', `${P}/connections/google/start`, (ctx) => {
  const caps = Array.isArray(ctx.body.capabilities) ? ctx.body.capabilities.filter((c): c is string => c === 'gmail.readonly' || c === 'spreadsheets') : [];
  if (!caps.length) throw validation([{ path: '/capabilities', message: 'Choose Gmail and/or Sheets.' }]);
  const state = randomBytes(12).toString('hex');
  st().oauthStates.set(state, caps);
  audit('connection.google.start', { type: 'connection', id: 'google' }, { capabilities: caps });
  // Stands in for accounts.google.com: consent is simulated by the callback itself.
  return ok({ authorizationUrl: `/api/v1/connections/google/callback?state=${state}&code=fake-code` }, 200, {
    'Set-Cookie': cookie('cq_oauth_binding', state, { httpOnly: true, path: '/api/v1/connections/google', maxAge: 600 }),
  });
});
/** Simulated broker OAuth callback (the real one is served by the capability broker). */
route('GET', `${P}/connections/google/callback`, (ctx) => {
  const s = st();
  const state = ctx.query.get('state') ?? '';
  const caps = s.oauthStates.get(state);
  s.oauthStates.delete(state);
  let location = '/connections/google?result=connected';
  if (!caps || ctx.cookies.cq_oauth_binding !== state) location = '/connections/google?result=error&code=OAUTH_STATE_INVALID';
  else if (s.flags.googleDeny) location = '/connections/google?result=error&code=OAUTH_DENIED';
  else {
    const current = s.connections.get('google');
    const granted = new Set([...(current?.status === 'CONNECTED' ? current.grantedCapabilities : []), ...caps]);
    setConnection('google', { status: 'CONNECTED', grantedCapabilities: [...granted].sort(), account: 'demo@example.com', detail: null, lastCheckedAt: nowIso() });
    audit('connection.google.connected', { type: 'connection', id: 'google' }, { capabilities: [...granted] });
  }
  return {
    status: 303,
    headers: {
      Location: location,
      'Set-Cookie': cookie('cq_oauth_binding', '', { httpOnly: true, path: '/api/v1/connections/google', maxAge: 0 }),
    },
  };
}, false);

route('POST', `${P}/connections/google/test`, () => {
  const c = st().connections.get('google');
  if (!c || c.status === 'NOT_CONNECTED') throw new ApiErr(409, 'NEEDS_CONNECTION', 'Connect Google first.');
  if (c.status === 'CONNECTED') setConnection('google', { lastCheckedAt: nowIso() });
  return ok(st().connections.get('google'));
});
route('DELETE', `${P}/connections/google`, () => {
  setConnection('google', { status: 'NOT_CONNECTED', grantedCapabilities: [], account: null, detail: null, lastCheckedAt: nowIso() });
  audit('connection.google.disconnected', { type: 'connection', id: 'google' });
  return noContent();
});

route('PUT', `${P}/connections/twilio`, (ctx) => {
  const sid = str(ctx.body.accountSid) ?? '';
  const token = str(ctx.body.authToken) ?? '';
  const from = str(ctx.body.fromNumber) ?? '';
  const errors = [];
  if (!/^AC[0-9a-fA-F]{8,}$/.test(sid)) errors.push({ path: '/accountSid', message: 'Account SID starts with AC.' });
  if (token.length < 8) errors.push({ path: '/authToken', message: 'Enter the auth token.' });
  if (!/^\+[1-9]\d{7,14}$/.test(from)) errors.push({ path: '/fromNumber', message: 'Use E.164 format, for example +14155550100.' });
  if (errors.length) throw validation(errors);
  audit('connection.twilio.saved', { type: 'connection', id: 'twilio' });
  return ok(setConnection('twilio', {
    status: 'CONNECTED',
    grantedCapabilities: ['call.fixed_script'],
    lastCheckedAt: nowIso(),
    account: `AC••••${sid.slice(-4)}`,
    detail: `Caller number ••••${from.slice(-4)}. Callback status verified.`,
  }));
});
route('POST', `${P}/connections/twilio/test`, () => {
  const c = st().connections.get('twilio');
  if (!c || c.status === 'NOT_CONNECTED') throw new ApiErr(409, 'NEEDS_CONNECTION', 'Save Twilio credentials first.');
  return ok(setConnection('twilio', { lastCheckedAt: nowIso() }));
});
route('POST', `${P}/connections/twilio/test-call`, (ctx) => {
  const s = st();
  if (ctx.body.confirm !== true) throw validation([{ path: '/confirm', message: 'Confirm the live call.' }]);
  const to = str(ctx.body.to) ?? '';
  if (!/^\+[1-9]\d{7,14}$/.test(to)) throw validation([{ path: '/to', message: 'Use E.164 format.' }]);
  if (s.connections.get('twilio')?.status !== 'CONNECTED') throw new ApiErr(409, 'NEEDS_CONNECTION', 'Save Twilio credentials first.');
  const since = Date.now() - s.lastTestCallAt;
  if (since < 60000) {
    const retry = Math.ceil((60000 - since) / 1000);
    throw new ApiErr(429, 'RATE_LIMITED', 'Only one test call per minute.', { retryAfterSeconds: retry });
  }
  s.lastTestCallAt = Date.now();
  audit('connection.twilio.test_call', { type: 'connection', id: 'twilio' }, { to: `••••${to.slice(-2)}` });
  return ok({ placed: true, to: `••••${to.slice(-2)}`, status: 'queued' });
});
route('DELETE', `${P}/connections/twilio`, () => {
  setConnection('twilio', { status: 'NOT_CONNECTED', grantedCapabilities: [], account: null, detail: null, lastCheckedAt: nowIso() });
  audit('connection.twilio.deleted', { type: 'connection', id: 'twilio' });
  return noContent();
});

function syncCloudConnections(): void {
  for (const provider of ['openai', 'anthropic'] as const) {
    const profiles = [...st().profiles.values()].filter((p) => p.provider === provider);
    const enabled = profiles.find((p) => p.enabled);
    setConnection(provider, {
      status: enabled ? 'CONNECTED' : profiles.length ? 'DISABLED' : 'NOT_CONNECTED',
      grantedCapabilities: enabled ? enabled.allowedModels : [],
      lastCheckedAt: enabled?.lastCheckedAt ?? null,
      account: enabled?.displayName ?? null,
    });
  }
}

route('GET', `${P}/provider-profiles`, () => ok(page([...st().profiles.values()])));
route('POST', `${P}/provider-profiles`, (ctx) => {
  const provider = ctx.body.provider;
  if (provider !== 'openai' && provider !== 'anthropic') throw validation([{ path: '/provider', message: 'Choose OpenAI or Anthropic.' }]);
  const apiKey = str(ctx.body.apiKey) ?? '';
  if (apiKey.length < 8) throw validation([{ path: '/apiKey', message: 'Enter the API key.' }]);
  const id = newId();
  st().profiles.set(id, {
    id,
    provider,
    displayName: str(ctx.body.displayName) ?? provider,
    allowedModels: Array.isArray(ctx.body.allowedModels) ? ctx.body.allowedModels.map(String) : [],
    budgets: asObj(ctx.body.budgets),
    enabled: ctx.body.enabled !== false,
    status: 'CONNECTED',
    lastCheckedAt: nowIso(),
  });
  syncCloudConnections();
  audit('connection.provider_profile.created', { type: 'provider_profile', id }, { provider });
  return ok(st().profiles.get(id), 201);
});
route('DELETE', `${P}/provider-profiles/:id`, (ctx) => {
  if (!st().profiles.delete(param(ctx, 'id'))) throw notFound('Provider profile', param(ctx, 'id'));
  syncCloudConnections();
  audit('connection.provider_profile.deleted', { type: 'provider_profile', id: param(ctx, 'id') });
  return noContent();
});
route('POST', `${P}/provider-profiles/:id/test`, (ctx) => {
  const p = st().profiles.get(param(ctx, 'id'));
  if (!p) throw notFound('Provider profile', param(ctx, 'id'));
  p.lastCheckedAt = nowIso();
  audit('cloud.test_request', { type: 'provider_profile', id: p.id }, { provider: p.provider });
  return ok({ status: 'CONNECTED', detail: 'The smallest test request succeeded.', checkedAt: p.lastCheckedAt });
});

// --- Knowledge ---------------------------------------------------------------------------------------------

const MAX_UPLOAD = 25 * 1024 * 1024;
const MIME: Record<string, string> = { txt: 'text/plain', md: 'text/markdown', csv: 'text/csv', pdf: 'application/pdf', docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' };

function kbOrThrow(id: string) {
  const kb = st().kbs.get(id);
  if (!kb) throw notFound('Knowledge base', id);
  return kb;
}

route('GET', `${P}/knowledge-bases`, () => ok(page([...st().kbs.values()])));
route('POST', `${P}/knowledge-bases`, (ctx) => {
  const name = (str(ctx.body.name) ?? '').trim();
  if (!name) throw validation([{ path: '/name', message: 'Enter a name.' }]);
  if ([...st().kbs.values()].some((k) => k.name === name)) throw new ApiErr(409, 'DUPLICATE_NAME', 'A knowledge base with this name exists.');
  const id = newId();
  st().kbs.set(id, { id, name, embeddingProfile: 'local.embedding.jina-v2-small-en', embeddingDimension: 512, createdAt: nowIso() });
  audit('knowledge.created', { type: 'knowledge_base', id });
  return ok(st().kbs.get(id), 201);
});
route('GET', `${P}/knowledge-bases/:id`, (ctx) => ok(kbOrThrow(param(ctx, 'id'))));
route('DELETE', `${P}/knowledge-bases/:id`, (ctx) => {
  const kb = kbOrThrow(param(ctx, 'id'));
  st().kbs.delete(kb.id);
  for (const [id, d] of st().docs) if (d.knowledgeBaseId === kb.id) st().docs.delete(id);
  audit('knowledge.deleted', { type: 'knowledge_base', id: kb.id });
  return noContent();
});
route('GET', `${P}/knowledge-bases/:id/documents`, (ctx) => {
  const kb = kbOrThrow(param(ctx, 'id'));
  return ok(page([...st().docs.values()].filter((d) => d.knowledgeBaseId === kb.id)));
});
route('POST', `${P}/knowledge-bases/:id/documents`, async (ctx) => {
  const kb = kbOrThrow(param(ctx, 'id'));
  if (ctx.raw.length > MAX_UPLOAD + 64 * 1024) throw new ApiErr(413, 'PAYLOAD_TOO_LARGE', 'Documents are limited to 25 MiB.');
  const ct = ctx.req.headers['content-type'] ?? '';
  if (!ct.startsWith('multipart/form-data')) throw new ApiErr(415, 'UNSUPPORTED_MEDIA_TYPE', 'Upload the file as multipart/form-data.');
  const form = await new Response(new Uint8Array(ctx.raw), { headers: { 'content-type': ct } }).formData();
  const file = form.get('file');
  if (!file || typeof file === 'string') throw validation([{ path: '/file', message: 'Attach a file.' }]);
  const bytes = Buffer.from(await file.arrayBuffer());
  if (bytes.length > MAX_UPLOAD) throw new ApiErr(413, 'PAYLOAD_TOO_LARGE', 'Documents are limited to 25 MiB.');
  const name = file.name.replace(/[\\/]/g, '_').replace(/\p{Cc}/gu, '');
  const ext = (name.split('.').pop() ?? '').toLowerCase();
  const mime = MIME[ext];
  if (!mime) {
    throw new ApiErr(422, 'UNSUPPORTED_TYPE', 'Upload .txt, .md, .csv, text-based .pdf, or .docx files.', {
      errors: [{ path: '/file', message: `.${ext} files are not supported.` }],
    });
  }
  const sha256 = createHash('sha256').update(bytes).digest('hex');
  if ([...st().docs.values()].some((d) => d.knowledgeBaseId === kb.id && d.sha256 === sha256)) {
    throw new ApiErr(409, 'DUPLICATE_DOCUMENT', 'This file is already in the knowledge base.');
  }
  const id = newId();
  const now = nowIso();
  st().docs.set(id, { id, knowledgeBaseId: kb.id, name, mime, bytes: bytes.length, sha256, state: 'PENDING', extracted: null, error: null, createdAt: now, updatedAt: now });
  audit('knowledge.document.uploaded', { type: 'document', id }, { knowledgeBaseId: kb.id, bytes: bytes.length });
  void ingest(id);
  return ok(st().docs.get(id), 202);
});
function docOrThrow(ctx: Ctx) {
  const kb = kbOrThrow(param(ctx, 'id'));
  const doc = st().docs.get(param(ctx, 'docId'));
  if (!doc || doc.knowledgeBaseId !== kb.id) throw notFound('Document', param(ctx, 'docId'));
  return doc;
}
route('GET', `${P}/knowledge-bases/:id/documents/:docId`, (ctx) => ok(docOrThrow(ctx)));
route('DELETE', `${P}/knowledge-bases/:id/documents/:docId`, (ctx) => {
  const doc = docOrThrow(ctx);
  st().docs.delete(doc.id);
  audit('knowledge.document.deleted', { type: 'document', id: doc.id });
  return noContent();
});
route('POST', `${P}/knowledge-bases/:id/documents/:docId/reindex`, (ctx) => {
  const doc = docOrThrow(ctx);
  doc.state = 'PENDING';
  doc.error = null;
  doc.updatedAt = nowIso();
  void ingest(doc.id);
  return ok(doc, 202);
});

function passagesFor(kbId: string, count: number): StoredCitation[] {
  const docs = [...st().docs.values()].filter((d) => d.knowledgeBaseId === kbId && d.state === 'READY');
  if (!docs.length) return [];
  return KB_PASSAGES.slice(0, count).map((p, i) => {
    const doc = docs[i % docs.length] ?? docs[0]!;
    return {
      index: i + 1,
      citationId: `0192a000-0000-7000-9${String(i).padStart(3, '0')}-${doc.id.slice(-12)}`,
      text: p.text,
      score: Number((0.86 - i * 0.07).toFixed(2)),
      document: { id: doc.id, name: doc.name },
      locator: p.locator,
      location: p.location,
      knowledgeBaseId: kbId,
    };
  });
}

route('POST', `${P}/knowledge-bases/:id/query`, (ctx) => {
  const kb = kbOrThrow(param(ctx, 'id'));
  const query = (str(ctx.body.query) ?? '').trim();
  if (!query) throw validation([{ path: '/query', message: 'Enter a question.' }]);
  const topK = typeof ctx.body.topK === 'number' ? ctx.body.topK : 8;
  const passages = /weather|unsupported/i.test(query) ? [] : passagesFor(kb.id, Math.min(topK, 3));
  return ok({
    knowledgeBaseId: kb.id,
    passages: passages.map(({ index: _i, knowledgeBaseId: _k, ...p }) => ({ ...p, score: p.score ?? 0 })),
  });
});

// --- Chat ------------------------------------------------------------------------------------------------------

function chatOrThrow(id: string): ChatRec {
  const c = st().chats.get(id);
  if (!c) throw notFound('Chat session', id);
  return c;
}
function chatOut(c: ChatRec): Obj {
  const { messages: _m, ...rest } = c;
  void _m;
  return rest;
}

route('GET', `${P}/chat/sessions`, () => ok(page([...st().chats.values()].reverse().map(chatOut))));
route('POST', `${P}/chat/sessions`, (ctx) => {
  const modelProfile = str(ctx.body.modelProfile) ?? 'local.general.small';
  if (!modelProfile.startsWith('local.')) throw new ApiErr(403, 'PERMISSION_DENIED', 'Chat is local-only.');
  modelOrThrow(modelProfile);
  const kbId = str(ctx.body.knowledgeBaseId) ?? null;
  if (kbId) kbOrThrow(kbId);
  const id = newId();
  const now = nowIso();
  st().chats.set(id, {
    id,
    title: str(ctx.body.title) || 'New chat',
    modelProfile,
    knowledgeBaseId: kbId,
    retrievalMode: ctx.body.retrievalMode === 'only_knowledge' ? 'only_knowledge' : 'when_relevant',
    enabled: false,
    holdsModelLease: false,
    local: true,
    version: 1,
    createdAt: now,
    updatedAt: now,
    lastMessageAt: null,
    messages: [],
  });
  const c = st().chats.get(id);
  return ok(c ? chatOut(c) : null, 201);
});
route('GET', `${P}/chat/sessions/:id`, (ctx) => {
  const c = chatOrThrow(param(ctx, 'id'));
  return ok({ ...chatOut(c), messages: c.messages });
});
route('DELETE', `${P}/chat/sessions/:id`, (ctx) => {
  const c = chatOrThrow(param(ctx, 'id'));
  const m = st().models.get(c.modelProfile);
  if (m && c.enabled) releaseLease(m, c.id);
  st().chats.delete(c.id);
  return noContent();
});
function toggleChat(ctx: Ctx, enable: boolean): Resp {
  const c = chatOrThrow(param(ctx, 'id'));
  const m = modelOrThrow(c.modelProfile);
  if (enable && !c.enabled) {
    if (m.downloadState !== 'INSTALLED') throw new ApiErr(409, 'MODEL_NOT_INSTALLED', 'Install the model before enabling chat.');
    addLease(m, 'chat', c.id, `Chat: ${c.title}`);
    void loadModel(m).catch(() => undefined);
    c.enabled = true;
    c.holdsModelLease = true;
  } else if (!enable && c.enabled) {
    releaseLease(m, c.id);
    c.enabled = false;
    c.holdsModelLease = false;
  }
  c.version += 1;
  c.updatedAt = nowIso();
  audit(enable ? 'chat.enabled' : 'chat.disabled', { type: 'chat_session', id: c.id }, { model: c.modelProfile });
  return ok(chatOut(c));
}
route('POST', `${P}/chat/sessions/:id/enable`, (ctx) => toggleChat(ctx, true));
route('POST', `${P}/chat/sessions/:id/disable`, (ctx) => toggleChat(ctx, false));

route('POST', `${P}/chat/sessions/:id/messages`, (ctx) => {
  const c = chatOrThrow(param(ctx, 'id'));
  if (!c.enabled) throw new ApiErr(409, 'CHAT_DISABLED', 'Enable chat before sending messages.');
  const model = modelOrThrow(c.modelProfile);
  if (model.memoryState !== 'READY') throw new ApiErr(409, 'MODEL_NOT_READY', 'The model is still loading.');
  const content = (str(ctx.body.content) ?? '').trim();
  if (!content) throw validation([{ path: '/content', message: 'Type a message.' }]);
  const now = nowIso();
  const user: ChatMessageOut = { id: newId(), role: 'user', content, status: 'complete', citations: [], model: null, provider: null, usage: null, createdAt: now };
  const unsupported = /weather|unsupported/i.test(content);
  const citations = c.knowledgeBaseId && !unsupported ? passagesFor(c.knowledgeBaseId, 2) : [];
  const assistant: ChatMessageOut = {
    id: newId(),
    role: 'assistant',
    content: '',
    status: 'streaming',
    citations: citations,
    model: c.modelProfile,
    provider: 'local',
    usage: null,
    createdAt: nowIso(),
  };
  c.messages.push(user, assistant);
  c.lastMessageAt = now;
  let answer: string;
  if (c.knowledgeBaseId && citations.length === 0 && c.retrievalMode === 'only_knowledge') {
    answer = 'I could not find this in the knowledge base.';
  } else if (citations.length > 0) {
    answer = 'Refunds are processed within 14 days of the request [1]. Exceptions need approval from the support lead first [2].';
  } else if (unsupported) {
    answer = "I don't have reliable information about that in your documents, so I can't answer it with sources.";
  } else {
    answer = 'Hello! I am running locally on this device. Ask me anything about your documents.';
  }
  const res = ctx.res;
  sseHeaders(res);
  res.write(sseEvent('message', { userMessage: user, assistantMessageId: assistant.id, citations }));
  let aborted = false;
  ctx.req.on('close', () => {
    if (assistant.status === 'streaming') {
      aborted = true;
      assistant.status = 'stopped';
    }
  });
  void (async () => {
    const words = answer.split(/(?<= )/);
    for (const w of words) {
      await sleep(Math.max(20, st().speedMs / 4));
      if (aborted) return;
      assistant.content += w;
      res.write(sseEvent('delta', { text: w }));
    }
    assistant.status = 'complete';
    assistant.usage = { inputTokens: 120, outputTokens: words.length };
    res.write(sseEvent('done', assistant));
    res.end();
  })();
  return HANDLED;
});

route('GET', `${P}/chat/sessions/:id/messages/:messageId/citations/:citationId`, (ctx) => {
  const c = chatOrThrow(param(ctx, 'id'));
  const message = c.messages.find((m) => m.id === param(ctx, 'messageId'));
  const cited = (message?.citations ?? []).map((x) => x as unknown as StoredCitation).find((x) => x.citationId === param(ctx, 'citationId'));
  if (!cited) throw notFound('Citation', param(ctx, 'citationId'));
  const doc = st().docs.get(cited.document.id);
  return ok({ ...cited, documentAvailable: !!doc, documentState: doc?.state ?? null });
});

// --- Mock controls -----------------------------------------------------------------------------------------

async function mockControl(ctx: Ctx): Promise<Result> {
  const s = st();
  const on = ctx.body.on !== false;
  switch (`${ctx.method} ${ctx.path}`) {
    case 'POST /__mock/reset': {
      const scenario = (str(ctx.body.scenario) ?? 'ready') as Scenario;
      if (!SCENARIOS.includes(scenario)) throw validation([{ path: '/scenario', message: `Use one of ${SCENARIOS.join(', ')}` }]);
      applyScenario(scenario);
      return ok({ scenario });
    }
    case 'POST /__mock/speed':
      s.speedMs = typeof ctx.body.ms === 'number' ? ctx.body.ms : 300;
      return ok({ ms: s.speedMs });
    case 'POST /__mock/offline':
      s.flags.offline = on;
      return ok({ offline: on });
    case 'POST /__mock/runtime-down':
      s.flags.runtimeDown = on;
      return ok({ runtimeDown: on });
    case 'POST /__mock/low-disk':
      s.flags.lowDisk = on;
      return ok({ lowDisk: on });
    case 'POST /__mock/backups-disabled':
      s.flags.backupsDisabled = on;
      return ok({ backupsDisabled: on });
    case 'POST /__mock/google-deny':
      s.flags.googleDeny = on;
      return ok({ googleDeny: on });
    case 'POST /__mock/google-expired':
      setConnection('google', {
        status: 'NEEDS_ATTENTION',
        detail: 'Google access expired (test-mode 7-day limit). Reconnect Google.',
        lastCheckedAt: nowIso(),
      });
      audit('connection.google.expired', { type: 'connection', id: 'google' }, {}, 'failure');
      return ok({ google: 'NEEDS_ATTENTION' });
    case 'POST /__mock/sse': {
      const mode = str(ctx.body.mode);
      if (mode === 'drop') {
        const n = runStreamClosers.size;
        [...runStreamClosers].forEach((close) => close());
        return ok({ dropped: n });
      }
      if (mode === 'fail') {
        s.flags.sseFail = typeof ctx.body.count === 'number' ? ctx.body.count : 3;
        return ok({ failNext: s.flags.sseFail });
      }
      throw validation([{ path: '/mode', message: "Use 'drop' or 'fail'." }]);
    }
    case 'GET /__mock/sse-log':
      return ok(s.sseLog);
    case 'GET /__mock/google/consent':
      // Kept for manual use; the real flow goes straight to the callback below.
      return { status: 303, headers: { Location: `/api/v1/connections/google/callback?${ctx.query.toString()}&code=fake-code` } };
    case 'POST /__mock/new-version': {
      const agentId = str(ctx.body.agentId) ?? 'daily-gmail-digest';
      const entry = s.catalog.get(agentId);
      if (!entry) throw notFound('Agent', agentId);
      const prev = latestVersion(entry);
      const [major, minor] = prev.version.split('.').map(Number);
      const version = `${major ?? 0}.${(minor ?? 0) + 1}.0`;
      const permissions = { ...prev.permissions, cloudProviders: ['openai'] };
      entry.versions.push(versionFrom(entry.manifest, version, permissions));
      for (const inst of s.installations.values()) {
        if (inst.agentId === agentId) {
          inst.needsReapproval = true;
          inst.requestedPermissions = permissions;
          inst.updatedAt = nowIso();
        }
      }
      return ok({ agentId, version });
    }
    case 'POST /__mock/model-error': {
      const m = modelOrThrow(str(ctx.body.modelId) ?? 'local.general.small');
      changeModel(m, { memoryState: 'LOAD_ERROR', stage: null, reservedBytes: 0, error: { code: 'LOAD_FAILED', message: 'vLLM exited during Loading weights (out of memory).' } });
      return ok(modelOut(m));
    }
    case 'POST /__mock/input-request': {
      if (!s.owner) throw new ApiErr(409, 'NO_OWNER', 'Reset to the ready or populated scenario first.');
      return ok(createPendingInputRequest());
    }
    case 'GET /__mock/state':
      return ok({
        gen: s.gen,
        owner: s.owner?.user.username ?? null,
        runs: [...s.runs.values()].map((r) => ({ id: r.id, agentId: r.agentId, state: r.state, events: r.events.length })),
        models: [...s.models.values()].map((m) => ({ id: m.id, downloadState: m.downloadState, memoryState: m.memoryState, leases: m.activeLeases?.length ?? 0 })),
        flags: s.flags,
      });
    default:
      throw new ApiErr(404, 'NOT_FOUND', `No mock control ${ctx.method} ${ctx.path}`);
  }
}

// --- Static SPA --------------------------------------------------------------------------------------------------

const TYPES: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.json': 'application/json',
  '.woff2': 'font/woff2',
  '.txt': 'text/plain; charset=utf-8',
  '.webmanifest': 'application/manifest+json',
};

function serveStatic(root: string, path: string, res: ServerResponse): void {
  const safe = normalize(decodeURIComponent(path)).replace(/^(\.\.[/\\])+/, '');
  let file = join(root, safe);
  if (!file.startsWith(root) || !existsSync(file) || statSync(file).isDirectory()) {
    // Only real asset requests 404; app routes may contain dots (e.g. /models/local.general.small).
    if (path.startsWith('/assets/') || Object.hasOwn(TYPES, extname(path).toLowerCase())) {
      res.writeHead(404, { 'Content-Type': 'text/plain' });
      res.end('Not found');
      return;
    }
    file = join(root, 'index.html');
  }
  const ext = extname(file);
  const headers: Record<string, string> = { 'Content-Type': TYPES[ext] ?? 'application/octet-stream', 'X-Content-Type-Options': 'nosniff' };
  if (ext === '.html') Object.assign(headers, HTML_SECURITY_HEADERS, { 'Cache-Control': 'no-cache' });
  else if (path.startsWith('/assets/')) headers['Cache-Control'] = 'public, max-age=31536000, immutable';
  else headers['Cache-Control'] = 'no-cache';
  res.writeHead(200, headers);
  res.end(readFileSync(file));
}

// --- Server --------------------------------------------------------------------------------------------------------

function argValue(name: string): string | undefined {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

const port = Number(argValue('--port') ?? process.env.MOCK_PORT ?? 4010);
const staticArg = argValue('--static');
const staticRoot = staticArg ? resolve(staticArg) : null;
const initialScenario = (argValue('--scenario') ?? 'ready') as Scenario;
applyScenario(SCENARIOS.includes(initialScenario) ? initialScenario : 'ready');
const speed = argValue('--speed');
if (speed) st().speedMs = Number(speed);

async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const url = new URL(req.url ?? '/', `http://${req.headers.host ?? 'localhost'}`);
  const method = (req.method ?? 'GET').toUpperCase();
  const path = url.pathname;
  const requestId = nextRequestId();
  const isApi = path.startsWith('/api/');
  const isMock = path.startsWith('/__mock/');

  if (!isApi && !isMock) {
    if (staticRoot && (method === 'GET' || method === 'HEAD')) return serveStatic(staticRoot, path, res);
    res.writeHead(404, { 'Content-Type': 'text/plain' });
    res.end('Not found (start with --static dist to serve the UI)');
    return;
  }
  if (isApi && st().flags.offline) {
    res.writeHead(502, { 'Content-Type': 'text/plain' });
    res.end('502 Bad Gateway');
    return;
  }

  const raw = await readBody(req);
  let body: Obj = {};
  if (raw.length && (req.headers['content-type'] ?? '').includes('json')) {
    try {
      body = asObj(JSON.parse(raw.toString('utf8')));
    } catch {
      send(res, { status: 422, body: errorBody(new ApiErr(422, 'VALIDATION_FAILED', 'The body is not valid JSON.'), requestId) });
      return;
    }
  }
  const ctx: Ctx = { req, res, method, path, query: url.searchParams, cookies: parseCookies(req.headers.cookie), body, raw, requestId, params: {} };

  try {
    if (isMock) {
      const result = await mockControl(ctx);
      if (result !== HANDLED) send(res, result);
      return;
    }
    const match = routes
      .filter((r) => r.re.test(path))
      .find((r) => r.method === method);
    if (!match) {
      const exists = routes.some((r) => r.re.test(path));
      throw new ApiErr(exists ? 405 : 404, exists ? 'METHOD_NOT_ALLOWED' : 'NOT_FOUND', exists ? 'Method not allowed.' : 'Not found.');
    }
    const m = match.re.exec(path);
    match.keys.forEach((k, i) => {
      ctx.params[k] = decodeURIComponent(m?.[i + 1] ?? '');
    });

    let idemKey: string | null = null;
    let hash = '';
    if (match.auth) {
      const sess = currentSession(ctx);
      if (UNSAFE.has(method)) {
        checkOrigin(ctx);
        if (req.headers['x-csrf-token'] !== sess.csrf) throw new ApiErr(403, 'CSRF_FAILED', 'The request is missing a valid CSRF token.');
        const key = req.headers['idempotency-key'];
        if (typeof key === 'string' && key.length >= 8) {
          idemKey = `${method} ${path} ${key}`;
          hash = createHash('sha256').update(raw).digest('hex');
          const stored = st().idempotency.get(idemKey);
          if (stored) {
            if (stored.hash !== hash) throw new ApiErr(409, 'IDEMPOTENCY_KEY_REUSED', 'This Idempotency-Key was used with a different request.');
            send(res, { ...stored.resp, headers: { ...(stored.resp.headers ?? {}), 'Idempotent-Replayed': 'true' } });
            return;
          }
        }
      }
    }
    const result = await match.handler(ctx);
    if (result === HANDLED) return;
    if (idemKey && result.status < 500 && !path.endsWith('/google/start')) {
      const { 'Set-Cookie': _sc, ...headers } = result.headers ?? {};
      void _sc;
      st().idempotency.set(idemKey, { hash, resp: { ...result, headers } });
    }
    send(res, result);
  } catch (e) {
    const err = e instanceof ApiErr ? e : new ApiErr(500, 'INTERNAL', 'An unexpected error occurred.');
    if (!(e instanceof ApiErr)) console.error(e);
    if (res.headersSent) {
      res.end();
      return;
    }
    const headers: Record<string, string> = {};
    if (err.status === 429 && typeof err.details.retryAfterSeconds === 'number') headers['Retry-After'] = String(err.details.retryAfterSeconds);
    send(res, { status: err.status, body: errorBody(err, requestId), headers });
  }
}

const server = createServer((req, res) => {
  void handle(req, res);
});
server.listen(port, '127.0.0.1', () => {
  console.log(`Mock control API on http://127.0.0.1:${port}${staticRoot ? ` (serving ${staticRoot})` : ''}`);
});
