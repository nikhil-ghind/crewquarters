import { KeyValue } from '../../../components/Layout';
import { asSchema } from '../../../lib/jsonSchema';
import { CallerResult, parseCaller } from './CallerResult';
import { GmailDigestResult, parseDigest } from './GmailDigestResult';
import { PersonalSpaceResult, parsePersonalSpace } from './PersonalSpaceResult';
import { PrReviewResult, parsePrReview } from './PrReviewResult';

export const RENDERERS = {
  gmailDigest: 'crewquarters.gmail-digest/v1',
  caller: 'crewquarters.caller/v1',
  personalSpace: 'crewquarters.personal-space/v1',
  prReview: 'crewquarters.pr-review/v1',
} as const;

function scalar(v: unknown): string | null {
  if (v === null || v === undefined) return null;
  if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') return String(v);
  return null;
}

/** Fallback for results without a known renderer: top-level values as text. */
export function GenericResult({ result }: { result: Record<string, unknown> }) {
  const entries = Object.entries(result)
    .map(([k, v]) => [k, scalar(v)] as const)
    .filter((e): e is readonly [string, string] => e[1] !== null);
  if (entries.length === 0) return <p className="muted">The result is available under Raw result.</p>;
  return <KeyValue items={entries.map(([k, v]) => [k, <span key={k} className="break-anywhere">{v}</span>])} />;
}

/**
 * Selects the safe structured renderer declared by the agent's result schema
 * (`x-crewquarters-renderer`). All agent strings are rendered as text.
 */
export function ResultView({
  result,
  resultSchema,
  timeZone,
}: {
  result: Record<string, unknown>;
  resultSchema: Record<string, unknown> | null | undefined;
  timeZone: string;
}) {
  const renderer = asSchema(resultSchema)['x-crewquarters-renderer'];
  if (renderer === RENDERERS.gmailDigest || (!renderer && 'groups' in result && 'counts' in result)) {
    const digest = parseDigest(result);
    if (digest) return <GmailDigestResult digest={digest} />;
  }
  if (renderer === RENDERERS.caller || (!renderer && 'rows' in result && 'operatorDecision' in result)) {
    const caller = parseCaller(result);
    if (caller) return <CallerResult data={caller} timeZone={timeZone} />;
  }
  if (renderer === RENDERERS.personalSpace || (!renderer && 'themes' in result && 'highlights' in result && 'status' in result)) {
    const space = parsePersonalSpace(result);
    if (space) return <PersonalSpaceResult data={space} timeZone={timeZone} />;
  }
  if (renderer === RENDERERS.prReview) {
    const review = parsePrReview(result);
    if (review) return <PrReviewResult data={review} />;
  }
  return <GenericResult result={result} />;
}
