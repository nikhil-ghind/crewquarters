/**
 * Typed fixtures for component tests. Every object is checked against the generated
 * contract types (schema.d.ts), so a contract change breaks these tests at compile
 * time rather than silently drifting.
 */
import type {
  ConnectionOut,
  SettingsOut,
  AttentionOut,
  CatalogAgentOut,
  InputRequestOut,
  InstallationOut,
  MemoryOut,
  ModelOut,
  RunEventOut,
  RunOut,
  SessionOut,
  SystemStatusOut,
} from '../api/schema';

export const NOW = '2026-09-25T04:30:00Z';

export const session: SessionOut = {
  user: { id: 'u1', username: 'owner', email: null, role: 'owner', createdAt: NOW },
  csrfToken: 'csrf-token-1',
  expiresAt: '2026-10-02T04:30:00Z',
  idleExpiresAt: '2026-09-25T16:30:00Z',
};

export const settings: SettingsOut = {
  timezone: 'Asia/Kolkata',
  idleUnloadSeconds: 600,
  callbackBaseUrl: 'https://crew.example.test',
  callbackUrls: {
    googleRedirectUri: 'https://crew.example.test/api/v1/connections/google/callback',
    twilioCallbackBase: 'https://crew.example.test/api/v1/callbacks/twilio',
  },
  setupCompleted: true,
  setupState: {},
  versions: { timezone: 1, idleUnloadSeconds: 0, setupState: 3, setupCompleted: 1, callbackBaseUrl: 0 },
};

export const systemStatus: SystemStatusOut = {
  status: 'healthy',
  profile: 'dev',
  version: '0.1.0',
  architecture: 'linux/arm64',
  runtime: { adapter: 'fake' },
  checks: [
    { group: 'device', name: 'architecture', status: 'passed', detail: 'linux/arm64', checkedAt: NOW },
    { group: 'database', name: 'postgresql', status: 'passed', detail: 'Reachable', checkedAt: NOW },
    { group: 'storage', name: 'disk', status: 'passed', detail: '400 GiB free of 512 GiB', checkedAt: NOW },
    { group: 'runtime', name: 'runtime daemon', status: 'passed', detail: 'Reachable', checkedAt: NOW },
  ],
};

export const memory: MemoryOut = {
  totalBytes: 128 * 2 ** 30,
  availableBytes: 100 * 2 ** 30,
  systemReserveBytes: 24 * 2 ** 30,
  maxServingBytes: 96 * 2 ** 30,
  safetyMarginBytes: 8 * 2 ** 30,
  reservedBytes: 0,
  models: [],
};

export const model: ModelOut = {
  id: 'local.general.small',
  displayName: 'General small (8B)',
  family: 'local.general',
  backend: 'vllm',
  downloadState: 'INSTALLED',
  memoryState: 'NOT_LOADED',
  diskBytes: 5 * 2 ** 30,
  expectedMemoryBytes: 12 * 2 ** 30,
  reservedBytes: 0,
  contextLimit: 16384,
  capabilities: ['chat'],
  activeLeases: [],
};

export const connections: ConnectionOut[] = [
  { provider: 'google', displayName: 'Google', status: 'CONNECTED', grantedCapabilities: ['gmail.readonly', 'spreadsheets'], lastCheckedAt: NOW },
  { provider: 'twilio', displayName: 'Twilio', status: 'CONNECTED', grantedCapabilities: [], lastCheckedAt: NOW },
  { provider: 'openai', displayName: 'OpenAI', status: 'NOT_CONNECTED', grantedCapabilities: [], lastCheckedAt: null },
  { provider: 'anthropic', displayName: 'Anthropic', status: 'NOT_CONNECTED', grantedCapabilities: [], lastCheckedAt: null },
];

export const attention: AttentionOut = { count: 0, items: [] };

export const callerPermissions = {
  llmProfiles: [],
  knowledge: [],
  connectors: { google: ['spreadsheets'], twilio: ['call.fixed_script'] },
  cloudProviders: [],
  userInput: true,
};

