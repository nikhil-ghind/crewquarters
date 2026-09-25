import { useMutation, useQueryClient } from '@tanstack/react-query';
import { FileText, MoreHorizontal, RotateCw, Search, Trash2, Upload } from 'lucide-react';
import { useId, useRef, useState, type DragEvent, type FormEvent } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router';
import { idempotencyKey } from '../../api/client';
import { isApiError, remediation } from '../../api/errors';
import { useActionGuard } from '../../api/guards';
import { MAX_UPLOAD_BYTES, pendingApi } from '../../api/pending';
import { documentChunks, documentError, type DocumentOut, type KnowledgeBaseOut, type PassageOut } from '../../api/pending-contracts';
import { keys, useDocuments, useKnowledgeBase } from '../../api/queries';
import { Button } from '../../components/Button';
import { ConfirmDialog } from '../../components/Dialog';
import { DataTable, type Column } from '../../components/DataTable';
import { Banner, EmptyState, ErrorPanel, SkeletonTable } from '../../components/Feedback';
import { Field } from '../../components/Field';
import { Card, KeyValue, Page, PageHeader } from '../../components/Layout';
import { LocalityChip } from '../../components/LocalityChip';
import { QueryView } from '../../components/QueryView';
import { StatusBadge } from '../../components/StatusBadge';
import { useFeedback } from '../../components/Toast';
import { formatBytes, formatDateTime, plural } from '../../lib/format';
import { DOCUMENT_STATUS } from '../../lib/status';
import { useTimeZone } from '../common/useTimeZone';

const ACCEPTED = ['.txt', '.md', '.csv', '.pdf', '.docx'];

/** Error copy names the kind of problem: type, size, password, missing text, extraction, embedding. */
export function documentErrorText(code: string, fallback: string): string {
  const c = code.toUpperCase();
  if (c.includes('SCANNED') || c.includes('NO_TEXT')) return 'Scanned document: it has no embedded text to index. OCR is not supported.';
  if (c.includes('ENCRYPT') || c.includes('PASSWORD')) return 'Password-protected document: remove the password and upload it again.';
  if (c.includes('TOO_LARGE') || c.includes('SIZE')) return `Too large: documents can be up to ${formatBytes(MAX_UPLOAD_BYTES)}.`;
  if (c.includes('TYPE') || c.includes('MIME') || c.includes('UNSUPPORTED')) return `Unsupported file type. Use ${ACCEPTED.join(', ')}.`;
  if (c.includes('EMBED')) return 'Embedding failed: the text was extracted but could not be indexed. Try re-indexing.';
  if (c.includes('DUPLICATE')) return 'Already uploaded: this exact file is already in the knowledge base.';
  if (c.includes('EXTRACT') || c.includes('MALFORMED') || c.includes('CORRUPT')) return 'Could not read the file (extraction failed). It may be damaged.';
  return fallback;
}

interface LocalUpload {
  id: string;
  name: string;
  size: number;
  status: 'uploading' | 'accepted' | 'rejected';
  message?: string;
}

function extensionOf(name: string): string {
  const i = name.lastIndexOf('.');
  return i === -1 ? '' : name.slice(i).toLowerCase();
}

export default function KnowledgeBasePage() {
  const { kbId = '' } = useParams();
  const kb = useKnowledgeBase(kbId);
  return (
    <Page>
      <QueryView query={kb} errorTitle="Could not load this knowledge base">
        {(data) => <KnowledgeBaseView kb={data} />}
      </QueryView>
    </Page>
  );
}

