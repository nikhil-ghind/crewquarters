import { describe, expect, it } from 'vitest';
import { RUN_STATES, type ModelOut } from '../api/schema';
import { formatBytes, formatDateTime, formatDuration, formatUtc, plural, shortId } from './format';
import { cleanValue, defaultsFor, fieldGroups, fieldNameFromPath, validate, widgetFor, type JsonSchema } from './jsonSchema';
import { addedPermissionIds, groupPermissions, permissionItems, usesCloud } from './permissions';
import { redact } from './redact';
import {
  CONNECTION_STATUS,
  DEVICE_STATUS,
  MODEL_DOWNLOAD_STATUS,
  MODEL_MEMORY_STATUS,
  RUN_STATUS,
} from './status';

describe('format', () => {
  it('uses binary units', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(1024)).toBe('1.0 KiB');
    expect(formatBytes(5 * 2 ** 30)).toBe('5.0 GiB');
    expect(formatBytes(null)).toBe('Unknown');
  });
  it('formats durations', () => {
    expect(formatDuration(5)).toBe('5 s');
    expect(formatDuration(125)).toBe('2 min 5 s');
    expect(formatDuration(3700)).toBe('1 h 1 min');
    expect(formatDuration(null)).toBe('—');
  });
  it('shows the timezone and an exact UTC detail', () => {
    const text = formatDateTime('2026-09-25T04:30:00Z', 'Asia/Kolkata');
    expect(text).toMatch(/10:00/);
    expect(text).toMatch(/GMT\+5:30|IST/);
    expect(formatUtc('2026-09-25T04:30:00.000Z')).toBe('2026-09-25T04:30:00Z (UTC)');
  });
  it('shortens UUIDv7 ids by their random tail', () => {
    expect(shortId('01890000-0000-7000-8000-00000000abcd')).toBe('0000abcd');
  });
  it('pluralizes', () => {
    expect(plural(1, 'call')).toBe('1 call');
    expect(plural(3, 'call')).toBe('3 calls');
  });
});

describe('status vocabulary', () => {
  it('covers every run state with the plan labels', () => {
    for (const s of RUN_STATES) expect(RUN_STATUS[s].label).toBeTruthy();
    expect(RUN_STATUS.QUEUED.label).toBe('Waiting to start');
    expect(RUN_STATUS.LOADING_MODEL.label).toBe('Loading local model');
    expect(RUN_STATUS.WAITING_INPUT.label).toBe('Needs your input');
    expect(RUN_STATUS.SUCCEEDED.label).toBe('Completed');
    expect(RUN_STATUS.INTERRUPTED.label).toBe('Interrupted');
  });
  it('never uses an ambiguous "Active" label for models', () => {
    const labels = [...Object.values(MODEL_DOWNLOAD_STATUS), ...Object.values(MODEL_MEMORY_STATUS)].map((s) => s.label);
    expect(labels).not.toContain('Active');
    expect(MODEL_DOWNLOAD_STATUS.INSTALLED.label).toBe('Installed on disk');
    expect(MODEL_MEMORY_STATUS.READY.label).toBe('Ready');
    const states: ModelOut['memoryState'][] = ['NOT_LOADED', 'LOADING', 'READY', 'DRAINING', 'LOAD_ERROR', 'ERROR'];
    for (const s of states) expect(MODEL_MEMORY_STATUS[s]).toBeDefined();
  });
  it('uses the connection and device words from the plan', () => {
    expect(CONNECTION_STATUS.NEEDS_ATTENTION.label).toBe('Needs attention');
    expect(Object.values(DEVICE_STATUS).map((d) => d.label)).toEqual(['Healthy', 'Degraded', 'Offline']);
  });
  it('gives every status an icon (never color alone)', () => {
    for (const spec of Object.values(RUN_STATUS)) expect(spec.icon).toBeTruthy();
  });
});

const schema: JsonSchema = {
  type: 'object',
  required: ['spreadsheetId', 'timezone'],
  properties: {
    spreadsheetId: { type: 'string', minLength: 1, 'x-crewquarters-widget': 'spreadsheet' },
    timezone: { type: 'string', 'x-crewquarters-widget': 'timezone' },
    maxCalls: { type: 'integer', minimum: 1, maximum: 10, default: 3 },
    mode: { type: 'string', enum: ['a', 'b'] },
    targetDate: { type: ['string', 'null'], format: 'date', default: null, 'x-crewquarters-group': 'Advanced' },
    excluded: { type: 'array', items: { enum: ['X', 'Y'] }, default: ['X'] },
    labels: { type: 'array', items: { type: 'string' } },
    apiKey: { type: 'string', writeOnly: true },
  },
};

