import { useQuery, useQueryClient } from '@tanstack/react-query';
import { MessagesSquare, Power, Send, Square } from 'lucide-react';
import { Fragment, useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { sendChatMessage } from '../../api/chat';
import { isApiError, remediation } from '../../api/errors';
import { useActionGuard } from '../../api/guards';
import { useCreateChat, useIntentKey, useToggleChat } from '../../api/mutations';
import { endpoints as pendingApi } from '../../api/endpoints';
import { toCitation, type StoredCitation } from '../../lib/knowledge';
import {
  isModelBusy,
  keys,
  useChatSession,
  useChatSessions,
  useKnowledgeBases,
  useModel,
  useModels,
  useSettings,
} from '../../api/queries';
import { useModelEvents } from '../../api/streams';
import type { ChatMessageOut, ChatSessionDetailOut } from '../../api/schema';
import { Button } from '../../components/Button';
import { ConfirmDialog } from '../../components/Dialog';
import { Banner, EmptyState, ErrorPanel, SkeletonBlock } from '../../components/Feedback';
import { Field } from '../../components/Field';
import { Card, Page, PageHeader } from '../../components/Layout';
import { LocalityChip } from '../../components/LocalityChip';
import { SourceDrawer } from '../../components/SourceDrawer';
import { StatusBadge } from '../../components/StatusBadge';
import { useFeedback } from '../../components/Toast';
import { useLayout } from '../../lib/breakpoints';
import { formatDuration, formatRelative } from '../../lib/format';
import { MODEL_DOWNLOAD_STATUS, MODEL_MEMORY_STATUS } from '../../lib/status';
import { ModelProgress } from '../models/ModelProgress';

export default function ChatPage() {
  const { sessionId } = useParams();
  return sessionId ? <ChatSessionPage sessionId={sessionId} /> : <ChatStart />;
}

function ConversationList({ current }: { current?: string }) {
  const sessions = useChatSessions();
  return (
    <nav className="chat-list" aria-label="Conversations">
      <Link to="/chat" aria-current={current ? undefined : 'page'}>
        New chat
      </Link>
      {(sessions.data ?? []).map((s) => (
        <Link key={s.id} to={`/chat/${encodeURIComponent(s.id)}`} aria-current={s.id === current ? 'page' : undefined}>
          <span className="break-anywhere">{s.title}</span>
          <span className="muted" style={{ display: 'block', fontSize: 12 }}>
            {s.enabled ? 'Enabled' : 'Disabled'} · {formatRelative(s.lastMessageAt ?? s.createdAt)}
          </span>
        </Link>
      ))}
    </nav>
  );
}

/** The default Chat page is an intentional inactive state, not an empty conversation. */
function ChatStart() {
  const models = useModels();
  const kbs = useKnowledgeBases();
  const create = useCreateChat();
  const toggle = useToggleChat();
  const [createKey] = useIntentKey();
  const [enableKey] = useIntentKey();
  const navigate = useNavigate();
  const guard = useActionGuard();
  // Only chat models: embedding and speech-to-text models cannot hold a conversation.
  const local = (models.data ?? []).filter((m) => m.id.startsWith('local.') && (m.capabilities ?? []).includes('chat'));
  const [model, setModel] = useState('');
  const [kb, setKb] = useState('');
  const [mode, setMode] = useState<'when_relevant' | 'only_knowledge'>('when_relevant');
  const [title, setTitle] = useState('');
  const chosen = local.find((m) => m.id === (model || local.find((x) => x.downloadState === 'INSTALLED')?.id));
  const busy = create.isPending || toggle.isPending;

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!chosen) return;
    const session = await create.mutateAsync({
      key: createKey,
      body: { modelProfile: chosen.id, knowledgeBaseId: kb || null, retrievalMode: mode, title: title.trim() || null },
    });
    try {
      await toggle.mutateAsync({ id: session.id, enable: true, key: enableKey });
    } finally {
      void navigate(`/chat/${encodeURIComponent(session.id)}`);
    }
  };

  return (
    <Page>
      <PageHeader title="Chat" purpose="Opt-in chat with a local model, optionally grounded in your knowledge." />
      <div className="chat-layout">
        <ConversationList />
        <div className="chat-main" style={{ padding: 24 }}>
          <div className="stack form-width">
            <Banner tone="info" role="none" title="Chat is off until you enable it">
              Enabling chat loads the chosen model into memory and keeps it there while chat is enabled. That can use a lot
              of unified memory. Everything stays on this device.
            </Banner>
            <form className="form" onSubmit={(e) => void onSubmit(e)}>
              <Field label="Local model" required help={chosen ? `${MODEL_DOWNLOAD_STATUS[chosen.downloadState].label} · ${MODEL_MEMORY_STATUS[chosen.memoryState].label}` : undefined}>
                <select className="select" value={chosen?.id ?? ''} onChange={(e) => setModel(e.target.value)}>
                  {local.length === 0 ? <option value="">No local model installed</option> : null}
                  {local.map((m) => (
                    <option key={m.id} value={m.id} disabled={m.downloadState !== 'INSTALLED'}>
                      {m.displayName} ({MODEL_DOWNLOAD_STATUS[m.downloadState].label})
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Knowledge base" help="Optional. Answers cite passages from it.">
                <select className="select" value={kb} onChange={(e) => setKb(e.target.value)}>
                  <option value="">None</option>
                  {(kbs.data ?? []).map((k) => (
                    <option key={k.id} value={k.id}>
                      {k.name}
                    </option>
                  ))}
                </select>
              </Field>
              {kb ? (
                <fieldset className="fieldset">
                  <legend>How to use the knowledge base</legend>
                  <label className="check-row">
                    <input type="radio" name="mode" checked={mode === 'when_relevant'} onChange={() => setMode('when_relevant')} />
                    <span>Use knowledge when relevant</span>
                  </label>
                  <label className="check-row">
                    <input type="radio" name="mode" checked={mode === 'only_knowledge'} onChange={() => setMode('only_knowledge')} />
                    <span>Answer only from knowledge</span>
                  </label>
                </fieldset>
              ) : null}
              <Field label="Title" help="Optional.">
                <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} />
              </Field>
              <div className="row">
                <LocalityChip provider="local" long />
              </div>
              {create.isError ? <ErrorPanel error={create.error} title="Could not create the chat" /> : null}
              {toggle.isError ? <ErrorPanel error={toggle.error} title="Chat was created but could not be enabled" /> : null}
              <div className="row">
                <Button
                  type="submit"
                  variant="primary"
                  icon={<Power size={16} aria-hidden="true" />}
                  busy={busy}
                  busyLabel="Enabling…"
                  disabledReason={guard.runtime ?? (chosen && chosen.downloadState === 'INSTALLED' ? null : 'Install a local model first (Models page).')}
                >
                  Enable local chat
                </Button>
              </div>
            </form>
          </div>
        </div>
      </div>
    </Page>
  );
}

interface Streaming {
  user: ChatMessageOut;
  assistantId: string;
  text: string;
  citations: StoredCitation[];
  error?: string;
}

/** Text with [n] markers turned into citation chips; everything else stays plain text. */
function CitedText({ text, citations, onOpen }: { text: string; citations: StoredCitation[]; onOpen: (c: StoredCitation) => void }) {
  const parts: ReactNode[] = [];
  const regex = /\[(\d{1,2})\]/g;
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = regex.exec(text)) !== null) {
    const n = Number(match[1]);
    const citation = citations.find((c) => c.index === n);
    if (!citation) continue;
    parts.push(text.slice(last, match.index));
    parts.push(
      <button
        key={`c-${match.index}`}
        type="button"
        className="citation-chip"
        aria-label={`Source ${n}: ${citation.document.name}${citation.location ? `, ${citation.location}` : ''}`}
        onClick={() => onOpen(citation)}
      >
        {n}
      </button>,
    );
    last = match.index + match[0].length;
  }
  parts.push(text.slice(last));
  return (
    <span className="message-text">
      {parts.map((p, i) => (
        <Fragment key={i}>{p}</Fragment>
      ))}
    </span>
  );
}

