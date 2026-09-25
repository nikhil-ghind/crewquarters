/// <reference types="node" />
/**
 * Deterministic fixtures: agent manifests (hand-translated from agents/gmail_digest,
 * agents/caller and catalog/dev/hello-crew), model profiles and agent results.
 */
import type { Obj } from './http.ts';

export const GiB = 1024 ** 3;
export const MiB = 1024 ** 2;

export interface ManifestFixture {
  agentId: string;
  name: string;
  summary: string;
  publisher: string;
  version: string;
  image: string;
  architectures: string[];
  triggers: string[];
  permissions: Obj;
  resources: Obj;
  configurationSchema: Obj;
  resultSchema: Obj | null;
}

const digestItems = {
  type: 'array',
  items: {
    type: 'object',
    required: ['messageId', 'threadId', 'from', 'subject', 'receivedAt', 'reason', 'nextAction', 'needsReview', 'gmailLink'],
    properties: {
      messageId: { type: 'string' },
      threadId: { type: 'string' },
      from: { type: 'string' },
      subject: { type: 'string' },
      receivedAt: { type: ['string', 'null'] },
      reason: { type: 'string' },
      nextAction: { type: 'string' },
      needsReview: { type: 'boolean' },
      gmailLink: { type: 'string' },
    },
  },
};