describe('jsonSchema', () => {
  it('picks widgets from types and platform hints', () => {
    const p = schema.properties ?? {};
    expect(widgetFor(p.spreadsheetId ?? {})).toBe('spreadsheet');
    expect(widgetFor(p.timezone ?? {})).toBe('timezone');
    expect(widgetFor(p.maxCalls ?? {})).toBe('integer');
    expect(widgetFor(p.mode ?? {})).toBe('select');
    expect(widgetFor(p.targetDate ?? {})).toBe('date');
    expect(widgetFor(p.excluded ?? {})).toBe('multiselect');
    expect(widgetFor(p.labels ?? {})).toBe('list');
    expect(widgetFor(p.apiKey ?? {})).toBe('secret');
  });
  it('groups fields with Advanced last', () => {
    const groups = fieldGroups(schema).map((g) => g.group);
    expect(groups[groups.length - 1]).toBe('Advanced');
  });
  it('collects defaults', () => {
    expect(defaultsFor(schema)).toEqual({ maxCalls: 3, targetDate: null, excluded: ['X'] });
  });
  it('validates required, ranges, enums and formats', () => {
    const errors = validate(schema, { maxCalls: 11, mode: 'c', targetDate: '25/09/2026' });
    const paths = errors.map((e) => e.path);
    expect(paths).toContain('/spreadsheetId');
    expect(paths).toContain('/timezone');
    expect(paths).toContain('/maxCalls');
    expect(paths).toContain('/mode');
    expect(paths).toContain('/targetDate');
    expect(validate(schema, { spreadsheetId: 'x', timezone: 'UTC', maxCalls: 3 })).toEqual([]);
  });
  it('supports conditional fields', () => {
    const conditional: JsonSchema = {
      type: 'object',
      required: ['other'],
      properties: { kind: { type: 'string' }, other: { type: 'string', 'x-crewquarters-visible-if': { kind: 'custom' } } },
    };
    expect(validate(conditional, { kind: 'preset' })).toEqual([]);
    expect(validate(conditional, { kind: 'custom' })).toHaveLength(1);
    expect(cleanValue(conditional, { kind: 'preset', other: 'x' })).toEqual({ kind: 'preset' });
  });
  it('maps server error paths to fields', () => {
    expect(fieldNameFromPath('/config/spreadsheetId')).toBe('spreadsheetId');
    expect(fieldNameFromPath('/spreadsheetId')).toBe('spreadsheetId');
  });
});

describe('permissions', () => {
  const perms = {
    llmProfiles: ['local.general'],
    knowledge: ['config'],
    connectors: { google: ['gmail.readonly'], twilio: ['call.fixed_script'] },
    cloudProviders: ['openai'],
    userInput: true,
  };
  it('lists each capability in the four groups', () => {
    const items = permissionItems(perms);
    expect(items.map((i) => i.id)).toEqual([
      'knowledge.search:config',
      'google.gmail.readonly',
      'twilio.call.fixed_script',
      'llm.profile:local.general',
      'cloud.openai',
      'user_input',
    ]);
    expect(groupPermissions(items).map((g) => g.group)).toEqual(['Local data', 'External services', 'Model use', 'User interaction']);
    expect(items.find((i) => i.id === 'cloud.openai')?.emphasis).toBe('cloud');
    expect(items.find((i) => i.id === 'twilio.call.fixed_script')?.emphasis).toBe('phone');
    expect(usesCloud(perms)).toEqual(['openai']);
  });
  it('highlights permissions added by a new version', () => {
    const before = { ...perms, cloudProviders: [] };
    expect([...addedPermissionIds(perms, before)]).toEqual(['cloud.openai']);
  });
});

describe('redact', () => {
  it('removes keys, tokens and phone numbers from copied text', () => {
    const out = redact('key sk-abcdefghijklmnopqrstuv Bearer abc.def token="s3cret" call +15551234567');
    expect(out).not.toContain('sk-abcdefghijklmnopqrstuv');
    expect(out).not.toContain('abc.def');
    expect(out).not.toContain('s3cret');
    expect(out).not.toContain('+15551234567');
    expect(out).toContain('••••67');
  });
});
