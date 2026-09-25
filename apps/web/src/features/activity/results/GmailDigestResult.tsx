import { AlertOctagon, AlertTriangle, ExternalLink, Inbox } from 'lucide-react';
import type { ReactNode } from 'react';
import { Banner } from '../../../components/Feedback';
import { LocalityChip } from '../../../components/LocalityChip';
import { formatDateTime, formatUtc, plural } from '../../../lib/format';

export interface DigestItem {
  messageId: string;
  threadId: string;
  from: string;
  subject: string;
  receivedAt: string | null;
  reason: string;
  nextAction: string;
  needsReview: boolean;
  gmailLink: string;
}

export interface DigestResult {
  date: string;
  timezone: string;
  processedCount: number;
  truncated: boolean;
  counts: { urgent: number; important: number; lowPriority: number; needsReview: number };
  groups: { urgent: DigestItem[]; important: DigestItem[]; lowPriority: DigestItem[] };
  model: { profile: string; provider?: string | null; locality?: string | null };
}

function str(v: unknown): string {
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  return '';
}

function items(v: unknown): DigestItem[] {
  if (!Array.isArray(v)) return [];
  return v.flatMap((raw) => {
    if (typeof raw !== 'object' || raw === null) return [];
    const r = raw as Record<string, unknown>;
    return [
      {
        messageId: str(r.messageId),
        threadId: str(r.threadId),
        from: str(r.from),
        subject: str(r.subject),
        receivedAt: typeof r.receivedAt === 'string' ? r.receivedAt : null,
        reason: str(r.reason),
        nextAction: str(r.nextAction),
        needsReview: r.needsReview === true,
        gmailLink: str(r.gmailLink),
      },
    ];
  });
}

/** Narrow an untyped run result into the crewquarters.gmail-digest/v1 shape. */
export function parseDigest(result: Record<string, unknown> | null): DigestResult | null {
  if (!result || typeof result.groups !== 'object' || result.groups === null) return null;
  const groups = result.groups as Record<string, unknown>;
  const counts = (result.counts ?? {}) as Record<string, unknown>;
  const model = (result.model ?? {}) as Record<string, unknown>;
  const g = { urgent: items(groups.urgent), important: items(groups.important), lowPriority: items(groups.lowPriority) };
  const num = (v: unknown, fallback: number) => (typeof v === 'number' ? v : fallback);
  return {
    date: str(result.date),
    timezone: str(result.timezone) || 'UTC',
    processedCount: num(result.processedCount, g.urgent.length + g.important.length + g.lowPriority.length),
    truncated: result.truncated === true,
    counts: {
      urgent: num(counts.urgent, g.urgent.length),
      important: num(counts.important, g.important.length),
      lowPriority: num(counts.lowPriority, g.lowPriority.length),
      needsReview: num(counts.needsReview, 0),
    },
    groups: g,
    model: { profile: str(model.profile), provider: typeof model.provider === 'string' ? model.provider : null, locality: typeof model.locality === 'string' ? model.locality : null },
  };
}

/** Only link out to Gmail itself over https. */
export function safeGmailLink(link: string): string | null {
  try {
    const url = new URL(link);
    return url.protocol === 'https:' && url.hostname === 'mail.google.com' ? url.toString() : null;
  } catch {
    return null;
  }
}

