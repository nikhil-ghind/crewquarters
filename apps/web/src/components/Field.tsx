import { AlertCircle } from 'lucide-react';
import { cloneElement, isValidElement, useId, type ReactElement, type ReactNode } from 'react';
import type { FieldError } from '../api/errors';

interface FieldProps {
  label: ReactNode;
  help?: ReactNode;
  error?: string | null;
  required?: boolean;
  /** A single form control; it receives id, aria-describedby and aria-invalid. */
  children: ReactElement<Record<string, unknown>>;
  className?: string;
}

/** Label, description and error wired to the control for assistive technology. */
export function Field({ label, help, error, required, children, className }: FieldProps) {
  const id = useId();
  const helpId = `${id}-help`;
  const errorId = `${id}-error`;
  const describedBy = [help ? helpId : null, error ? errorId : null].filter(Boolean).join(' ') || undefined;
  const control = isValidElement(children)
    ? cloneElement(children, {
        id,
        'aria-describedby': describedBy,
        'aria-invalid': error ? true : undefined,
        'aria-required': required || undefined,
      })
    : children;
  return (
    <div className={`field ${className ?? ''}`}>
      <label className="field-label" htmlFor={id}>
        {label}
        {required ? (
          <span className="field-required" aria-hidden="true">
            *
          </span>
        ) : null}
      </label>
      {control}
      {help ? (
        <p id={helpId} className="field-help">
          {help}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="field-error">
          <AlertCircle size={14} aria-hidden="true" style={{ marginTop: 3, flex: 'none' }} />
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** Summary of errors at the top of a form on submit, with links to each field. */
export function ErrorSummary({
  errors,
  labels,
  idFor,
}: {
  errors: FieldError[];
  labels?: Record<string, string>;
  idFor?: (path: string) => string | undefined;
}) {
  if (errors.length === 0) return null;
  return (
    <div className="error-summary" role="alert" tabIndex={-1}>
      <p className="field-label">
        {errors.length === 1 ? 'Fix 1 problem before continuing:' : `Fix ${errors.length} problems before continuing:`}
      </p>
      <ul>
        {errors.map((e, i) => {
          const name = e.path.split('/').filter(Boolean)[0] ?? '';
          const label = labels?.[name] ?? name;
          const target = idFor?.(e.path);
          return (
            <li key={`${e.path}-${i}`}>
              {target ? <a href={`#${target}`}>{label}</a> : label}
              {label ? ': ' : ''}
              {e.message}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/**
 * Secret input: write-only. After a secret is saved the UI shows only that it exists,
 * with Replace/Delete; it is never revealed or copied back (section 13.17).
 */
export function SecretInput({
  saved,
  value,
  onChange,
  replacing,
  onReplace,
  name,
  autoComplete = 'off',
  ...rest
}: {
  saved: boolean;
  value: string;
  onChange: (value: string) => void;
  replacing: boolean;
  onReplace: () => void;
  name: string;
  autoComplete?: string;
  id?: string;
  'aria-describedby'?: string;
  'aria-invalid'?: boolean;
}) {
  if (saved && !replacing) {
    return (
      <div className="row">
        <span className="secret-saved" id={rest.id} aria-describedby={rest['aria-describedby']}>
          Saved · hidden
        </span>
        <button type="button" className="btn btn-secondary" onClick={onReplace}>
          Replace
        </button>
      </div>
    );
  }
  return (
    <input
      {...rest}
      className="input mono"
      type="password"
      name={name}
      value={value}
      autoComplete={autoComplete}
      spellCheck={false}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}
