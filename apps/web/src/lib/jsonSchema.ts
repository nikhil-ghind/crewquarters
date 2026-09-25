/**
 * A small JSON Schema (2020-12 subset) reader for generated configuration and
 * input forms. It covers what agent manifests use: object/string/integer/number/
 * boolean/array, enum, nullable types, required, min/max, minLength/maxLength,
 * pattern, format=date, defaults, and the platform UI hints
 *   x-crewquarters-widget: timezone | modelProfile | knowledgeBase | spreadsheet | textarea | secret
 *   x-crewquarters-group:  section name
 *   x-crewquarters-visible-if: { "<sibling field>": <value or [values]> }  (conditional fields)
 *
 * Validation here is only for fast, local format feedback on blur/submit. The server
 * validates again and is authoritative (section 13.17).
 */
import type { FieldError } from '../api/errors';

export interface JsonSchema {
  type?: string | string[];
  title?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
  const?: unknown;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  minItems?: number;
  maxItems?: number;
  pattern?: string;
  format?: string;
  examples?: unknown[];
  writeOnly?: boolean;
  additionalProperties?: boolean | JsonSchema;
  $ref?: string;
  $defs?: Record<string, JsonSchema>;
  'x-crewquarters-widget'?: string;
  'x-crewquarters-group'?: string;
  'x-crewquarters-visible-if'?: Record<string, unknown>;
  'x-crewquarters-renderer'?: string;
  [key: string]: unknown;
}

export type Widget =
  | 'text'
  | 'textarea'
  | 'number'
  | 'integer'
  | 'checkbox'
  | 'select'
  | 'multiselect'
  | 'list'
  | 'date'
  | 'timezone'
  | 'modelProfile'
  | 'knowledgeBase'
  | 'spreadsheet'
  | 'secret'
  | 'unsupported';

export function asSchema(value: unknown): JsonSchema {
  return typeof value === 'object' && value !== null ? (value as JsonSchema) : {};
}

function types(schema: JsonSchema): string[] {
  if (Array.isArray(schema.type)) return schema.type;
  if (typeof schema.type === 'string') return [schema.type];
  if (schema.enum) {
    const kinds = new Set(schema.enum.map((v) => (v === null ? 'null' : typeof v)));
    return [...kinds];
  }
  return [];
}

export function isNullable(schema: JsonSchema): boolean {
  return types(schema).includes('null') || (schema.enum?.includes(null) ?? false);
}

export function primaryType(schema: JsonSchema): string {
  return types(schema).find((t) => t !== 'null') ?? 'string';
}

export function resolveRef(schema: JsonSchema, root: JsonSchema): JsonSchema {
  if (!schema.$ref) return schema;
  const match = /^#\/\$defs\/(.+)$/.exec(schema.$ref);
  const target = match?.[1] ? root.$defs?.[match[1]] : undefined;
  return target ? { ...target, ...schema, $ref: undefined } : schema;
}

export function widgetFor(schema: JsonSchema): Widget {
  const hint = schema['x-crewquarters-widget'];
  if (
    hint === 'timezone' ||
    hint === 'modelProfile' ||
    hint === 'knowledgeBase' ||
    hint === 'spreadsheet' ||
    hint === 'textarea' ||
    hint === 'secret'
  ) {
    return hint;
  }
  if (schema.writeOnly || schema.format === 'password') return 'secret';
  const type = primaryType(schema);
  if (schema.enum) return 'select';
  if (type === 'boolean') return 'checkbox';
  if (type === 'integer') return 'integer';
  if (type === 'number') return 'number';
  if (type === 'array') {
    const items = schema.items ?? {};
    if (items.enum) return 'multiselect';
    if (primaryType(items) === 'string') return 'list';
    return 'unsupported';
  }
  if (type === 'string') return schema.format === 'date' ? 'date' : 'text';
  return 'unsupported';
}

/** A readable label from a property name: "maxMessages" → "Max messages". */
export function humanize(name: string): string {
  const words = name
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .trim()
    .toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export interface FieldSpec {
  name: string;
  schema: JsonSchema;
  label: string;
  required: boolean;
  widget: Widget;
  group: string;
}

export const DEFAULT_GROUP = 'General';
export const ADVANCED_GROUP = 'Advanced';

/** Fields in declaration order, grouped; "Advanced" always sorts last. */
export function fieldGroups(root: JsonSchema): { group: string; fields: FieldSpec[] }[] {
  const required = new Set(root.required ?? []);
  const groups = new Map<string, FieldSpec[]>();
  for (const [name, raw] of Object.entries(root.properties ?? {})) {
    const schema = resolveRef(raw, root);
    const group = schema['x-crewquarters-group'] ?? DEFAULT_GROUP;
    const spec: FieldSpec = {
      name,
      schema,
      label: schema.title ?? humanize(name),
      required: required.has(name),
      widget: widgetFor(schema),
      group,
    };
    groups.set(group, [...(groups.get(group) ?? []), spec]);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => (a === ADVANCED_GROUP ? 1 : 0) - (b === ADVANCED_GROUP ? 1 : 0))
    .map(([group, fields]) => ({ group, fields }));
}

export function isVisible(field: FieldSpec, value: Record<string, unknown>): boolean {
  const rule = field.schema['x-crewquarters-visible-if'];
  if (!rule) return true;
  return Object.entries(rule).every(([key, expected]) =>
    Array.isArray(expected) ? expected.includes(value[key]) : value[key] === expected,
  );
}

/** Defaults for every property that declares one. */
export function defaultsFor(root: JsonSchema): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [name, raw] of Object.entries(root.properties ?? {})) {
    const schema = resolveRef(raw, root);
    if (schema.default !== undefined) out[name] = structuredClone(schema.default);
  }
  return out;
}

