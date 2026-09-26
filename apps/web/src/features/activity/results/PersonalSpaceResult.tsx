import { Compass, FileText, Layers, Sparkles } from 'lucide-react';
import type { ReactNode } from 'react';
import { Banner } from '../../../components/Feedback';
import { LocalityChip } from '../../../components/LocalityChip';
import { formatDateTime, formatUtc, plural } from '../../../lib/format';

export interface SpaceTheme {
  title: string;
  summary: string;
  citations: string[];
}

export interface SpaceHighlight {
  title: string;
  whyItMatters: string;
  nextStep: string | null;
  citations: string[];
}

export interface SpaceSource {
  citationId: string;
  documentName: string;
  locator: Record<string, unknown>;
  score: number;
}

export interface PersonalSpaceData {
  status: 'ready' | 'insufficient_context';
  domain: string | null;
  intent: string | null;
  summary: string;
  generatedAt: string;
  degraded: boolean;
  themes: SpaceTheme[];
  highlights: SpaceHighlight[];
  exploreNext: string[];
  suggestedAdditions: string[];
  sources: SpaceSource[];
  stats: { queriesRun: number; passagesConsidered: number; passagesUsed: number; documentsSeen: number };
  model: { profile: string; provider: string | null; locality: string | null };
}

function str(v: unknown): string {
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  return '';
}

function optStr(v: unknown): string | null {
  return typeof v === 'string' && v !== '' ? v : null;
}

function num(v: unknown): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : 0;
}

function strings(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];
}

function objects(v: unknown): Record<string, unknown>[] {
  if (!Array.isArray(v)) return [];
  return v.filter((x): x is Record<string, unknown> => typeof x === 'object' && x !== null && !Array.isArray(x));
}

/** Narrow an untyped run result into the crewquarters.personal-space/v1 shape. */
export function parsePersonalSpace(result: Record<string, unknown> | null): PersonalSpaceData | null {
  if (!result) return null;
  const status = result.status;
  if (status !== 'ready' && status !== 'insufficient_context') return null;
  if (typeof result.summary !== 'string') return null;
  const stats = (typeof result.stats === 'object' && result.stats !== null ? result.stats : {}) as Record<string, unknown>;
  const model = (typeof result.model === 'object' && result.model !== null ? result.model : {}) as Record<string, unknown>;
  return {
    status,
    domain: optStr(result.domain),
    intent: optStr(result.intent),
    summary: result.summary,
    generatedAt: str(result.generatedAt),
    degraded: result.degraded === true,
    themes: objects(result.themes).map((t) => ({ title: str(t.title), summary: str(t.summary), citations: strings(t.citations) })),
    highlights: objects(result.highlights).map((h) => ({
      title: str(h.title),
      whyItMatters: str(h.whyItMatters),
      nextStep: optStr(h.nextStep),
      citations: strings(h.citations),
    })),
    exploreNext: strings(result.exploreNext),
    suggestedAdditions: strings(result.suggestedAdditions),
    sources: objects(result.sources).map((s) => ({
      citationId: str(s.citationId),
      documentName: str(s.documentName),
      locator: typeof s.locator === 'object' && s.locator !== null ? (s.locator as Record<string, unknown>) : {},
      score: num(s.score),
    })),
    stats: {
      queriesRun: num(stats.queriesRun),
      passagesConsidered: num(stats.passagesConsidered),
      passagesUsed: num(stats.passagesUsed),
      documentsSeen: num(stats.documentsSeen),
    },
    model: { profile: str(model.profile), provider: optStr(model.provider), locality: optStr(model.locality) },
  };
}

/** Where in a document a passage came from, as plain text. */
export function describeLocator(locator: Record<string, unknown>): string {
  const parts: string[] = [];
  if (locator.page !== undefined && locator.page !== null) parts.push(`Page ${str(locator.page)}`);
  if (typeof locator.section === 'string' && locator.section) parts.push(locator.section);
  if (locator.row !== undefined && locator.row !== null) parts.push(`Row ${str(locator.row)}`);
  return parts.join(' · ');
}

function Citations({ ids, sources }: { ids: string[]; sources: SpaceSource[] }) {
  const numbered = ids.flatMap((id) => {
    const index = sources.findIndex((s) => s.citationId === id);
    const source = sources[index];
    return source ? [{ n: index + 1, source }] : [];
  });
  if (numbered.length === 0) return null;
  return (
    <span className="row" style={{ gap: 4, flexWrap: 'wrap' }}>
      <span className="sr-only">Sources:</span>
      {numbered.map(({ n, source }) => (
        <span key={source.citationId} className="badge tone-neutral" title={source.documentName}>
          Source {n}
        </span>
      ))}
    </span>
  );
}

function SectionTitle({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <div className="result-section-header">
      <span className="badge tone-info">
        {icon}
        {children}
      </span>
    </div>
  );
}

