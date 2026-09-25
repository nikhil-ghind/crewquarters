import { FileText, X } from 'lucide-react';
import { useEffect, useId, useRef } from 'react';
import { Link } from 'react-router';
import type { StoredCitation } from '../lib/knowledge';
import { useLayout } from '../lib/breakpoints';
import { ErrorPanel, Skeleton } from './Feedback';

interface SourceDrawerProps {
  citation: StoredCitation | null;
  loading?: boolean;
  error?: unknown;
  onClose: () => void;
}

function Body({ citation, loading, error }: Omit<SourceDrawerProps, 'onClose'>) {
  if (error) return <ErrorPanel error={error} title="Could not open this source" />;
  if (loading || !citation) {
    return (
      <div className="stack-sm" role="status">
        <span className="sr-only">Loading source…</span>
        <Skeleton width="60%" />
        <Skeleton height={80} />
      </div>
    );
  }
  const available = citation.documentAvailable !== false;
  const docLink = citation.knowledgeBaseId && available
    ? `/knowledge/${encodeURIComponent(citation.knowledgeBaseId)}?document=${encodeURIComponent(citation.document.id)}`
    : null;
  return (
    <>
      <div className="stack-sm" style={{ gap: 2 }}>
        <span className="row field-label">
          <FileText size={16} aria-hidden="true" />
          <span className="break-anywhere">{citation.document.name}</span>
        </span>
        {citation.location ? <span className="muted">{citation.location}</span> : null}
        {typeof citation.score === 'number' ? (
          <span className="muted">Match score {citation.score.toFixed(2)}</span>
        ) : null}
      </div>
      <p className="field-label">Matched passage</p>
      {/* Source text is a plain text node: escaped, never interpreted as HTML. */}
      <blockquote className="passage" style={{ margin: 0 }}>
        {citation.text || 'The passage text is not available.'}
      </blockquote>
      {!available ? (
        <p className="muted">This document was deleted after the answer was written; only the stored passage remains.</p>
      ) : null}
      {docLink ? (
        <Link className="btn btn-secondary" to={docLink}>
          Open document
        </Link>
      ) : null}
    </>
  );
}

/**
 * Citation metadata and escaped passage beside the conversation (≥1280 px) or as an
 * overlay drawer on smaller screens, without losing the conversation context.
 */
export function SourceDrawer({ citation, loading, error, onClose }: SourceDrawerProps) {
  const layout = useLayout();
  const titleId = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const overlay = layout !== 'desktop';

  useEffect(() => {
    if (!overlay) {
      closeRef.current?.focus();
      return;
    }
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) dialog.showModal();
    const onCancel = (e: Event) => {
      e.preventDefault();
      onClose();
    };
    dialog?.addEventListener('cancel', onCancel);
    return () => {
      dialog?.removeEventListener('cancel', onCancel);
      if (dialog?.open) dialog.close();
    };
  }, [overlay, onClose]);

  const header = (
    <div className="drawer-header">
      <h2 id={titleId} style={{ fontSize: 16, lineHeight: '24px' }}>
        Source{citation?.index ? ` [${citation.index}]` : ''}
      </h2>
      <button ref={closeRef} type="button" className="btn btn-tertiary btn-icon" aria-label="Close source" onClick={onClose}>
        <X size={18} aria-hidden="true" />
      </button>
    </div>
  );

  if (overlay) {
    return (
      <dialog ref={dialogRef} className="drawer-overlay" aria-labelledby={titleId}>
        <div className="drawer" style={{ width: '100%', height: '100%' }}>
          {header}
          <div className="drawer-body">
            <Body citation={citation} loading={loading} error={error} />
          </div>
        </div>
      </dialog>
    );
  }
  return (
    <aside className="drawer" aria-labelledby={titleId}>
      {header}
      <div className="drawer-body">
        <Body citation={citation} loading={loading} error={error} />
      </div>
    </aside>
  );
}