function isEmpty(value: unknown): boolean {
  return value === undefined || value === null || value === '';
}

function checkValue(schema: JsonSchema, value: unknown, path: string, errors: FieldError[]): void {
  if (isEmpty(value)) return;
  const type = primaryType(schema);
  if (schema.enum && !schema.enum.some((option) => option === value)) {
    errors.push({ path, message: 'Choose one of the listed options.' });
    return;
  }
  if (type === 'integer' || type === 'number') {
    if (typeof value !== 'number' || Number.isNaN(value)) {
      errors.push({ path, message: 'Enter a number.' });
      return;
    }
    if (type === 'integer' && !Number.isInteger(value)) {
      errors.push({ path, message: 'Enter a whole number.' });
    }
    if (schema.minimum !== undefined && value < schema.minimum) {
      errors.push({ path, message: `Must be at least ${schema.minimum}.` });
    }
    if (schema.maximum !== undefined && value > schema.maximum) {
      errors.push({ path, message: `Must be at most ${schema.maximum}.` });
    }
    return;
  }
  if (type === 'string') {
    if (typeof value !== 'string') {
      errors.push({ path, message: 'Enter text.' });
      return;
    }
    if (schema.minLength !== undefined && value.length < schema.minLength) {
      errors.push({ path, message: `Enter at least ${schema.minLength} characters.` });
    }
    if (schema.maxLength !== undefined && value.length > schema.maxLength) {
      errors.push({ path, message: `Use at most ${schema.maxLength} characters.` });
    }
    if (schema.pattern) {
      try {
        if (!new RegExp(schema.pattern, 'u').test(value)) {
          errors.push({ path, message: 'The format is not valid.' });
        }
      } catch {
        // An unsupported pattern is left to the server.
      }
    }
    if (schema.format === 'date' && !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
      errors.push({ path, message: 'Use the date format YYYY-MM-DD.' });
    }
    return;
  }
  if (type === 'array') {
    if (!Array.isArray(value)) {
      errors.push({ path, message: 'Enter a list.' });
      return;
    }
    if (schema.minItems !== undefined && value.length < schema.minItems) {
      errors.push({ path, message: `Add at least ${schema.minItems}.` });
    }
    if (schema.maxItems !== undefined && value.length > schema.maxItems) {
      errors.push({ path, message: `Add at most ${schema.maxItems}.` });
    }
    value.forEach((item, index) => {
      if (schema.items) checkValue(schema.items, item, `${path}/${index}`, errors);
    });
    return;
  }
  if (type === 'boolean' && typeof value !== 'boolean') {
    errors.push({ path, message: 'Choose yes or no.' });
  }
}

/** Local validation of a top-level object against its schema. */
export function validate(root: JsonSchema, value: Record<string, unknown>): FieldError[] {
  const errors: FieldError[] = [];
  const fields = fieldGroups(root).flatMap((g) => g.fields);
  for (const field of fields) {
    if (!isVisible(field, value)) continue;
    const v = value[field.name];
    const path = `/${field.name}`;
    if (field.required && isEmpty(v) && !isNullable(field.schema)) {
      errors.push({ path, message: `${field.label} is required.` });
      continue;
    }
    checkValue(field.schema, v, path, errors);
  }
  return errors;
}

/** Drop hidden and empty optional values so the server applies its own defaults. */
export function cleanValue(root: JsonSchema, value: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  const fields = fieldGroups(root).flatMap((g) => g.fields);
  for (const field of fields) {
    if (!isVisible(field, value)) continue;
    const v = value[field.name];
    if (v === undefined || (v === '' && !field.required)) continue;
    out[field.name] = v;
  }
  // Keep keys the schema does not describe only if additional properties are allowed.
  if (root.additionalProperties !== false) {
    for (const [k, v] of Object.entries(value)) {
      if (!(k in (root.properties ?? {})) && v !== undefined) out[k] = v;
    }
  }
  return out;
}

/** Map a server field-error path ("/config/timezone" or "/timezone") to a field name. */
export function fieldNameFromPath(path: string, prefix = 'config'): string {
  const parts = path.split('/').filter(Boolean);
  if (parts[0] === prefix) parts.shift();
  if (parts[0] === 'body') parts.shift();
  if (parts[0] === prefix) parts.shift();
  return parts[0] ?? '';
}
