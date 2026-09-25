import { Cpu } from 'lucide-react';
import { Link, useSearchParams } from 'react-router';
import { useModels } from '../../api/queries';
import { useModelEvents } from '../../api/streams';
import type { ModelOut } from '../../api/schema';
import { EmptyState, SkeletonBlock } from '../../components/Feedback';
import { Page, PageHeader } from '../../components/Layout';
import { LocalityChip } from '../../components/LocalityChip';
import { QueryView } from '../../components/QueryView';
import { StatusBadge } from '../../components/StatusBadge';
import { formatBytes } from '../../lib/format';
import { MODEL_DOWNLOAD_STATUS, MODEL_MEMORY_STATUS } from '../../lib/status';
import { licenseText } from './modelInfo';
import { ModelActions } from './ModelActions';
import { ModelProgress } from './ModelProgress';
import { isModelBusy } from '../../api/queries';

export function ModelStates({ model }: { model: ModelOut }) {
  return (
    <span className="row">
      <StatusBadge status={MODEL_DOWNLOAD_STATUS[model.downloadState]} context="Disk" />
      <StatusBadge status={MODEL_MEMORY_STATUS[model.memoryState]} context="Memory" />
    </span>
  );
}

function ModelCard({ model }: { model: ModelOut }) {
  useModelEvents(model.id, isModelBusy(model));
  const leases = model.activeLeases ?? [];
  return (
    <article className="card stack-sm" aria-labelledby={`model-${model.id}`}>
      <div className="row-between" style={{ alignItems: 'flex-start' }}>
        <h2 id={`model-${model.id}`} className="card-title">
          <Link to={`/models/${encodeURIComponent(model.id)}`}>{model.displayName}</Link>
        </h2>
        <LocalityChip provider="local" />
      </div>
      <dl className="kv" style={{ fontSize: 13 }}>
        <dt>On disk</dt>
        <dd>
          <StatusBadge status={MODEL_DOWNLOAD_STATUS[model.downloadState]} context="Disk" /> {model.diskBytes ? formatBytes(model.diskBytes) : ''}
        </dd>
        <dt>In memory</dt>
        <dd>
          <StatusBadge status={MODEL_MEMORY_STATUS[model.memoryState]} context="Memory" />{' '}
          {model.reservedBytes > 0 ? `${formatBytes(model.reservedBytes)} reserved` : model.expectedMemoryBytes ? `needs about ${formatBytes(model.expectedMemoryBytes)}` : ''}
        </dd>
        <dt>Context</dt>
        <dd>{model.contextLimit ? `${model.contextLimit.toLocaleString()} tokens` : '—'}</dd>
        <dt>Capabilities</dt>
        <dd>{(model.capabilities ?? []).join(', ') || '—'}</dd>
        <dt>This hardware</dt>
        <dd>{model.validation ?? 'Not yet validated'}</dd>
        <dt>License</dt>
        <dd>{licenseText(model)}</dd>
        {leases.length > 0 ? (
          <>
            <dt>Used by</dt>
            <dd>{leases.map((l) => l.label).join(', ')}</dd>
          </>
        ) : null}
      </dl>
      <ModelProgress model={model} />
      <ModelActions model={model} detailLink />
    </article>
  );
}

export default function ModelsPage() {
  const models = useModels();
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') === 'installed' ? 'installed' : 'available';
  return (
    <Page>
      <PageHeader
        title="Models"
        purpose="Install model files to disk, then load them into memory only when needed. Installed does not mean loaded."
      />
      <div className="tabs" role="tablist" aria-label="Model views">
        {(['available', 'installed'] as const).map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            id={`tab-${t}`}
            aria-selected={tab === t}
            aria-controls="models-panel"
            className="tab"
            onClick={() => setParams(t === 'installed' ? { tab: 'installed' } : {})}
          >
            {t === 'available' ? 'Available' : 'Installed'}
          </button>
        ))}
      </div>
      <div id="models-panel" role="tabpanel" aria-labelledby={`tab-${tab}`}>
        <QueryView
          query={models}
          errorTitle="Could not load models"
          loading={<div className="grid-3">{[0, 1, 2].map((i) => <SkeletonBlock key={i} lines={5} />)}</div>}
        >
          {(list) => {
            const shown = tab === 'installed' ? list.filter((m) => m.downloadState === 'INSTALLED' || m.downloadState === 'DELETING') : list;
            return shown.length === 0 ? (
              <EmptyState icon={Cpu} title={tab === 'installed' ? 'No models installed' : 'No models available'}>
                {tab === 'installed' ? 'Install a model from the Available tab.' : 'The model catalog is empty on this device.'}
              </EmptyState>
            ) : (
              <div className="grid-3">
                {shown.map((m) => (
                  <ModelCard key={m.id} model={m} />
                ))}
              </div>
            );
          }}
        </QueryView>
      </div>
    </Page>
  );
}
