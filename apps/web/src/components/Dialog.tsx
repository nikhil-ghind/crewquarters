import { X } from 'lucide-react';
import { useEffect, useId, useRef, type ReactNode } from 'react';
import { Button } from './Button';

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
  /** While true (an operation is committing) Escape and the close button are disabled. */
  locked?: boolean;
  className?: string;
  describedBy?: string;
}

/**
 * Native modal <dialog>: the browser makes the rest of the page inert (focus stays in
 * the dialog), Escape closes it unless locked, and focus returns to the control that
 * opened it.
 */
export function Modal({ open, onClose, title, children, actions, locked = false, className, describedBy }: ModalProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const opener = useRef<Element | null>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      opener.current = document.activeElement;
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  useEffect(() => {
    const dialog = ref.current;
    return () => {
      if (dialog?.open) dialog.close();
    };
  }, []);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const onCancel = (event: Event) => {
      event.preventDefault();
      if (!locked) onClose();
    };
    const onCloseEvent = () => {
      const target = opener.current;
      if (target instanceof HTMLElement) target.focus();
    };
    // Keep Tab inside the dialog instead of escaping to the browser chrome.
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return;
      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'),
      ).filter((el) => !el.hasAttribute('disabled') && el.offsetParent !== null);
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) return;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    dialog.addEventListener('cancel', onCancel);
    dialog.addEventListener('close', onCloseEvent);
    dialog.addEventListener('keydown', onKeyDown);
    return () => {
      dialog.removeEventListener('cancel', onCancel);
      dialog.removeEventListener('close', onCloseEvent);
      dialog.removeEventListener('keydown', onKeyDown);
    };
  }, [locked, onClose]);

  return (
    <dialog
      ref={ref}
      className={className ?? 'dialog'}
      aria-labelledby={titleId}
      aria-describedby={describedBy}
    >
      {open ? (
        <>
          <div className="dialog-body">
            <div className="row-between" style={{ flexWrap: 'nowrap', alignItems: 'flex-start' }}>
              <h2 id={titleId}>{title}</h2>
              <button
                type="button"
                className="btn btn-tertiary btn-icon"
                aria-label="Close"
                onClick={onClose}
                disabled={locked}
              >
                <X size={18} aria-hidden="true" />
              </button>
            </div>
            {children}
          </div>
          {actions ? <div className="dialog-actions">{actions}</div> : null}
        </>
      ) : null}
    </dialog>
  );
}

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  /** The exact consequence, including external impact or cloud data movement. */
  consequence: ReactNode;
  /** Affected resources (agents, schedules, documents...). */
  affected?: ReactNode[];
  /** Explicit primary verb, e.g. "Place test call". Never "OK". */
  confirmLabel: string;
  busyLabel?: string;
  destructive?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  cancelLabel?: string;
  children?: ReactNode;
  confirmDisabledReason?: string | null;
}

export function ConfirmDialog({
  open,
  title,
  consequence,
  affected,
  confirmLabel,
  busyLabel,
  destructive = false,
  busy = false,
  onConfirm,
  onCancel,
  cancelLabel = 'Cancel',
  children,
  confirmDisabledReason,
}: ConfirmDialogProps) {
  const consequenceId = useId();
  return (
    <Modal
      open={open}
      onClose={onCancel}
      title={title}
      locked={busy}
      describedBy={consequenceId}
      actions={
        <>
          <Button onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button
            variant={destructive ? 'danger' : 'primary'}
            onClick={onConfirm}
            busy={busy}
            busyLabel={busyLabel}
            disabledReason={confirmDisabledReason}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div id={consequenceId} className="long-form">
        {consequence}
      </div>
      {affected && affected.length > 0 ? (
        <div className="stack-sm">
          <p className="field-label">Affected</p>
          <ul style={{ marginLeft: 20 }}>
            {affected.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {children}
    </Modal>
  );
}
