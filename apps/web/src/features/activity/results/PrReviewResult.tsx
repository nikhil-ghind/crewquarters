import type { ReactNode } from 'react';
import { Banner } from '../../../components/Feedback';

export interface PrFinding {
  path: string;
  line: number;
  category: 'bug' | 'style';
  severity: 'high' | 'medium' | 'low';
  comment: string;
  suggestion: string | null;
}

export interface PrPull {
  number: number;
  title: string;
  author: string;
  url: string;
  headSha: string;
  status: 'reviewed' | 'skipped';
  skipReason: string | null;
  filesReviewed: number;
  filesSkipped: number;
  truncated: boolean;
  droppedFindings: number;
  posted: boolean;
  reviewUrl: string | null;
  postNote: string | null;
  findings: PrFinding[];
}

export interface PrReviewData {
  repo: string;
  dryRun: boolean;
  counts: { pulls: number; reviewed: number; skipped: number; findings: number; bugs: number; style: number; posted: number };
  pulls: PrPull[];
}

function str(v: unknown): string {
  return typeof v === 'string' ? v : '';
}
function num(v: unknown): number {
  return typeof v === 'number' ? v : 0;
}
function nullableStr(v: unknown): string | null {
  return typeof v === 'string' ? v : null;
}

export function parsePrReview(result: Record<string, unknown> | null): PrReviewData | null {
  if (!result || !Array.isArray(result.pulls) || typeof result.counts !== 'object' || result.counts === null) return null;
  const c = result.counts as Record<string, unknown>;
  return {
    repo: str(result.repo),
    dryRun: result.dryRun !== false,
    counts: { pulls: num(c.pulls), reviewed: num(c.reviewed), skipped: num(c.skipped), findings: num(c.findings), bugs: num(c.bugs), style: num(c.style), posted: num(c.posted) },
    pulls: result.pulls.flatMap((raw) => {
      if (typeof raw !== 'object' || raw === null) return [];
      const p = raw as Record<string, unknown>;
      const findings = Array.isArray(p.findings) ? p.findings : [];
      return [
        {
          number: num(p.number),
          title: str(p.title),
          author: str(p.author),
          url: str(p.url),
          headSha: str(p.headSha),
          status: p.status === 'skipped' ? 'skipped' : 'reviewed',
          skipReason: nullableStr(p.skipReason),
          filesReviewed: num(p.filesReviewed),
          filesSkipped: num(p.filesSkipped),
          truncated: p.truncated === true,
          droppedFindings: num(p.droppedFindings),
          posted: p.posted === true,
          reviewUrl: nullableStr(p.reviewUrl),
          postNote: nullableStr(p.postNote),
          findings: findings.flatMap((rawFinding) => {
            if (typeof rawFinding !== 'object' || rawFinding === null) return [];
            const f = rawFinding as Record<string, unknown>;
            return [
              {
                path: str(f.path),
                line: num(f.line),
                category: f.category === 'bug' ? 'bug' : 'style',
                severity: f.severity === 'high' || f.severity === 'medium' ? f.severity : 'low',
                comment: str(f.comment),
                suggestion: nullableStr(f.suggestion),
              } satisfies PrFinding,
            ];
          }),
        } satisfies PrPull,
      ];
    }),
  };
}

/** Only link out to GitHub itself over https. */
export function safeGithubLink(link: string | null): string | null {
  if (!link) return null;
  try {
    const url = new URL(link);
    return url.protocol === 'https:' && url.hostname === 'github.com' ? url.toString() : null;
  } catch {
    return null;
  }
}

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

function Pull({ pull }: { pull: PrPull }) {
  const link = safeGithubLink(pull.url);
  const reviewLink = safeGithubLink(pull.reviewUrl);
  return (
    <section className="result-section stack-sm">
      <h3 className="break-anywhere">
        {link ? (
          <a href={link} target="_blank" rel="noopener noreferrer">
            #{pull.number} {pull.title}
          </a>
        ) : (
          <>
            #{pull.number} {pull.title}
          </>
        )}{' '}
        <span className="muted">by {pull.author}</span>
      </h3>
      {pull.status === 'skipped' ? <p className="muted">Skipped: {pull.skipReason ?? 'not reviewed'}</p> : null}
      {pull.status === 'reviewed' ? (
        <p className="muted">
          {pull.filesReviewed} file{pull.filesReviewed === 1 ? '' : 's'} reviewed
          {pull.filesSkipped ? `, ${pull.filesSkipped} skipped` : ''}
          {pull.truncated ? ', large diffs were cut short' : ''}
          {pull.droppedFindings ? `, ${pull.droppedFindings} finding${pull.droppedFindings === 1 ? '' : 's'} dropped` : ''}
          {pull.status === 'reviewed' && pull.findings.length === 0 ? ' — nothing to report' : ''}
        </p>
      ) : null}
      {pull.posted ? (
        <p>
          Posted as a review
          {reviewLink ? (
            <>
              {' '}
              <a href={reviewLink} target="_blank" rel="noopener noreferrer">
                Open on GitHub
              </a>
            </>
          ) : null}
        </p>
      ) : null}
      {pull.postNote ? <p className="muted break-anywhere">{pull.postNote}</p> : null}
      <ul className="stack-sm" style={{ paddingLeft: 18 }}>
        {pull.findings.map((f) => (
          <li key={`${f.path}:${f.line}`} className="break-anywhere">
            <span className="mono">
              {f.path}:{f.line}
            </span>{' '}
            <strong>
              {f.category === 'bug' ? 'Bug' : 'Style'} · {f.severity}
            </strong>{' '}
            — {f.comment}
            {f.suggestion ? <span className="muted"> Suggestion: {f.suggestion}</span> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Counts, then each pull request with its findings. All agent strings render as text. */
export function PrReviewResult({ data }: { data: PrReviewData }) {
  const { counts } = data;
  return (
    <div className="stack">
      {data.dryRun ? (
        <Banner tone="info" title="Dry run">
          Nothing was posted to GitHub. Turn on “Post comments” in the agent’s configuration to post these findings.
        </Banner>
      ) : null}
      <div className="row" style={{ gap: 24, flexWrap: 'wrap' }}>
        <Stat label="Pull requests reviewed" value={counts.reviewed} />
        <Stat label="Possible bugs" value={counts.bugs} />
        <Stat label="Style notes" value={counts.style} />
        <Stat label="Reviews posted" value={counts.posted} />
      </div>
      {data.pulls.map((pull) => (
        <Pull key={pull.number} pull={pull} />
      ))}
    </div>
  );
}
