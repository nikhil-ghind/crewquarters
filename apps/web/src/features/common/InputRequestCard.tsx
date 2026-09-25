import { useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, Hand } from 'lucide-react';
import { useId, useMemo, useState, type FormEvent } from 'react';
import { Link } from 'react-router';
import { isApiError, remediation, type FieldError } from '../../api/errors';
import { useActionGuard } from '../../api/guards';
import { useAnswerInput, useIntentKey } from '../../api/mutations';
import { keys } from '../../api/queries';
import type { InputRequestOut } from '../../api/schema';
import { Button } from '../../components/Button';
import { Banner } from '../../components/Feedback';
import { ErrorSummary } from '../../components/Field';
import { SchemaForm } from '../../components/SchemaForm';
import { StatusBadge } from '../../components/StatusBadge';
import { useFeedback } from '../../components/Toast';
import { formatDateTime, formatRelative, formatUtc } from '../../lib/format';
import { asSchema, validate } from '../../lib/jsonSchema';
import { INPUT_STATUS } from '../../lib/status';
import { parsePreview, PreviewBlocks } from './PreviewBlocks';

interface InputRequestCardProps {
  request: InputRequestOut;
  /** Show a link to the run (dashboard and Activity lists). */
  showRunLink?: boolean;
  timeZone?: string;
  headingLevel?: 2 | 3;
}

/**
 * A Crew Request: agent name, why input is needed, the requested action, preview,
 * deadline and the exact consequence of submitting. The same card is used on the
 * dashboard, Activity › Approvals and run detail; the saved request version makes the
 * server reject stale or double answers, whichever place answers first.
 */
export function InputRequestCard({ request, showRunLink = false, timeZone, headingLevel = 2 }: InputRequestCardProps) {
  const client = useQueryClient();
  const answer = useAnswerInput();
  const [key, resetKey] = useIntentKey();
  const guard = useActionGuard();
  const { announce } = useFeedback();
  const preview = useMemo(() => parsePreview(request.preview), [request.preview]);
  const schema = useMemo(() => asSchema(request.schema), [request.schema]);
  const [value, setValue] = useState<Record<string, unknown>>({});
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [submitted, setSubmitted] = useState<InputRequestOut | null>(null);
  const [conflict, setConflict] = useState<string | null>(null);
  const [pendingChoice, setPendingChoice] = useState<string | null>(null);
  const titleId = useId();
  const Heading = headingLevel === 2 ? 'h2' : 'h3';

  const current = submitted ?? request;
  const pending = current.state === 'pending' && !conflict;

  const send = (answerValue: unknown, choice?: string) => {
    setPendingChoice(choice ?? null);
    setConflict(null);
    answer.mutate(
      { id: request.id, version: request.version, value: answerValue, key },
      {
        onSuccess: (result) => {
          setSubmitted(result);
          resetKey();
          announce(`Answer submitted for ${request.title}.`);
        },
        onError: (error) => {
          if (isApiError(error) && error.status === 409) {
            setConflict(
              error.code === 'VERSION_CONFLICT'
                ? 'This request changed or was answered somewhere else. The latest version is shown below.'
                : 'This request was already answered, so your answer was not sent again.',
            );
            void client.invalidateQueries({ queryKey: keys.inputRequestsAll });
            announce('This request was already answered.', 'assertive');
          } else if (isApiError(error) && error.status === 422) {
            setErrors(error.fieldErrors);
            announce('The answer was not accepted. Fix the highlighted fields.', 'assertive');
          } else if (isApiError(error) && error.isOutcomeUnknown) {
            // Keep the same idempotency key: retrying replays instead of answering twice.
            void client.invalidateQueries({ queryKey: keys.inputRequestsAll });
          }
        },
      },
    );
  };

  const onSubmitForm = (event: FormEvent) => {
    event.preventDefault();
    const localErrors = validate(schema, value);
    setErrors(localErrors);
    if (localErrors.length > 0) return;
    send(value);
  };

  const deadline = (
    <span title={formatUtc(current.deadline)}>
      {formatDateTime(current.deadline, timeZone)} ({formatRelative(current.deadline)})
    </span>
  );

  return (
    <article className={`card ${pending ? 'card-attention' : ''}`} aria-labelledby={titleId}>
      <div className="card-header">
        <div className="stack-sm" style={{ gap: 2 }}>
          <span className="row muted">
            <Hand size={16} aria-hidden="true" />
            {current.agentName ?? 'Agent'} asks
          </span>
          <Heading id={titleId} className="card-title break-anywhere">
            {current.title}
          </Heading>
        </div>
        <StatusBadge status={INPUT_STATUS[current.state]} context="Request" />
      </div>
      <div className="stack">
        <p className="long-form break-anywhere">{current.prompt}</p>
        <PreviewBlocks blocks={preview.blocks} caption={`${current.title} preview`} />
        {preview.consequence ? (
          <Banner tone="warning" role="none" title="If you approve">
            <span className="break-anywhere">{preview.consequence}</span>
          </Banner>
        ) : null}
        <dl className="kv">
          <dt>Answer by</dt>
          <dd>{deadline}</dd>
          {showRunLink ? (
            <>
              <dt>Run</dt>
              <dd>
                <Link to={`/runs/${encodeURIComponent(current.runId)}`}>Open run</Link>
              </dd>
            </>
          ) : null}
        </dl>

        {conflict ? <Banner tone="info" title="Nothing more to do">{conflict}</Banner> : null}

        {current.state === 'answered' ? (
          <div className="row" role="status">
            <CheckCircle2 size={18} aria-hidden="true" style={{ color: 'var(--color-success)' }} />
            <span>
              Answer submitted{current.answeredAt ? ` ${formatRelative(current.answeredAt)}` : ''}. The run continues on its
              own.
            </span>
          </div>
        ) : null}

        {pending && answer.isError && !conflict && isApiError(answer.error) && answer.error.status !== 422 ? (
          <Banner tone="danger" role="alert" title="The answer was not confirmed">
            {remediation(answer.error)}
          </Banner>
        ) : null}

        {pending ? (
          preview.choices.length > 0 ? (
            <div className="row" role="group" aria-label="Choose an answer">
              {preview.choices.map((choice) => (
                <Button
                  key={choice.value}
                  variant={choice.style === 'primary' ? 'primary' : choice.style === 'danger' ? 'danger' : 'secondary'}
                  busy={answer.isPending && pendingChoice === choice.value}
                  busyLabel="Submitting…"
                  disabled={answer.isPending}
                  disabledReason={choice === preview.choices[0] ? guard.offline : null}
                  onClick={() => send({ choice: choice.value }, choice.value)}
                >
                  {choice.label}
                </Button>
              ))}
            </div>
          ) : (
            <form className="form" onSubmit={onSubmitForm} noValidate>
              <ErrorSummary errors={errors} />
              <SchemaForm
                schema={schema}
                value={value}
                onChange={setValue}
                errors={errors}
                disabled={answer.isPending}
                idPrefix={`answer-${request.id}`}
              />
              <div className="row">
                <Button
                  type="submit"
                  variant="primary"
                  busy={answer.isPending}
                  busyLabel="Submitting…"
                  disabledReason={guard.offline}
                >
                  Send answer
                </Button>
              </div>
            </form>
          )
        ) : null}
      </div>
    </article>
  );
}