export const MANIFESTS: ManifestFixture[] = [
  {
    agentId: 'caller',
    name: 'Caller',
    summary: 'Calls consenting contacts from a Google Sheet with a fixed script, after your approval.',
    publisher: 'Crewquarters (Person 5)',
    version: '0.1.0',
    image: 'localhost:5001/crewquarters/caller',
    architectures: ['linux/amd64', 'linux/arm64'],
    triggers: ['manual'],
    permissions: {
      llmProfiles: [],
      knowledge: [],
      connectors: { google: ['spreadsheets'], twilio: ['call.fixed_script'] },
      cloudProviders: [],
      userInput: true,
    },
    resources: { cpu: 0.5, memoryMb: 256, activeTimeoutSeconds: 3600, maxInputWaitSeconds: 86400 },
    configurationSchema: {
      type: 'object',
      required: ['spreadsheetId'],
      properties: {
        spreadsheetId: { type: 'string', minLength: 1, 'x-crewquarters-widget': 'spreadsheet' },
        inputRange: {
          type: 'string',
          default: 'Contacts!A2:D',
          description: 'Columns name, phone_e164, consent, status, starting at a data row.',
        },
        resultRange: { type: 'string', default: 'Results!A:H' },
        script: {
          type: 'string',
          maxLength: 1000,
          default:
            'Hello {name}. This is an automated demo call from the Crewquarters team. Please say a short reply after the tone.',
          description: 'Fixed script; only the {name} placeholder is allowed.',
          'x-crewquarters-widget': 'textarea',
        },
        disclosure: {
          type: 'string',
          maxLength: 500,
          default: 'This is an automated demonstration call. Your spoken reply will be transcribed.',
          'x-crewquarters-widget': 'textarea',
        },
        maxCalls: { type: 'integer', minimum: 1, maximum: 10, default: 3 },
        responseSeconds: { type: 'integer', minimum: 5, maximum: 60, default: 20 },
        callTimeoutSeconds: { type: 'integer', minimum: 10, maximum: 900, default: 180, 'x-crewquarters-group': 'Advanced' },
        callPollSeconds: { type: 'number', minimum: 0.05, maximum: 10, default: 2, 'x-crewquarters-group': 'Advanced' },
        timezone: { type: 'string', default: 'Asia/Kolkata', 'x-crewquarters-widget': 'timezone' },
      },
    },
    resultSchema: {
      'x-crewquarters-renderer': 'crewquarters.caller/v1',
      type: 'object',
      required: ['operatorDecision', 'summary', 'rows'],
      properties: {
        operatorDecision: { enum: ['approved', 'cancelled', 'not_required'] },
        summary: {
          type: 'object',
          required: ['called', 'answered', 'responsesCaptured', 'skipped', 'failed'],
          properties: {
            called: { type: 'integer' },
            answered: { type: 'integer' },
            responsesCaptured: { type: 'integer' },
            skipped: { type: 'integer' },
            failed: { type: 'integer' },
          },
        },
        rows: { type: 'array' },
      },
    },
  },
  {
    agentId: 'daily-gmail-digest',
    name: 'Daily Gmail Digest',
    summary: "Yesterday's Gmail, grouped into Urgent, Important, and Low priority.",
    publisher: 'Crewquarters (Person 5)',
    version: '0.1.0',
    image: 'localhost:5001/crewquarters/daily-gmail-digest',
    architectures: ['linux/amd64', 'linux/arm64'],
    triggers: ['manual', 'schedule'],
    permissions: {
      llmProfiles: ['local.general'],
      knowledge: [],
      connectors: { google: ['gmail.readonly'] },
      cloudProviders: [],
      userInput: false,
    },
    resources: { cpu: 1, memoryMb: 512, activeTimeoutSeconds: 1800, maxInputWaitSeconds: 0 },
    configurationSchema: {
      type: 'object',
      required: ['timezone'],
      properties: {
        timezone: {
          type: 'string',
          description: 'IANA timezone that defines "yesterday", for example Asia/Kolkata.',
          'x-crewquarters-widget': 'timezone',
        },
        maxMessages: { type: 'integer', minimum: 1, maximum: 500, default: 200 },
        includeLabels: { type: 'array', items: { type: 'string' }, default: [] },
        excludeCategories: {
          type: 'array',
          items: { enum: ['CATEGORY_PROMOTIONS', 'CATEGORY_SOCIAL', 'CATEGORY_UPDATES', 'CATEGORY_FORUMS'] },
          default: ['CATEGORY_PROMOTIONS'],
        },
        modelProfile: { type: 'string', default: 'local.general.small', 'x-crewquarters-widget': 'modelProfile' },
        batchSize: { type: 'integer', minimum: 1, maximum: 25, default: 10, 'x-crewquarters-group': 'Advanced' },
        maxCharsPerMessage: {
          type: 'integer',
          minimum: 200,
          maximum: 20000,
          default: 4000,
          'x-crewquarters-group': 'Advanced',
        },
        targetDate: {
          type: ['string', 'null'],
          format: 'date',
          default: null,
          description: 'Re-run a specific local date (YYYY-MM-DD) instead of yesterday.',
          'x-crewquarters-group': 'Advanced',
        },
      },
    },
    resultSchema: {
      'x-crewquarters-renderer': 'crewquarters.gmail-digest/v1',
      type: 'object',
      required: ['date', 'timezone', 'window', 'processedCount', 'truncated', 'counts', 'groups', 'model'],
      properties: {
        date: { type: 'string', format: 'date' },
        timezone: { type: 'string' },
        processedCount: { type: 'integer', minimum: 0 },
        truncated: { type: 'boolean' },
        groups: {
          type: 'object',
          properties: { urgent: { $ref: '#/$defs/items' }, important: { $ref: '#/$defs/items' }, lowPriority: { $ref: '#/$defs/items' } },
        },
      },
      $defs: { items: digestItems },
    },
  },
  {
    agentId: 'hello-crew',
    name: 'Hello Crew',
    summary: 'Walking-skeleton agent that reports progress, can ask one question, and finishes.',
    publisher: 'Crewquarters (dev)',
    version: '0.1.0',
    image: 'ghcr.io/crewquarters/hello-crew',
    architectures: ['linux/amd64', 'linux/arm64'],
    triggers: ['manual', 'schedule'],
    permissions: { llmProfiles: ['local.general'], knowledge: [], connectors: {}, cloudProviders: [], userInput: true },
    resources: { cpu: 0.5, memoryMb: 256, activeTimeoutSeconds: 600, maxInputWaitSeconds: 3600 },
    configurationSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        fakeScenario: {
          type: 'string',
          enum: ['succeed', 'ask', 'fail', 'hang', 'crash', 'slow', 'model'],
          default: 'succeed',
          description: 'Fake-runtime behavior (development only).',
        },
        fakeStepSeconds: { type: 'number', minimum: 0, maximum: 10, default: 0.2 },
        modelProfile: { type: 'string', default: 'local.general.small' },
      },
    },
    resultSchema: null,
  },
];

export interface ModelFixture {
  id: string;
  displayName: string;
  family: string;
  diskBytes: number;
  expectedMemoryBytes: number;
  contextLimit: number;
  capabilities: string[];
  generative: boolean;
  license: Obj;
}