function KnowledgeBaseView({ kb }: { kb: KnowledgeBaseOut }) {
  const docs = useDocuments(kb.id);
  const client = useQueryClient();
  const navigate = useNavigate();
  const guard = useActionGuard();
  const timeZone = useTimeZone();
  const { toast, announce } = useFeedback();
  const [params] = useSearchParams();
  const highlighted = params.get('document');
  const [uploads, setUploads] = useState<LocalUpload[]>([]);
  const [dragging, setDragging] = useState(false);
  const [toDelete, setToDelete] = useState<DocumentOut | null>(null);
  const [deleteKb, setDeleteKb] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();

  const refresh = () => void client.invalidateQueries({ queryKey: keys.documents(kb.id) });

  const uploadFiles = async (files: File[]) => {
    const list = files.map((f) => ({ file: f, id: idempotencyKey() }));
    setUploads((u) => [
      ...list.map(({ file, id }): LocalUpload => {
        const ext = extensionOf(file.name);
        const rejected = !ACCEPTED.includes(ext)
          ? `Unsupported file type ${ext || '(none)'}. Use ${ACCEPTED.join(', ')}.`
          : file.size > MAX_UPLOAD_BYTES
            ? `Too large (${formatBytes(file.size)}): the limit is ${formatBytes(MAX_UPLOAD_BYTES)}.`
            : null;
        return { id, name: file.name, size: file.size, status: rejected ? 'rejected' : 'uploading', message: rejected ?? undefined };
      }),
      ...u,
    ]);
    // Each file is independent: a rejected file never stops the others.
    for (const { file, id } of list) {
      const ext = extensionOf(file.name);
      if (!ACCEPTED.includes(ext) || file.size > MAX_UPLOAD_BYTES) continue;
      try {
        await pendingApi.documentUpload(kb.id, file, id);
        setUploads((u) => u.map((x) => (x.id === id ? { ...x, status: 'accepted', message: 'Uploaded; indexing continues below.' } : x)));
      } catch (e) {
        const message = isApiError(e) ? documentErrorText(e.code, remediation(e)) : 'Upload failed.';
        setUploads((u) => u.map((x) => (x.id === id ? { ...x, status: 'rejected', message } : x)));
      }
      refresh();
    }
    announce(`${plural(list.length, 'file')} processed. Check each file's status below.`);
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    void uploadFiles(Array.from(e.dataTransfer.files));
  };

  const reindex = useMutation({
    mutationFn: (doc: DocumentOut) => pendingApi.documentReindex(kb.id, doc.id),
    onSuccess: (_d, doc) => toast(`Re-indexing ${doc.name}.`),
    onSettled: refresh,
  });
  const remove = useMutation({
    mutationFn: (doc: DocumentOut) => pendingApi.documentDelete(kb.id, doc.id),
    onSuccess: (_d, doc) => {
      setToDelete(null);
      toast(`${doc.name} deleted.`);
    },
    onSettled: refresh,
  });
  const removeKb = useMutation({
    mutationFn: () => pendingApi.knowledgeBaseDelete(kb.id),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.knowledgeBases });
      void navigate('/knowledge');
    },
  });

  const documents = docs.data ?? [];
  const chunkTotal = documents.reduce((sum, d) => sum + (documentChunks(d) ?? 0), 0);
  const columns: Column<DocumentOut>[] = [
    {
      key: 'name',
      header: 'Document',
      primary: true,
      cell: (d) => (
        <span className="row" style={{ flexWrap: 'nowrap', fontWeight: d.id === highlighted ? 700 : undefined }}>
          <FileText size={16} aria-hidden="true" />
          <span className="break-anywhere">{d.name}</span>
        </span>
      ),
      sortValue: (d) => d.name.toLowerCase(),
    },
    { key: 'type', header: 'Type', cell: (d) => extensionOf(d.name).replace('.', '').toUpperCase() || d.mime },
    { key: 'size', header: 'Size', cell: (d) => formatBytes(d.bytes), sortValue: (d) => d.bytes },
    {
      key: 'state',
      header: 'Status',
      cell: (d) => {
        const err = documentError(d.error);
        return (
          <span className="stack-sm" style={{ gap: 2 }}>
            <StatusBadge status={DOCUMENT_STATUS[d.state]} />
            {d.state === 'FAILED' && err ? <span className="field-error">{documentErrorText(err.code, err.message)}</span> : null}
          </span>
        );
      },
      sortValue: (d) => d.state,
    },
    { key: 'chunks', header: 'Chunks', cell: (d) => (documentChunks(d) ?? '—').toString(), sortValue: (d) => documentChunks(d) },
    { key: 'added', header: 'Added', cell: (d) => formatDateTime(d.createdAt, timeZone), sortValue: (d) => d.createdAt },
    {
      key: 'actions',
      header: 'Actions',
      cell: (d) => (
        <details className="action-menu">
          <summary className="btn btn-tertiary btn-icon" aria-label={`Actions for ${d.name}`}>
            <MoreHorizontal size={18} aria-hidden="true" />
          </summary>
          <div className="popover" style={{ width: 220 }}>
            <button type="button" className="menu-item" disabled={!!guard.offline} onClick={() => reindex.mutate(d)}>
              <RotateCw size={16} aria-hidden="true" /> Re-index
            </button>
            <button type="button" className="menu-item" disabled={!!guard.offline} onClick={() => setToDelete(d)}>
              <Trash2 size={16} aria-hidden="true" /> Delete document
            </button>
          </div>
        </details>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title={kb.name}
        purpose="Documents are extracted and indexed on this device."
        breadcrumbs={[{ label: 'Knowledge', to: '/knowledge' }, { label: kb.name }]}
        status={
          <>
            <LocalityChip provider="local" long />
            <span className="muted">
              {plural(documents.length, 'document')} · {plural(chunkTotal, 'chunk')} · embedding {kb.embeddingProfile}
            </span>
          </>
        }
        actions={
          <Button variant="primary" icon={<Upload size={16} aria-hidden="true" />} onClick={() => inputRef.current?.click()} disabledReason={guard.offline}>
            Upload documents
          </Button>
        }
      />
      <div
        className="dropzone"
        data-active={dragging ? 'true' : undefined}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <Upload size={24} aria-hidden="true" />
        <p>
          Drag files here, or{' '}
          <label htmlFor={inputId} className="btn btn-tertiary" style={{ display: 'inline-flex' }}>
            choose files
          </label>
        </p>
        <p className="muted">
          {ACCEPTED.join(', ')} · up to {formatBytes(MAX_UPLOAD_BYTES)} each · scanned PDFs without text are not supported
        </p>
        <input
          ref={inputRef}
          id={inputId}
          type="file"
          multiple
          accept={ACCEPTED.join(',')}
          className="sr-only"
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            e.target.value = '';
            if (files.length > 0) void uploadFiles(files);
          }}
        />
      </div>
      {uploads.length > 0 ? (
        <Card title="Uploads" actions={<Button variant="tertiary" onClick={() => setUploads([])}>Clear list</Button>}>
          <ul className="stack-sm" style={{ listStyle: 'none' }} aria-live="polite">
            {uploads.map((u) => (
              <li key={u.id} className="row-between">
                <span className="break-anywhere">
                  {u.name} <span className="muted">({formatBytes(u.size)})</span>
                </span>
                <span className="stack-sm" style={{ gap: 2, alignItems: 'flex-end' }}>
                  <StatusBadge
                    status={
                      u.status === 'uploading'
                        ? { label: 'Uploading', tone: 'info', icon: 'spinner' }
                        : u.status === 'accepted'
                          ? { label: 'Uploaded', tone: 'success', icon: 'check' }
                          : { label: 'Not uploaded', tone: 'danger', icon: 'x' }
                    }
                  />
                  {u.message ? <span className={u.status === 'rejected' ? 'field-error' : 'muted'}>{u.message}</span> : null}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
      <QueryView
        query={docs}
        errorTitle="Could not load documents"
        loading={<SkeletonTable label="Loading documents" />}
        isEmpty={(d) => d.length === 0}
        empty={<EmptyState icon={FileText} title="No documents yet">Upload documents to make them searchable in chat and agents.</EmptyState>}
      >
        {(list) => <DataTable caption={`Documents in ${kb.name}`} columns={columns} rows={list} rowKey={(d) => d.id} initialSort={{ key: 'added', direction: 'desc' }} />}
      </QueryView>
      {reindex.isError ? <ErrorPanel error={reindex.error} title="Could not re-index" /> : null}
      <TestRetrieval kb={kb} hasReady={documents.some((d) => d.state === 'READY')} />
      <Card title="Settings">
        <KeyValue
          items={[
            ['Name', kb.name],
            ['Embedding profile', <span key="p" className="mono">{kb.embeddingProfile} ({kb.embeddingDimension} dimensions)</span>],
          ]}
        />
        <p className="muted" style={{ marginTop: 8 }}>
          The embedding profile is fixed when the knowledge base is created; re-index documents to rebuild their chunks.
        </p>
        <div className="row" style={{ marginTop: 12 }}>
          <Button onClick={() => setDeleteKb(true)} disabledReason={guard.offline}>
            Delete knowledge base
          </Button>
        </div>
      </Card>
      <ConfirmDialog
        open={toDelete !== null}
        title={`Delete ${toDelete?.name ?? 'document'}?`}
        consequence="Its chunks stop appearing in chat and agent searches immediately. The file is securely removed from this device."
        confirmLabel="Delete document"
        destructive
        busy={remove.isPending}
        onConfirm={() => toDelete && remove.mutate(toDelete)}
        onCancel={() => setToDelete(null)}
      >
        {remove.isError ? <ErrorPanel error={remove.error} title="Could not delete" /> : null}
      </ConfirmDialog>
      <ConfirmDialog
        open={deleteKb}
        title={`Delete ${kb.name}?`}
        consequence={`All ${plural(documents.length, 'document')} and their chunks are removed. Chats and agents using this knowledge base can no longer cite it.`}
        confirmLabel="Delete knowledge base"
        destructive
        busy={removeKb.isPending}
        onConfirm={() => removeKb.mutate()}
        onCancel={() => setDeleteKb(false)}
      >
        {removeKb.isError ? <ErrorPanel error={removeKb.error} title="Could not delete" /> : null}
      </ConfirmDialog>
    </>
  );
}

function TestRetrieval({ kb, hasReady }: { kb: KnowledgeBaseOut; hasReady: boolean }) {
  const [query, setQuery] = useState('');
  const [asked, setAsked] = useState('');
  const search = useMutation({
    mutationFn: (q: string) => pendingApi.knowledgeQuery(kb.id, { query: q, topK: 5, maxContextTokens: 3000 }),
  });
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    setAsked(query.trim());
    search.mutate(query.trim());
  };
  return (
    <Card title="Test retrieval" subtitle="See which passages chat and agents would get for a question.">
      <form className="row" style={{ alignItems: 'flex-end' }} onSubmit={onSubmit}>
        <div style={{ flex: '1 1 320px' }}>
          <Field label="Question">
            <input className="input" type="search" value={query} onChange={(e) => setQuery(e.target.value)} />
          </Field>
        </div>
        <Button type="submit" icon={<Search size={16} aria-hidden="true" />} busy={search.isPending} busyLabel="Searching…" disabledReason={hasReady ? null : 'Index at least one document first.'}>
          Search passages
        </Button>
      </form>
      {search.isError ? <ErrorPanel error={search.error} title="Search failed" /> : null}
      {search.data ? (
        <div className="stack-sm" style={{ marginTop: 16 }} aria-live="polite">
          <p className="muted">
            Top passages for “{asked}”: {plural(search.data.passages.length, 'result')}
          </p>
          {search.data.passages.length === 0 ? (
            <Banner tone="info">No passage matched. Chat would say it could not find this in the knowledge base.</Banner>
          ) : (
            <ol className="stack-sm" style={{ listStyle: 'none' }}>
              {search.data.passages.map((p: PassageOut, i) => (
                <li key={p.citationId} className="card card-compact stack-sm">
                  <div className="row-between">
                    <span className="field-label">
                      [{i + 1}] {p.document.name}
                    </span>
                    <span className="muted">
                      {p.location} · score {p.score.toFixed(2)}
                    </span>
                  </div>
                  <blockquote className="passage" style={{ margin: 0 }}>
                    {p.text}
                  </blockquote>
                </li>
              ))}
            </ol>
          )}
        </div>
      ) : null}
    </Card>
  );
}