/** Every cited source stays reachable even when the answer text has no [n] markers. */
function SourceList({ citations, onOpen }: { citations: StoredCitation[]; onOpen: (c: StoredCitation) => void }) {
  return (
    <span className="row muted" style={{ fontSize: 12, marginTop: 4 }}>
      Sources:
      {citations.map((c) => (
        <button
          key={c.citationId}
          type="button"
          className="citation-chip"
          style={{ width: 'auto' }}
          aria-label={`Source ${c.index}: ${c.document.name}${c.location ? `, ${c.location}` : ''}`}
          onClick={() => onOpen(c)}
        >
          {c.index} · {c.document.name}
        </button>
      ))}
    </span>
  );
}

function ChatSessionPage({ sessionId }: { sessionId: string }) {
  const session = useChatSession(sessionId);
  if (session.isPending) {
    return (
      <Page>
        <SkeletonBlock label="Loading chat" />
      </Page>
    );
  }
  if (session.isError) {
    return (
      <Page>
        <ErrorPanel error={session.error} title="Could not load this chat" onRetry={() => void session.refetch()} />
      </Page>
    );
  }
  return <ChatView session={session.data} />;
}

function ChatView({ session }: { session: ChatSessionDetailOut }) {
  const client = useQueryClient();
  const model = useModel(session.modelProfile);
  const settings = useSettings();
  const kbs = useKnowledgeBases();
  const toggle = useToggleChat();
  const [toggleKey, resetToggleKey] = useIntentKey();
  const guard = useActionGuard();
  const layout = useLayout();
  const { announce } = useFeedback();
  const [draft, setDraft] = useState('');
  const [streaming, setStreaming] = useState<Streaming | null>(null);
  const [sendError, setSendError] = useState<unknown>(null);
  const [confirmDisable, setConfirmDisable] = useState(false);
  const [disabledNotice, setDisabledNotice] = useState(false);
  const [source, setSource] = useState<{ citation: StoredCitation; messageId: string } | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const streamRef = useRef<HTMLDivElement>(null);
  useModelEvents(session.modelProfile, model.data ? isModelBusy(model.data) : false);

  const kbName = kbs.data?.find((k) => k.id === session.knowledgeBaseId)?.name;
  const modelReady = model.data?.memoryState === 'READY';
  const idleMinutes = Math.round((settings.data?.idleUnloadSeconds ?? 600) / 60);

  const resolved = useQuery({
    queryKey: ['citation', session.id, source?.messageId, source?.citation.citationId],
    queryFn: () => pendingApi.citation(session.id, source?.messageId ?? '', source?.citation.citationId ?? ''),
    enabled: source !== null && source.messageId !== '',
    retry: false,
  });

  useEffect(() => {
    const el = streamRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [session.messages.length, streaming?.text]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const send = async (e: FormEvent) => {
    e.preventDefault();
    const content = draft.trim();
    if (!content || streaming) return;
    setSendError(null);
    const controller = new AbortController();
    abortRef.current = controller;
    setDraft('');
    try {
      await sendChatMessage(
        session.id,
        content,
        {
          onMessage: (user, assistantId, citations) =>
            setStreaming({
              user,
              assistantId,
              text: '',
              citations: citations.map((c, i) => toCitation(c, i)).filter((c): c is StoredCitation => c !== null),
            }),
          onDelta: (text) => setStreaming((s) => (s ? { ...s, text: s.text + text } : s)),
          onError: (err) => setStreaming((s) => (s ? { ...s, error: err.message } : s)),
          onDone: () => announce('Answer complete.'),
        },
        controller.signal,
      );
    } catch (error) {
      if (!(error instanceof DOMException && error.name === 'AbortError')) {
        setSendError(error);
        setDraft(content);
      }
    } finally {
      abortRef.current = null;
      await client.invalidateQueries({ queryKey: keys.chatSession(session.id) });
      setStreaming(null);
    }
  };

  const stop = () => abortRef.current?.abort();

  const setEnabled = (enable: boolean) => {
    if (!enable && streaming) stop();
    toggle.mutate(
      { id: session.id, enable, key: toggleKey },
      {
        onSuccess: () => {
          resetToggleKey();
          setConfirmDisable(false);
          setDisabledNotice(!enable);
          announce(enable ? 'Chat enabled.' : 'Chat disabled.');
        },
      },
    );
  };

  const openSource = useCallback((citation: StoredCitation, messageId: string) => setSource({ citation, messageId }), []);

  const messages = session.messages.filter((m) => !(streaming && m.id === streaming.assistantId));
  const drawerCitation = source ? { ...source.citation, ...(resolved.data ?? {}) } : null;
  const canSend = session.enabled && modelReady && !streaming;
  const sendReason = !session.enabled
    ? 'Enable chat to send messages.'
    : !modelReady
      ? 'Waiting for the model to be ready.'
      : guard.offline;

  const drawerOpenInline = source !== null && layout === 'desktop';

  return (
    <Page>
      <PageHeader
        title={session.title}
        documentTitle={`Chat: ${session.title}`}
        breadcrumbs={[{ label: 'Chat', to: '/chat' }, { label: session.title }]}
        status={
          <>
            <LocalityChip provider="local" long />
            <StatusBadge status={session.enabled ? { label: 'Enabled', tone: 'success', icon: 'check' } : { label: 'Disabled', tone: 'neutral', icon: 'ban' }} context="Chat" />
            {session.holdsModelLease ? (
              <span className="badge tone-info" title="This chat keeps the model loaded">
                Holding model {model.data?.displayName ?? session.modelProfile}
              </span>
            ) : null}
          </>
        }
        actions={
          session.enabled ? (
            <Button icon={<Power size={16} aria-hidden="true" />} onClick={() => (streaming ? setConfirmDisable(true) : setEnabled(false))} busy={toggle.isPending} busyLabel="Disabling…">
              Disable chat
            </Button>
          ) : (
            <Button variant="primary" icon={<Power size={16} aria-hidden="true" />} onClick={() => setEnabled(true)} busy={toggle.isPending} busyLabel="Enabling…" disabledReason={guard.runtime}>
              Enable local chat
            </Button>
          )
        }
      />
      {toggle.isError ? <ErrorPanel error={toggle.error} title="Could not change chat" /> : null}
      {disabledNotice && !session.enabled ? (
        <Banner tone="info" title="Chat disabled">
          The model lease is released. Model will unload in {formatDuration(idleMinutes * 60)} unless another run is using it.
          Conversation history is kept.
        </Banner>
      ) : null}
      {session.enabled && model.data && !modelReady ? (
        <Card title="Getting the model ready">
          <ModelProgress model={model.data} />
          {model.data.memoryState === 'NOT_LOADED' ? <p className="muted">Waiting for the model to start loading…</p> : null}
          <p className="muted" style={{ marginTop: 8 }}>
            You can leave this page; loading continues and shows in the top bar.
          </p>
        </Card>
      ) : null}
      <div className="chat-layout" data-drawer={drawerOpenInline ? 'true' : undefined}>
        <ConversationList current={session.id} />
        <div className="chat-main">
          <div className="chat-header">
            <span className="row">
              <span className="badge chip-local">{model.data?.displayName ?? session.modelProfile}</span>
              {session.knowledgeBaseId ? (
                <span className="badge tone-neutral">
                  Knowledge: {kbName ?? 'selected'} · {session.retrievalMode === 'only_knowledge' ? 'answers only from knowledge' : 'when relevant'}
                </span>
              ) : null}
            </span>
          </div>
          <div className="chat-stream" ref={streamRef} role="log" aria-label="Conversation" aria-live="polite" tabIndex={0}>
            {messages.length === 0 && !streaming ? (
              <EmptyState icon={MessagesSquare} title="No messages yet">
                {session.enabled ? 'Ask a question below.' : 'Enable chat to start the conversation.'}
              </EmptyState>
            ) : null}
            {messages.map((m) => {
              const citations = m.citations.map((c, i) => toCitation(c, i)).filter((c): c is StoredCitation => c !== null);
              return (
                <div key={m.id} className={`message ${m.role === 'user' ? 'message-user' : 'message-assistant'}`}>
                  <span className="sr-only">{m.role === 'user' ? 'You said:' : 'Assistant:'}</span>
                  {m.role === 'assistant' ? (
                    <CitedText text={m.content} citations={citations} onOpen={(c) => openSource(c, m.id)} />
                  ) : (
                    <span className="message-text">{m.content}</span>
                  )}
                  {m.status === 'stopped' ? <span className="muted"> (stopped)</span> : null}
                  {m.status === 'failed' ? <span className="field-error"> The answer failed.</span> : null}
                  {m.role === 'assistant' && citations.length > 0 ? (
                    <SourceList citations={citations} onOpen={(c) => openSource(c, m.id)} />
                  ) : null}
                </div>
              );
            })}
            {streaming ? (
              <>
                {!messages.some((m) => m.id === streaming.user.id) ? (
                  <div className="message message-user">
                    <span className="message-text">{streaming.user.content}</span>
                  </div>
                ) : null}
                <div className="message message-assistant" aria-busy="true">
                  <span className="sr-only">Assistant is answering:</span>
                  <CitedText text={streaming.text} citations={streaming.citations} onOpen={(c) => openSource(c, streaming.assistantId)} />
                  <span className="cursor" aria-hidden="true" />
                  {streaming.error ? <p className="field-error">{streaming.error}</p> : null}
                </div>
              </>
            ) : null}
          </div>
          <form className="composer" onSubmit={(e) => void send(e)}>
            {sendError ? (
              <Banner tone="danger" role="alert">
                {isApiError(sendError) ? remediation(sendError) : 'The message could not be sent.'}
              </Banner>
            ) : null}
            <label htmlFor="chat-input" className="sr-only">
              Message
            </label>
            <textarea
              id="chat-input"
              className="textarea"
              rows={2}
              value={draft}
              placeholder={session.enabled ? 'Ask something…' : 'Chat is disabled'}
              disabled={!session.enabled}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && canSend) {
                  e.preventDefault();
                  e.currentTarget.form?.requestSubmit();
                }
              }}
            />
            <div className="row-between">
              <span className="muted" style={{ fontSize: 12 }}>
                Local on this device · Enter to send, Shift+Enter for a new line
              </span>
              {streaming ? (
                <Button icon={<Square size={14} aria-hidden="true" />} onClick={stop}>
                  Stop
                </Button>
              ) : (
                <Button type="submit" variant="primary" icon={<Send size={16} aria-hidden="true" />} disabledReason={canSend ? (draft.trim() ? null : null) : sendReason}>
                  Send
                </Button>
              )}
            </div>
          </form>
        </div>
        {source ? (
          <SourceDrawer
            citation={drawerCitation}
            loading={resolved.isPending && !source.citation.text}
            error={resolved.isError && !source.citation.text ? resolved.error : undefined}
            onClose={() => setSource(null)}
          />
        ) : null}
      </div>
      <ConfirmDialog
        open={confirmDisable}
        title="Disable chat while it is answering?"
        consequence="The current answer stops and is kept as a partial reply. The model lease is released; history is kept."
        confirmLabel="Stop and disable chat"
        cancelLabel="Keep answering"
        busy={toggle.isPending}
        onConfirm={() => setEnabled(false)}
        onCancel={() => setConfirmDisable(false)}
      />
    </Page>
  );
}