export const catalogCaller: CatalogAgentOut = {
  agentId: 'caller',
  name: 'Caller',
  summary: 'Calls consenting contacts from a Google Sheet with a fixed script, after your approval.',
  publisher: 'Crewquarters',
  source: 'bundled',
  trustStatus: 'curated',
  currentVersion: '0.1.0',
  versions: ['0.1.0'],
  installed: false,
  latest: {
    id: 'v1',
    version: '0.1.0',
    image: 'localhost:5001/crewquarters/caller',
    imageDigest: 'sha256:' + 'a'.repeat(64),
    sdkProtocol: '1',
    architectures: ['linux/amd64', 'linux/arm64'],
    triggers: ['manual'],
    permissions: callerPermissions,
    resources: { cpu: 0.5, memoryMb: 256, activeTimeoutSeconds: 3600 },
    configurationSchema: {
      type: 'object',
      required: ['spreadsheetId'],
      properties: {
        spreadsheetId: { type: 'string', minLength: 1, 'x-crewquarters-widget': 'spreadsheet' },
        maxCalls: { type: 'integer', minimum: 1, maximum: 10, default: 3 },
      },
    },
    resultSchema: { 'x-crewquarters-renderer': 'crewquarters.caller/v1', type: 'object' },
    compatible: true,
    compatibilityIssues: [],
    createdAt: NOW,
  },
};

export const installation: InstallationOut = {
  id: 'inst-1',
  agentId: 'caller',
  agentName: 'Caller',
  agentVersion: '0.1.0',
  agentVersionId: 'v1',
  config: { spreadsheetId: 'sheet-1', maxCalls: 3 },
  requestedPermissions: callerPermissions,
  approvedPermissions: callerPermissions,
  capabilities: ['google.spreadsheets', 'twilio.call.fixed_script', 'user_input'],
  modelBindings: {},
  needsReapproval: false,
  enabled: true,
  version: 1,
  readiness: {
    ready: true,
    checks: [
      { name: 'permissions', status: 'ok', detail: 'Approved' },
      { name: 'connection', status: 'ok', detail: 'Google connected', resource: 'google' },
    ],
  },
  createdAt: NOW,
  updatedAt: NOW,
};

export const run: RunOut = {
  id: '01890000-0000-7000-8000-000000000001',
  installationId: 'inst-1',
  agentId: 'caller',
  agentName: 'Caller',
  agentVersion: '0.1.0',
  trigger: 'manual',
  scheduleId: null,
  scheduledFor: null,
  state: 'WAITING_INPUT',
  currentAttempt: 1,
  result: null,
  error: null,
  retryable: false,
  cancelRequested: false,
  acknowledgedAt: null,
  activeSecondsUsed: 12,
  inputWaitSecondsUsed: 30,
  activeTimeoutSeconds: 3600,
  maxInputWaitSeconds: 86400,
  usesCloud: false,
  pendingInputCount: 1,
  createdAt: NOW,
  startedAt: NOW,
  finishedAt: null,
  updatedAt: NOW,
};

export const approvalRequest: InputRequestOut = {
  id: 'ir-1',
  runId: run.id,
  agentName: 'Caller',
  key: 'confirm-calls-v1:abc',
  title: 'Approve 3 automated calls',
  prompt: 'The caller agent is ready to call 3 consenting recipients from your sheet.',
  schema: { type: 'object', required: ['choice'], properties: { choice: { type: 'string', enum: ['approve', 'cancel'] } } },
  preview: {
    blocks: [
      { type: 'table', columns: ['Row', 'Name', 'Number', 'Consent'], rows: [['2', 'Asha', '••••21', 'validated']] },
      { type: 'text', text: 'Script: Hello {name}. <b>not bold</b>' },
    ],
    choices: [
      { value: 'approve', label: 'Approve 3 calls', style: 'primary' },
      { value: 'cancel', label: 'Cancel run', style: 'secondary' },
    ],
    consequence: '3 automated calls will be placed now to the numbers above.',
  },
  state: 'pending',
  deadline: '2026-09-26T04:30:00Z',
  version: 2,
  createdAt: NOW,
  answeredAt: null,
};

export function event(sequence: number, type: string, payload: Record<string, unknown>): RunEventOut {
  return { runId: run.id, sequence, attempt: 1, type, payload, createdAt: NOW };
}