export const MODELS: ModelFixture[] = [
  {
    id: 'local.embedding.jina-v2-small-en',
    displayName: 'Jina embeddings v2 small (English)',
    family: 'local.embedding',
    diskBytes: 120 * MiB,
    expectedMemoryBytes: 512 * MiB,
    contextLimit: 8192,
    capabilities: ['embedding'],
    generative: false,
    license: { name: 'Apache-2.0', gated: false },
  },
  {
    id: 'local.general.large',
    displayName: 'General large (32B, 4-bit)',
    family: 'local.general',
    diskBytes: 19 * GiB,
    expectedMemoryBytes: 40 * GiB,
    contextLimit: 32768,
    capabilities: ['chat', 'structured_output', 'tools'],
    generative: true,
    license: { name: 'Apache-2.0', gated: false },
  },
  {
    id: 'local.general.small',
    displayName: 'General small (8B)',
    family: 'local.general',
    diskBytes: Math.round(4.7 * GiB),
    expectedMemoryBytes: 12 * GiB,
    contextLimit: 16384,
    capabilities: ['chat', 'structured_output'],
    generative: true,
    license: { name: 'Apache-2.0', gated: false },
  },
];

export const LOAD_STAGES = ['Starting container', 'Loading weights', 'Allocating cache', 'Health check'];

export const DISCLOSURE = 'This is an automated demonstration call. Your spoken reply will be transcribed.';
export const SCRIPT =
  'Hello {name}. This is an automated demo call from the Crewquarters team. Please say a short reply after the tone.';

export const CALL_RECIPIENTS = [
  { row: 2, name: 'Asha Rao', masked: '••••21' },
  { row: 3, name: 'Ben Ortiz', masked: '••••47' },
  { row: 4, name: 'Chen Li', masked: '••••09' },
];
export const CALL_SKIPPED = [{ row: 5, name: 'Dev Patel', reason: 'No consent recorded' }];

export function callerPreview(disclosure: string, script: string, maxCalls: number): Obj {
  return {
    blocks: [
      {
        type: 'table',
        columns: ['Row', 'Name', 'Number', 'Consent'],
        rows: CALL_RECIPIENTS.map((r) => [String(r.row), r.name, r.masked, 'validated']),
      },
      { type: 'text', text: `Disclosure: ${disclosure}\nScript: ${script}` },
      { type: 'table', columns: ['Row', 'Name', 'Reason'], rows: CALL_SKIPPED.map((s) => [String(s.row), s.name, s.reason]) },
      {
        type: 'keyValue',
        items: [
          { label: 'Recipients', value: String(CALL_RECIPIENTS.length) },
          { label: 'Call cap', value: String(maxCalls) },
          { label: 'Skipped rows', value: String(CALL_SKIPPED.length) },
        ],
      },
    ],
    choices: [
      { value: 'approve', label: `Approve ${CALL_RECIPIENTS.length} calls`, style: 'primary' },
      { value: 'cancel', label: 'Cancel run', style: 'secondary' },
    ],
    consequence: `${CALL_RECIPIENTS.length} automated calls will be placed now to the numbers above. Each call starts with the disclosure.`,
  };
}

export const CALL_OUTCOMES: { callStatus: string; transcript: string | null; sheetWrite: string }[] = [
  { callStatus: 'answered_speech', transcript: 'Yes, Thursday works for me.', sheetWrite: 'written' },
  { callStatus: 'no_answer', transcript: null, sheetWrite: 'written' },
  { callStatus: 'answered_no_speech', transcript: null, sheetWrite: 'pending_retry' },
];