/** A themed, cited brief; a guidance card when the knowledge base is too thin; a warning when degraded. */
export function PersonalSpaceResult({ data, timeZone }: { data: PersonalSpaceData; timeZone: string }) {
  const stats = data.stats;
  const provider = data.model.provider ?? (data.model.locality === 'cloud' ? 'cloud' : 'local');

  if (data.status === 'insufficient_context') {
    return (
      <div className="stack">
        <Banner tone="warning" title="Not enough in this knowledge base to personalize yet">
          <span className="break-anywhere">{data.summary}</span>
        </Banner>
        {data.suggestedAdditions.length > 0 ? (
          <section className="result-section" aria-label="What to add">
            <SectionTitle icon={<FileText size={14} aria-hidden="true" />}>What to add</SectionTitle>
            <ul style={{ paddingLeft: 'var(--space-6)', paddingBottom: 'var(--space-3)' }}>
              {data.suggestedAdditions.map((s) => (
                <li key={s} className="break-anywhere">
                  {s}
                </li>
              ))}
            </ul>
          </section>
        ) : null}
        <p className="muted">
          Searched with {plural(stats.queriesRun, 'query', 'queries')}; {plural(stats.passagesUsed, 'relevant passage')} found.
        </p>
      </div>
    );
  }

  return (
    <div className="stack">
      <div className="row-between">
        <span className="row" style={{ flexWrap: 'wrap' }}>
          {data.domain ? <span className="badge tone-info">{data.domain}</span> : null}
          {data.generatedAt ? (
            <time className="muted" dateTime={data.generatedAt} title={formatUtc(data.generatedAt)}>
              {formatDateTime(data.generatedAt, timeZone)}
            </time>
          ) : null}
        </span>
        <LocalityChip provider={provider} long />
      </div>
      {data.intent ? (
        <p className="muted break-anywhere">
          Built around: <strong>{data.intent}</strong>
        </p>
      ) : null}
      {data.degraded ? (
        <Banner tone="warning" title="The model could not write a brief this time">
          Showing the most relevant passages from your knowledge base, grouped by document.
        </Banner>
      ) : null}
      <p className="long-form break-anywhere">{data.summary}</p>

      {data.highlights.length > 0 ? (
        <section className="result-section" aria-label="Highlights">
          <SectionTitle icon={<Sparkles size={14} aria-hidden="true" />}>Highlights</SectionTitle>
          <ul style={{ listStyle: 'none' }}>
            {data.highlights.map((h) => (
              <li key={h.title} className="digest-item">
                <span className="field-label break-anywhere">{h.title}</span>
                <span className="break-anywhere">
                  <span className="field-label">Why: </span>
                  {h.whyItMatters}
                </span>
                {h.nextStep ? (
                  <span className="break-anywhere">
                    <span className="field-label">Next: </span>
                    {h.nextStep}
                  </span>
                ) : null}
                <Citations ids={h.citations} sources={data.sources} />
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {data.themes.length > 0 ? (
        <section className="result-section" aria-label={data.degraded ? 'From your documents' : 'Themes'}>
          <SectionTitle icon={<Layers size={14} aria-hidden="true" />}>{data.degraded ? 'From your documents' : 'Themes'}</SectionTitle>
          <ul style={{ listStyle: 'none' }}>
            {data.themes.map((t) => (
              <li key={t.title} className="digest-item">
                <span className="field-label break-anywhere">{t.title}</span>
                <span className="break-anywhere">{t.summary}</span>
                <Citations ids={t.citations} sources={data.sources} />
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {data.exploreNext.length > 0 ? (
        <section className="result-section" aria-label="Explore next">
          <SectionTitle icon={<Compass size={14} aria-hidden="true" />}>Explore next</SectionTitle>
          <ul style={{ paddingLeft: 'var(--space-6)', paddingBottom: 'var(--space-3)' }}>
            {data.exploreNext.map((q) => (
              <li key={q} className="break-anywhere">
                {q}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {data.sources.length > 0 ? (
        <details className="result-section">
          <summary>
            <span className="badge tone-neutral">
              <FileText size={14} aria-hidden="true" />
              Sources
            </span>
            <span>{plural(data.sources.length, 'passage')} cited</span>
          </summary>
          <ol style={{ paddingLeft: 'var(--space-6)', paddingBottom: 'var(--space-3)' }}>
            {data.sources.map((s) => (
              <li key={s.citationId} className="break-anywhere">
                <span className="field-label">{s.documentName || 'Unknown document'}</span>
                {describeLocator(s.locator) ? <span className="muted"> · {describeLocator(s.locator)}</span> : null}
              </li>
            ))}
          </ol>
        </details>
      ) : null}

      <p className="muted">
        Searched with {plural(stats.queriesRun, 'query', 'queries')}; used {plural(stats.passagesUsed, 'passage')} from{' '}
        {plural(stats.documentsSeen, 'document')}.
      </p>
    </div>
  );
}