function Item({ item, timeZone }: { item: DigestItem; timeZone: string }) {
  const link = safeGmailLink(item.gmailLink);
  return (
    <li className="digest-item">
      <div className="row-between" style={{ alignItems: 'flex-start' }}>
        <span className="field-label break-anywhere">{item.subject || '(no subject)'}</span>
        {item.needsReview ? (
          <span className="badge tone-warning">
            <AlertTriangle size={14} aria-hidden="true" />
            Needs review
          </span>
        ) : null}
      </div>
      <span className="muted break-anywhere">
        From {item.from || 'unknown sender'}
        {item.receivedAt ? (
          <>
            {' · '}
            <time dateTime={item.receivedAt} title={formatUtc(item.receivedAt)}>
              {formatDateTime(item.receivedAt, timeZone)}
            </time>
          </>
        ) : null}
      </span>
      <span className="break-anywhere">
        <span className="field-label">Why: </span>
        {item.reason}
      </span>
      <span className="break-anywhere">
        <span className="field-label">Next: </span>
        {item.nextAction}
      </span>
      <span className="row">
        {link ? (
          <a href={link} target="_blank" rel="noopener noreferrer" className="row" style={{ gap: 4 }}>
            Open in Gmail
            <ExternalLink size={14} aria-hidden="true" />
            <span className="sr-only">(opens a new tab): {item.subject}</span>
          </a>
        ) : null}
        <span className="mono muted" style={{ fontSize: 12 }} title="Gmail message ID">
          ID {item.messageId}
        </span>
      </span>
    </li>
  );
}

function Group({
  title,
  icon,
  tone,
  list,
  count,
  timeZone,
  collapsed,
}: {
  title: string;
  icon: ReactNode;
  tone: string;
  list: DigestItem[];
  count: number;
  timeZone: string;
  collapsed?: boolean;
}) {
  const header = (
    <>
      <span className={`badge ${tone}`}>
        {icon}
        {title}
      </span>
      <span>{plural(count, 'message')}</span>
    </>
  );
  const body =
    list.length === 0 ? (
      <p className="digest-item muted">Nothing in this group.</p>
    ) : (
      <ul style={{ listStyle: 'none' }}>
        {list.map((item) => (
          <Item key={item.messageId || `${item.subject}-${item.receivedAt}`} item={item} timeZone={timeZone} />
        ))}
      </ul>
    );
  if (collapsed) {
    return (
      <details className="result-section">
        <summary>{header}</summary>
        {body}
      </details>
    );
  }
  return (
    <section className="result-section" aria-label={`${title}: ${plural(count, 'message')}`}>
      <div className="result-section-header">{header}</div>
      {body}
    </section>
  );
}

/** Urgent, Important, Low priority (collapsed when long); truncation stays visible. */
export function GmailDigestResult({ digest }: { digest: DigestResult }) {
  const tz = digest.timezone;
  return (
    <div className="stack">
      <div className="row-between">
        <span>
          Mail from <strong>{digest.date}</strong> ({tz}) · {plural(digest.processedCount, 'message')} processed
        </span>
        <LocalityChip provider={digest.model.provider ?? (digest.model.locality === 'cloud' ? 'cloud' : 'local')} long />
      </div>
      {digest.truncated ? (
        <Banner tone="warning" title="This digest may be incomplete">
          The message limit was reached after {plural(digest.processedCount, 'message')}. Raise “Max messages” in the agent’s
          configuration to include more.
        </Banner>
      ) : null}
      {digest.counts.needsReview > 0 ? (
        <p className="muted">
          {plural(digest.counts.needsReview, 'message')} could not be classified confidently and {digest.counts.needsReview === 1 ? 'is' : 'are'} marked
          “Needs review”.
        </p>
      ) : null}
      <Group title="Urgent" icon={<AlertOctagon size={14} aria-hidden="true" />} tone="tone-danger" list={digest.groups.urgent} count={digest.counts.urgent} timeZone={tz} />
      <Group title="Important" icon={<AlertTriangle size={14} aria-hidden="true" />} tone="tone-warning" list={digest.groups.important} count={digest.counts.important} timeZone={tz} />
      <Group
        title="Low priority"
        icon={<Inbox size={14} aria-hidden="true" />}
        tone="tone-neutral"
        list={digest.groups.lowPriority}
        count={digest.counts.lowPriority}
        timeZone={tz}
        collapsed={digest.groups.lowPriority.length > 5}
      />
    </div>
  );
}
