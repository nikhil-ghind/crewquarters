import { Play } from 'lucide-react';
import { useNavigate } from 'react-router';
import { isApiError, remediation } from '../../api/errors';
import { useActionGuard } from '../../api/guards';
import { useCreateRun, useIntentKey } from '../../api/mutations';
import type { InstallationOut } from '../../api/schema';
import { Button, type ButtonVariant } from '../../components/Button';
import { useFeedback } from '../../components/Toast';
import { READINESS_NAMES } from '../../lib/status';

export function runBlockedReason(inst: InstallationOut): string | null {
  if (inst.needsReapproval) return 'A new version changed its permissions. Review and approve them first.';
  if (!inst.enabled) return 'This agent is disabled.';
  const blocker = inst.readiness.checks.find((c) => c.status !== 'ok');
  if (!inst.readiness.ready && blocker) return `${READINESS_NAMES[blocker.name]}: ${blocker.detail}`;
  if (!inst.readiness.ready) return 'This agent is not ready to run.';
  return null;
}

/** Creates a run and immediately opens its detail page (section 13.7). */
export function RunNowButton({ installation, variant = 'primary' }: { installation: InstallationOut; variant?: ButtonVariant }) {
  const create = useCreateRun();
  const [key, resetKey] = useIntentKey();
  const guard = useActionGuard();
  const navigate = useNavigate();
  const { announce } = useFeedback();
  const reason = guard.runtime ?? runBlockedReason(installation);
  return (
    <>
      <Button
        variant={variant}
        icon={<Play size={16} aria-hidden="true" />}
        busy={create.isPending}
        busyLabel="Starting…"
        disabledReason={reason}
        onClick={() =>
          create.mutate(
            { installationId: installation.id, key },
            {
              onSuccess: (run) => {
                resetKey();
                void navigate(`/runs/${encodeURIComponent(run.id)}`);
              },
              onError: (e) => announce(isApiError(e) ? remediation(e) : 'The run could not start.', 'assertive'),
            },
          )
        }
      >
        Run now<span className="sr-only"> {installation.agentName}</span>
      </Button>
      {create.isError ? (
        <p className="field-error" role="alert">
          {isApiError(create.error) && create.error.isOutcomeUnknown
            ? 'The device did not confirm the run. Check Activity before starting another.'
            : isApiError(create.error)
              ? remediation(create.error)
              : 'The run could not start.'}
        </p>
      ) : null}
    </>
  );
}
