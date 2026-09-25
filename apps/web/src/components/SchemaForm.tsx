import type { ReactNode } from 'react';
import type { FieldError } from '../api/errors';
import {
  ADVANCED_GROUP,
  DEFAULT_GROUP,
  fieldGroups,
  isNullable,
  isVisible,
  primaryType,
  type FieldSpec,
  type JsonSchema,
} from '../lib/jsonSchema';
import { Field } from './Field';
import { Advanced } from './Layout';

export interface SelectOption {
  value: string;
  label: string;
}

export interface SchemaFormOptions {
  models?: SelectOption[];
  knowledgeBases?: SelectOption[];
  timezones?: string[];
}

interface SchemaFormProps {
  schema: JsonSchema;
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  errors?: FieldError[];
  disabled?: boolean;
  options?: SchemaFormOptions;
  /** Prefix for input ids so the error summary can link to fields. */
  idPrefix?: string;
  onBlurField?: (name: string) => void;
}

/** Text for a JSON value; objects are shown as JSON, never "[object Object]". */
export function toText(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return `${value}`;
  return JSON.stringify(value);
}

export function schemaFieldId(prefix: string, name: string): string {
  return `${prefix}-${name}`;
}

function label(value: unknown): string {
  if (value === null) return 'None';
  const text = toText(value);
  // Enum values such as CATEGORY_PROMOTIONS → "Category promotions"
  if (/^[A-Z0-9_]+$/.test(text)) {
    const words = text.toLowerCase().replace(/_/g, ' ');
    return words.charAt(0).toUpperCase() + words.slice(1);
  }
  return text;
}

/**
 * Generates a grouped configuration/input form from JSON Schema, with platform
 * widgets for timezone/model/knowledge-base/spreadsheet fields and write-only secret
 * treatment. Descriptions are rendered as plain text (never HTML).
 */
export function SchemaForm({
  schema,
  value,
  onChange,
  errors = [],
  disabled = false,
  options = {},
  idPrefix = 'cfg',
  onBlurField,
}: SchemaFormProps) {
  const groups = fieldGroups(schema);
  const errorFor = (name: string) =>
    errors.find((e) => e.path === `/${name}` || e.path.startsWith(`/${name}/`))?.message ?? null;
  const set = (name: string, v: unknown) => onChange({ ...value, [name]: v });

  const renderField = (field: FieldSpec): ReactNode => {
    if (!isVisible(field, value)) return null;
    const help = [field.schema.description, examplesText(field.schema)].filter(Boolean).join(' ');
    return (
      <SchemaField
        key={field.name}
        field={field}
        id={schemaFieldId(idPrefix, field.name)}
        value={value[field.name]}
        onChange={(v) => set(field.name, v)}
        onBlur={() => onBlurField?.(field.name)}
        error={errorFor(field.name)}
        help={help || undefined}
        disabled={disabled}
        options={options}
      />
    );
  };

  if (groups.length === 0) {
    return <p className="muted">This agent has no configuration.</p>;
  }

  return (
    <div className="stack">
      {groups.map(({ group, fields }) =>
        group === ADVANCED_GROUP ? (
          <Advanced key={group} label="Advanced settings">
            {fields.map(renderField)}
          </Advanced>
        ) : groups.length === 1 && group === DEFAULT_GROUP ? (
          <div key={group} className="stack">
            {fields.map(renderField)}
          </div>
        ) : (
          <fieldset key={group} className="fieldset">
            <legend>{group}</legend>
            {fields.map(renderField)}
          </fieldset>
        ),
      )}
    </div>
  );
}

function examplesText(schema: JsonSchema): string {
  if (!schema.examples || schema.examples.length === 0) return '';
  return `Example: ${schema.examples.map((e) => toText(e)).join(', ')}`;
}

interface SchemaFieldProps {
  field: FieldSpec;
  id: string;
  value: unknown;
  onChange: (value: unknown) => void;
  onBlur: () => void;
  error: string | null;
  help?: string;
  disabled: boolean;
  options: SchemaFormOptions;
}