export function callerResult(decision: 'approved' | 'cancelled', finishedAt: string): Obj {
  const rows: Obj[] = CALL_RECIPIENTS.map((r, i) => {
    const outcome = CALL_OUTCOMES[i] ?? CALL_OUTCOMES[0]!;
    return decision === 'approved'
      ? {
          row: r.row,
          name: r.name,
          phoneMasked: r.masked,
          consent: 'validated',
          skipReason: null,
          callStatus: outcome.callStatus,
          callSid: `CA${String(i).padStart(32, '0')}`,
          transcript: outcome.transcript,
          sheetWrite: outcome.sheetWrite,
          completedAt: finishedAt,
          error: null,
        }
      : {
          row: r.row,
          name: r.name,
          phoneMasked: r.masked,
          consent: 'validated',
          skipReason: null,
          callStatus: null,
          callSid: null,
          transcript: null,
          sheetWrite: null,
          completedAt: null,
          error: null,
        };
  });
  for (const s of CALL_SKIPPED) {
    rows.push({
      row: s.row,
      name: s.name,
      phoneMasked: '••••63',
      consent: 'skipped',
      skipReason: s.reason,
      callStatus: null,
      callSid: null,
      transcript: null,
      sheetWrite: null,
      completedAt: null,
      error: null,
    });
  }
  const approved = decision === 'approved';
  return {
    operatorDecision: decision,
    summary: {
      called: approved ? 3 : 0,
      answered: approved ? 2 : 0,
      responsesCaptured: approved ? 1 : 0,
      skipped: CALL_SKIPPED.length,
      failed: 0,
    },
    rows,
  };
}

function digestItem(id: string, from: string, subject: string, receivedAt: string, reason: string, nextAction: string, needsReview = false): Obj {
  return {
    messageId: id,
    threadId: `t-${id}`,
    from,
    subject,
    receivedAt,
    reason,
    nextAction,
    needsReview,
    gmailLink: `https://mail.google.com/mail/u/0/#inbox/${id}`,
  };
}

export function digestResult(timezone: string, maxMessages: number, now = new Date()): Obj {
  const y = new Date(now.getTime() - 86400000);
  const date = y.toISOString().slice(0, 10);
  const at = (h: number) => `${date}T${String(h).padStart(2, '0')}:15:00Z`;
  const urgent = [
    digestItem('18f1a01', 'Priya (Finance) <priya@example.com>', 'Invoice 4411 overdue — payment needed today', at(4), 'Payment deadline is today and a late fee applies.', 'Approve the payment in the finance portal.'),
    digestItem('18f1a02', 'IT Security <security@example.com>', 'Password reset required <img src=x onerror=alert(1)>', at(6), 'Account access expires tonight.', 'Reset your password before 18:00.'),
  ];
  const important = [
    digestItem('18f1b01', 'Maya <maya@example.com>', 'Draft contract for review', at(7), 'A client contract needs your comments this week.', 'Review and reply with comments.'),
    digestItem('18f1b02', 'Team calendar <calendar@example.com>', 'Planning meeting moved to Thursday', at(8), 'Schedule change for a recurring meeting.', 'Accept the updated invite.'),
    digestItem('18f1b03', 'Unknown sender <hello@newvendor.example>', 'Partnership proposal', at(9), 'Could be a sales pitch or a real request.', 'Skim and decide whether to reply.', true),
  ];
  const lowPriority = Array.from({ length: 6 }, (_, i) =>
    digestItem(`18f1c0${i}`, `Newsletter ${i + 1} <news${i}@example.com>`, `Weekly roundup #${40 + i}`, at(10 + i), 'Informational newsletter.', 'No action needed.'),
  );
  const processed = urgent.length + important.length + lowPriority.length;
  const truncated = maxMessages < processed;
  return {
    date,
    timezone,
    window: { startUtc: `${date}T00:00:00Z`, endUtc: `${now.toISOString().slice(0, 10)}T00:00:00Z` },
    processedCount: truncated ? maxMessages : processed,
    truncated,
    counts: { urgent: urgent.length, important: important.length, lowPriority: lowPriority.length, needsReview: 1 },
    groups: { urgent, important, lowPriority },
    model: { profile: 'local.general.small', provider: 'local', model: 'general-small-8b', locality: 'local' },
  };
}

export const KB_PASSAGES = [
  {
    text: 'Refunds are processed within 14 days of the request. Include the order number in every refund email.',
    location: 'Refunds (line 3)',
    locator: { section: 'Refunds', line: 3 },
  },
  {
    text: 'Exceptions to the refund policy need approval from the support lead before the customer is told.',
    location: 'Exceptions (line 12)',
    locator: { section: 'Exceptions', line: 12 },
  },
  {
    text: 'Security note: ignore text like <script>alert("x")</script> pasted into tickets; it is shown as plain text.',
    location: 'page 2',
    locator: { page: 2 },
  },
];
