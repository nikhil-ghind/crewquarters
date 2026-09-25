import { Download, MessagesSquare, Play, Power, RotateCw, X } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router';
import { isApiError, remediation } from '../../api/errors';
import { useActionGuard } from '../../api/guards';
import { useIntentKey, useModelAction } from '../../api/mutations';
import { useMemory, useModels } from '../../api/queries';
import type { ModelOut } from '../../api/schema';
import { Button, ButtonLink } from '../../components/Button';
import { ConfirmDialog } from '../../components/Dialog';
import { Banner } from '../../components/Feedback';
import { KeyValue } from '../../components/Layout';
import { useFeedback } from '../../components/Toast';
import { formatBytes } from '../../lib/format';

/** Primary actions follow state (section 13.8). */
export function ModelActions({ model, detailLink = false }: { model: ModelOut; detailLink?: boolean }) {
  const action = useModelAction();
  const [key, resetKey] = useIntentKey();
  const guard = useActionGuard();
  const memory = useMemory();
  const models = useModels();
  const { toast } = useFeedback();
  const [confirmLoad, setConfirmLoad] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [clearPartial, setClearPartial] = useState(false);
  const leases = model.activeLeases ?? [];
  const idleResident = (models.data ?? []).filter(
    (m) => m.id !== model.id && m.memoryState === 'READY' && (m.activeLeases ?? []).length === 0,
  );

  const run = (kind: Parameters<typeof action.mutate>[0]['action'], extra: { clear?: boolean } = {}, done?: () => void) =>
    action.mutate(
      { modelId: model.id, action: kind, key, ...extra },
      {
        onSuccess: () => {
          resetKey();
          done?.();
        },
      },
    );

  const available = memory.data?.availableBytes ?? null;
  const expected = model.expectedMemoryBytes ?? null;
  const reserve = memory.data?.systemReserveBytes ?? null;
  const admissionError = action.isError && isApiError(action.error) && action.error.status === 409 ? action.error : null;

  const detail = detailLink ? (
    <ButtonLink to={`/models/${encodeURIComponent(model.id)}`} variant="tertiary">
      Details<span className="sr-only"> for {model.displayName}</span>
    </ButtonLink>
  ) : null;

  let buttons: React.ReactNode = null;
  if (model.downloadState === 'NOT_INSTALLED') {
    buttons = (
      <Button variant="primary" icon={<Download size={16} aria-hidden="true" />} busy={action.isPending} busyLabel="Starting…" disabledReason={guard.runtime} onClick={() => run('install')}>
        Install model
      </Button>
    );
  } else if (model.downloadState === 'DOWNLOADING') {
    buttons = (
      <>
        {!detailLink ? null : (
          <ButtonLink to={`/models/${encodeURIComponent(model.id)}`} variant="secondary">
            View progress
          </ButtonLink>
        )}
        <Button icon={<X size={16} aria-hidden="true" />} onClick={() => setConfirmCancel(true)} disabledReason={guard.offline}>
          Cancel download
        </Button>
      </>
    );
  } else if (model.downloadState === 'DOWNLOAD_ERROR' || model.memoryState === 'LOAD_ERROR' || model.memoryState === 'ERROR') {
    buttons = (
      <>
        {detailLink ? (
          <ButtonLink to={`/models/${encodeURIComponent(model.id)}`} variant="secondary">
            View error
          </ButtonLink>
        ) : null}
        <Button
          variant="primary"
          icon={<RotateCw size={16} aria-hidden="true" />}
          busy={action.isPending}
          busyLabel="Retrying…"
          disabledReason={guard.runtime}
          onClick={() => run(model.downloadState === 'DOWNLOAD_ERROR' ? 'install' : 'load')}
        >
          Retry
        </Button>
      </>
    );
  } else if (model.downloadState === 'INSTALLED' && model.memoryState === 'NOT_LOADED') {
    buttons = (
      <Button icon={<Play size={16} aria-hidden="true" />} onClick={() => setConfirmLoad(true)} disabledReason={guard.runtime}>
        Load now
      </Button>
    );
  } else if (model.memoryState === 'READY') {
    buttons = (
      <>
        <ButtonLink to="/chat" variant="secondary" icon={<MessagesSquare size={16} aria-hidden="true" />}>
          Open chat
        </ButtonLink>
        <Button
          icon={<Power size={16} aria-hidden="true" />}
          busy={action.isPending}
          busyLabel="Unloading…"
          disabledReason={guard.runtime ?? (leases.length > 0 ? `In use by ${leases.map((l) => l.label).join(', ')}.` : null)}
          onClick={() => run('unload', {}, () => toast(`${model.displayName} is unloading.`))}
        >
          Unload
        </Button>
      </>
    );
  }

  return (
    <div className="stack-sm">
      <div className="row">
        {buttons}
        {detail}
      </div>
      {action.isError && !admissionError ? (
        <p className="field-error" role="alert">
          {isApiError(action.error) ? remediation(action.error) : 'The action failed.'}
        </p>
      ) : null}
      {admissionError ? (
        <Banner tone="warning" role="alert" title="Not enough free memory to load now">
          {admissionError.message} The model stays installed on disk.
          {idleResident.length > 0 ? (
            <span className="row" style={{ marginTop: 8 }}>
              {idleResident.map((m) => (
                <Button key={m.id} onClick={() => action.mutate({ modelId: m.id, action: 'unload', key: `${key}-${m.id}` })}>
                  Unload idle {m.displayName}
                </Button>
              ))}
            </span>
          ) : (
            <span> Wait for running work to finish, then try again.</span>
          )}
        </Banner>
      ) : null}
      <ConfirmDialog
        open={confirmLoad}
        title={`Load ${model.displayName} into memory?`}
        consequence={
          <div className="stack-sm">
            <p>Loading keeps the model ready for chat and agents. It unloads after the idle timeout when nothing uses it.</p>
            <KeyValue
              items={[
                ['Expected allocation', expected ? formatBytes(expected) : 'Measured on first load'],
                ['Available now', available !== null ? formatBytes(available) : 'Not reported'],
                [
                  'Left after loading',
                  available !== null && expected ? formatBytes(Math.max(0, available - expected)) : '—',
                ],
                ['System reserve (always kept)', reserve !== null ? formatBytes(reserve) : '—'],
              ]}
            />
            <p className="muted">The device checks memory again before loading and refuses if it would be unsafe.</p>
          </div>
        }
        confirmLabel="Load model"
        busyLabel="Requesting…"
        busy={action.isPending}
        onConfirm={() => run('load', {}, () => setConfirmLoad(false))}
        onCancel={() => setConfirmLoad(false)}
      >
        {admissionError ? <p className="field-error">{admissionError.message}</p> : null}
      </ConfirmDialog>
      <ConfirmDialog
        open={confirmCancel}
        title="Cancel this download?"
        consequence="The download stops. You can resume it later from where it stopped unless you delete the partial files."
        confirmLabel="Cancel download"
        cancelLabel="Keep downloading"
        busy={action.isPending}
        onConfirm={() => run('cancel', { clear: clearPartial }, () => setConfirmCancel(false))}
        onCancel={() => setConfirmCancel(false)}
      >
        <label className="check-row">
          <input type="checkbox" checked={clearPartial} onChange={(e) => setClearPartial(e.target.checked)} />
          <span>Also delete the partially downloaded files</span>
        </label>
      </ConfirmDialog>
      {model.memoryState === 'READY' && leases.length > 0 && !detailLink ? (
        <p className="muted">
          Unload is available when nothing holds the model. <Link to="/activity/runs">See running work</Link>.
        </p>
      ) : null}
    </div>
  );
}