function SchemaField({ field, id, value, onChange, onBlur, error, help, disabled, options }: SchemaFieldProps) {
  const { schema, widget } = field;
  const nullable = isNullable(schema);
  const common = { disabled, onBlur, id };
  const textValue = value === null || value === undefined ? '' : toText(value);

  const wrap = (control: React.ReactElement<Record<string, unknown>>, extraHelp?: string) => (
    <Field
      label={field.label}
      help={[help, extraHelp].filter(Boolean).join(' ') || undefined}
      error={error}
      required={field.required}
    >
      {control}
    </Field>
  );

  switch (widget) {
    case 'checkbox':
      return (
        <div className="field">
          <label className="check-row">
            <input
              type="checkbox"
              checked={value === true}
              onChange={(e) => onChange(e.target.checked)}
              aria-describedby={help ? `${id}-help` : undefined}
              {...common}
            />
            <span>
              <span className="field-label">{field.label}</span>
              {help ? (
                <span id={`${id}-help`} className="field-help" style={{ display: 'block' }}>
                  {help}
                </span>
              ) : null}
            </span>
          </label>
          {error ? <p className="field-error">{error}</p> : null}
        </div>
      );
    case 'select': {
      const values = (schema.enum ?? []).filter((v) => v !== null);
      return wrap(
        <select
          className="select"
          value={textValue}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === '') return onChange(nullable ? null : undefined);
            const match = values.find((v) => toText(v) === raw);
            onChange(match ?? raw);
          }}
          {...common}
        >
          {!field.required || nullable || value === undefined ? <option value="">Choose…</option> : null}
          {values.map((v) => (
            <option key={toText(v)} value={toText(v)}>
              {label(v)}
            </option>
          ))}
        </select>,
      );
    }
    case 'multiselect': {
      const choices = (schema.items?.enum ?? []).filter((v) => v !== null);
      const selected: unknown[] = Array.isArray(value) ? (value as unknown[]) : [];
      return (
        <fieldset className="fieldset" style={{ padding: 16 }} aria-describedby={help ? `${id}-help` : undefined}>
          <legend className="field-label">{field.label}</legend>
          {help ? (
            <p id={`${id}-help`} className="field-help">
              {help}
            </p>
          ) : null}
          {choices.map((choice) => (
            <label key={toText(choice)} className="check-row" style={{ minHeight: 32, padding: 0 }}>
              <input
                type="checkbox"
                disabled={disabled}
                checked={selected.includes(choice)}
                onChange={(e) =>
                  onChange(e.target.checked ? [...selected, choice] : selected.filter((s) => s !== choice))
                }
              />
              <span>{label(choice)}</span>
            </label>
          ))}
          {error ? <p className="field-error">{error}</p> : null}
        </fieldset>
      );
    }
    case 'list': {
      const items = Array.isArray(value) ? value.map(String) : [];
      return wrap(
        <textarea
          className="textarea"
          rows={3}
          value={items.join('\n')}
          onChange={(e) =>
            onChange(
              e.target.value
                .split('\n')
                .map((s) => s.trim())
                .filter(Boolean),
            )
          }
          {...common}
        />,
        'One per line.',
      );
    }
    case 'integer':
    case 'number':
      return wrap(
        <input
          className="input"
          type="number"
          inputMode={widget === 'integer' ? 'numeric' : 'decimal'}
          step={widget === 'integer' ? 1 : 'any'}
          min={schema.minimum}
          max={schema.maximum}
          value={textValue}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === '') return onChange(nullable ? null : undefined);
            onChange(Number(raw));
          }}
          {...common}
        />,
        rangeHint(schema),
      );
    case 'textarea':
      return wrap(
        <textarea
          className="textarea"
          rows={4}
          value={textValue}
          maxLength={schema.maxLength}
          onChange={(e) => onChange(e.target.value)}
          {...common}
        />,
        schema.maxLength ? `Up to ${schema.maxLength} characters.` : undefined,
      );
    case 'date':
      return wrap(
        <input
          className="input"
          type="date"
          value={textValue}
          onChange={(e) => onChange(e.target.value === '' ? (nullable ? null : undefined) : e.target.value)}
          {...common}
        />,
      );
    case 'timezone': {
      const listed = options.timezones ?? [];
      // Browsers list some zones under legacy names (Asia/Calcutta); keep the saved value selectable.
      const zones = textValue && listed.length > 0 && !listed.includes(textValue) ? [textValue, ...listed] : listed;
      return wrap(
        zones.length > 0 ? (
          <select className="select" value={textValue} onChange={(e) => onChange(e.target.value)} {...common}>
            {!textValue ? <option value="">Choose a timezone…</option> : null}
            {zones.map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </select>
        ) : (
          <input className="input" value={textValue} onChange={(e) => onChange(e.target.value)} {...common} />
        ),
        'IANA timezone name.',
      );
    }
    case 'modelProfile':
    case 'knowledgeBase': {
      const list = (widget === 'modelProfile' ? options.models : options.knowledgeBases) ?? [];
      const hasCurrent = !textValue || list.some((o) => o.value === textValue);
      return wrap(
        <select
          className="select"
          value={textValue}
          onChange={(e) => onChange(e.target.value === '' ? (nullable ? null : undefined) : e.target.value)}
          {...common}
        >
          <option value="">{widget === 'modelProfile' ? 'Choose a model…' : 'Choose a knowledge base…'}</option>
          {!hasCurrent ? <option value={textValue}>{textValue}</option> : null}
          {list.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>,
      );
    }
    case 'spreadsheet':
      return wrap(
        <input
          className="input mono"
          value={textValue}
          spellCheck={false}
          onChange={(e) => onChange(e.target.value.trim())}
          {...common}
        />,
        'The spreadsheet ID from its Google Sheets address (the part between /d/ and /edit).',
      );
    case 'secret':
      return wrap(
        <input
          className="input mono"
          type="password"
          autoComplete="off"
          value={textValue}
          onChange={(e) => onChange(e.target.value)}
          {...common}
        />,
        'Stored encrypted; it is never shown again after saving.',
      );
    case 'text':
      return wrap(
        <input
          className="input"
          value={textValue}
          maxLength={schema.maxLength}
          onChange={(e) => onChange(e.target.value)}
          {...common}
        />,
      );
    default:
      return wrap(
        <input className="input" value={textValue} disabled readOnly id={id} />,
        `This ${primaryType(schema)} setting can only be changed in the manifest.`,
      );
  }
}

function rangeHint(schema: JsonSchema): string | undefined {
  if (schema.minimum !== undefined && schema.maximum !== undefined) {
    return `Between ${schema.minimum} and ${schema.maximum}.`;
  }
  if (schema.minimum !== undefined) return `At least ${schema.minimum}.`;
  if (schema.maximum !== undefined) return `At most ${schema.maximum}.`;
  return undefined;
}
