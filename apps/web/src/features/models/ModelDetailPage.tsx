import { Trash2 } from 'lucide-react';
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { useActionGuard } from '../../api/guards';
import { useIntentKey, useModelAction } from '../../api/mutations';
import { isModelBusy, useModel, useSettings } from '../../api/queries';
import { useModelEvents } from '../../api/streams';
import type { ModelOut } from '../../api/schema';
import { Button } from '../../components/Button';
import { ConfirmDialog } from '../../components/Dialog';
import { ErrorPanel } from '../../components/Feedback';
import { Advanced, Card, KeyValue, Page, PageHeader, RawJson } from '../../components/Layout';
import { LocalityChip } from '../../components/LocalityChip';
import { QueryView } from '../../components/QueryView';
import { formatBytes, formatDateTime, formatDuration, formatRelative, formatUtc } from '../../lib/format';
import { useTimeZone } from '../common/useTimeZone';
import { ModelActions } from './ModelActions';
import { licenseText } from './modelInfo';
import { ModelProgress } from './ModelProgress';
import { ModelStates } from './ModelsPage';

export default function ModelDetailPage() {
  const { modelId = '' } = useParams();
  const model = useModel(modelId);
  return (
    <Page>
      <QueryView query={model} errorTitle="Could not load this model">
        {(m) => <ModelView model={m} />}
      </QueryView>
    </Page>
  );
}

function ModelView({ model }: { model: ModelOut }) {
  const stream = useModelEvents(model.id, isModelBusy(model));
  const timeZone = useTimeZone();
  const settings = useSettings();
  const action = useModelAction();
  const [key] = useIntentKey();
  const guard = useActionGuard();
  const navigate = useNavigate();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const leases = model.activeLeases ?? [];
  const canDelete = model.downloadState === 'INSTALLED' && model.memoryState === 'NOT_LOADED';

  return (
    <>
      <PageHeader
        title={model.displayName}
        purpose="Download state and memory residency are tracked separately."
        breadcrumbs={[{ label: 'Models', to: '/models' }, { label: model.displayName }]}
        status={
          <>
            <ModelStates model={model} />
            <LocalityChip provider="local" long />
            {stream === 'reconnecting' || stream === 'polling' ? <span className="badge tone-warning">Live updates paused—reconnecting</span> : null}
          </>
        }
      />
      <Card title="Status">
        <div className="stack">
          <ModelProgress model={model} />
          {model.memoryState === 'READY' && leases.length === 0 && model.idleUnloadAt ? (
            <p title={formatUtc(model.idleUnloadAt)}>
              Not in use. Unloads {formatRelative(model.idleUnloadAt)} (idle timeout {formatDuration(settings.data?.idleUnloadSeconds ?? null)}) unless
              chat or a run uses it.
            </p>
          ) : null}
          <ModelActions model={model} />
        </div>
      </Card>
      <div className="grid-2">
        <Card title="Details">
          <KeyValue
            items={[
              ['Family', model.family],
              ['Disk size', formatBytes(model.diskBytes)],
              ['Memory when loaded', model.expectedMemoryBytes ? `About ${formatBytes(model.expectedMemoryBytes)}` : 'Measured on first load'],
              ['Reserved now', formatBytes(model.reservedBytes)],
              ['Context limit', model.contextLimit ? `${model.contextLimit.toLocaleString()} tokens` : '—'],
              ['Capabilities', (model.capabilities ?? []).join(', ') || '—'],
              ['Validated on this hardware', model.validation ?? 'Not yet validated'],
              ['License', licenseText(model)],
              ['Ready since', model.readyAt ? formatDateTime(model.readyAt, timeZone) : '—'],
            ]}
          />
        </Card>
        <Card title="Who is using it" subtitle="Leases keep the model loaded; it can unload only when none remain.">
          {leases.length === 0 ? (
            <p className="muted">Nothing is using this model.</p>
          ) : (
            <ul className="stack-sm" style={{ listStyle: 'none' }}>
              {leases.map((l) => (
                <li key={l.id} className="row-between">
                  <span>{l.label}</span>
                  <span className="muted" title={formatUtc(l.expiresAt)}>
                    {l.holderType === 'chat' ? 'Chat' : l.holderType === 'run' ? 'Agent run' : 'Manual'} · lease renews until{' '}
                    {formatDateTime(l.expiresAt, timeZone)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <Advanced>
        <KeyValue items={[['Model ID', <span key="id" className="mono">{model.id}</span>], ['Backend', model.backend], ['Revision', model.download?.revision ?? '—']]} />
        <RawJson value={model} label="Raw model record" />
        {canDelete ? (
          <div className="row">
            <Button icon={<Trash2 size={16} aria-hidden="true" />} onClick={() => setConfirmDelete(true)} disabledReason={guard.offline}>
              Delete model files
            </Button>
          </div>
        ) : null}
      </Advanced>
      <ConfirmDialog
        open={confirmDelete}
        title={`Delete ${model.displayName} from disk?`}
        consequence={`This frees ${formatBytes(model.diskBytes)}. Agents and chat that use this model cannot run until it is installed again.`}
        confirmLabel="Delete model files"
        destructive
        busy={action.isPending}
        onConfirm={() => action.mutate({ modelId: model.id, action: 'delete', key }, { onSuccess: () => void navigate('/models') })}
        onCancel={() => setConfirmDelete(false)}
      >
        {action.isError ? <ErrorPanel error={action.error} title="Could not delete the files" /> : null}
      </ConfirmDialog>
    </>
  );
}
