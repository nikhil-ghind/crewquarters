import { Loader2 } from 'lucide-react';
import { forwardRef, useId, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { Link, type LinkProps } from 'react-router';

export type ButtonVariant = 'primary' | 'secondary' | 'tertiary' | 'danger';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  icon?: ReactNode;
  busy?: boolean;
  /** Label while busy, e.g. "Submitting…". */
  busyLabel?: string;
  /**
   * Why the control is disabled. Rendered next to the button and linked with
   * aria-describedby, so the reason is available to keyboard and screen-reader users.
   */
  disabledReason?: string | null;
  block?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'secondary',
    icon,
    busy = false,
    busyLabel,
    disabledReason,
    block = false,
    className,
    children,
    disabled,
    type = 'button',
    ...rest
  },
  ref,
) {
  const reasonId = useId();
  const isDisabled = disabled || busy || !!disabledReason;
  const classes = ['btn', `btn-${variant}`, block ? 'btn-block' : '', className ?? '']
    .filter(Boolean)
    .join(' ');
  const button = (
    <button
      ref={ref}
      type={type}
      className={classes}
      disabled={isDisabled}
      aria-busy={busy || undefined}
      aria-describedby={disabledReason ? reasonId : rest['aria-describedby']}
      {...rest}
    >
      {busy ? <Loader2 size={16} className="spin" aria-hidden="true" /> : icon}
      {busy && busyLabel ? busyLabel : children}
    </button>
  );
  if (!disabledReason) return button;
  return (
    <span className={`btn-with-reason${block ? ' btn-with-reason-block' : ''}`}>
      {button}
      <span id={reasonId} className="disabled-reason">
        {disabledReason}
      </span>
    </span>
  );
});

interface ButtonLinkProps extends LinkProps {
  variant?: ButtonVariant;
  icon?: ReactNode;
}

export function ButtonLink({ variant = 'secondary', icon, className, children, ...rest }: ButtonLinkProps) {
  return (
    <Link className={`btn btn-${variant} ${className ?? ''}`} {...rest}>
      {icon}
      {children}
    </Link>
  );
}
