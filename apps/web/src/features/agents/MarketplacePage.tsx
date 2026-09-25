import { Search, Store } from 'lucide-react';
import { useId, useMemo, useState } from 'react';
import { Link } from 'react-router';
import { useCatalog, useConnections } from '../../api/queries';
import type { CatalogAgentOut } from '../../api/schema';
import { ButtonLink } from '../../components/Button';
import { EmptyState, SkeletonBlock } from '../../components/Feedback';
import { Page, PageHeader, RouteTabs } from '../../components/Layout';
import { QueryView } from '../../components/QueryView';
import { StatusBadge } from '../../components/StatusBadge';
import { usesCloud } from '../../lib/permissions';
import { CloudUseBadge, RequirementBadges, TrustBadge, triggerText } from './AgentBits';
import { profileFamilies, requiredConnectionsText } from './install';

export const CREW_TABS = [
  { to: '/agents/installed', label: 'Your Crew' },
  { to: '/agents/marketplace', label: 'Marketplace' },
];

/** 'all' or a specific value. */
type Filter = string;

function matches(agent: CatalogAgentOut, q: string, trigger: Filter, connector: Filter, locality: Filter, installed: Filter) {
  const text = `${agent.name} ${agent.summary} ${agent.publisher}`.toLowerCase();
  if (q && !text.includes(q.toLowerCase())) return false;
  if (trigger !== 'all' && !agent.latest.triggers.includes(trigger)) return false;
  const connectors = requiredConnectionsText(agent.latest);
  if (connector === 'none' && connectors.length > 0) return false;
  if (connector !== 'all' && connector !== 'none' && !connectors.includes(connector)) return false;
  const cloud = usesCloud(agent.latest.permissions).length > 0;
  if (locality === 'local' && cloud) return false;
  if (locality === 'cloud' && !cloud) return false;
  if (installed === 'installed' && !agent.installed) return false;
  if (installed === 'available' && agent.installed) return false;
  return true;
}

function Select({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: [string, string][] }) {
  const id = useId();
  return (
    <div className="field" style={{ minWidth: 160 }}>
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <select id={id} className="select" value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
    </div>
  );
}

export function AgentCard({ agent, connections }: { agent: CatalogAgentOut; connections?: Parameters<typeof RequirementBadges>[0]['connections'] }) {
  const version = agent.latest;
  const families = profileFamilies(version);
  return (
    <article className="card stack-sm" aria-labelledby={`agent-${agent.agentId}`}>
      <div className="row-between" style={{ alignItems: 'flex-start' }}>
        <h2 id={`agent-${agent.agentId}`} className="card-title">
          <Link to={`/agents/marketplace/${encodeURIComponent(agent.agentId)}`}>{agent.name}</Link>
        </h2>
        {agent.installed ? <StatusBadge status={{ label: 'In your crew', tone: 'success', icon: 'check' }} /> : null}
      </div>
      <p className="break-anywhere">{agent.summary}</p>
      <div className="row">
        <TrustBadge agent={agent} />
        <CloudUseBadge permissions={version.permissions} />
        {usesCloud(version.permissions).length === 0 && families.length > 0 ? (
          <span className="badge chip-local">Local model</span>
        ) : null}
      </div>
      <dl className="kv" style={{ fontSize: 13 }}>
        <dt>Publisher</dt>
        <dd>
          {agent.publisher} · v{agent.currentVersion}
        </dd>
        <dt>Runs</dt>
        <dd>{triggerText(version.triggers)}</dd>
        <dt>Needs</dt>
        <dd>
          <RequirementBadges agent={agent} connections={connections} />
        </dd>
        <dt>Model</dt>
        <dd>{families.length > 0 ? families.join(', ') : 'No model'}</dd>
        <dt>This device</dt>
        <dd>
          {version.compatible ? (
            <StatusBadge status={{ label: 'Compatible', tone: 'success', icon: 'check' }} />
          ) : (
            <span className="stack-sm" style={{ gap: 2 }}>
              <StatusBadge status={{ label: 'Not compatible', tone: 'danger', icon: 'x' }} />
              <span className="muted">{version.compatibilityIssues.join(' ')}</span>
            </span>
          )}
        </dd>
      </dl>
      <div className="row">
        <ButtonLink to={`/agents/marketplace/${encodeURIComponent(agent.agentId)}`} variant="secondary">
          View details<span className="sr-only"> of {agent.name}</span>
        </ButtonLink>
      </div>
    </article>
  );
}

export default function MarketplacePage() {
  const catalog = useCatalog();
  const connections = useConnections();
  const [q, setQ] = useState('');
  const [trigger, setTrigger] = useState<Filter>('all');
  const [connector, setConnector] = useState<Filter>('all');
  const [locality, setLocality] = useState<Filter>('all');
  const [installed, setInstalled] = useState<Filter>('all');
  const searchId = useId();
  const filtered = useMemo(
    () => (catalog.data ?? []).filter((a) => matches(a, q, trigger, connector, locality, installed)),
    [catalog.data, q, trigger, connector, locality, installed],
  );

  return (
    <Page>
      <PageHeader
        title="Marketplace"
        purpose="Curated agents you can add to your crew. You review every permission before installing."
        breadcrumbs={[{ label: 'Crew', to: '/agents/installed' }, { label: 'Marketplace' }]}
      />
      <RouteTabs tabs={CREW_TABS} label="Crew views" />
      <div className="row" style={{ alignItems: 'flex-end' }}>
        <div className="field" style={{ flex: '1 1 240px' }}>
          <label className="field-label" htmlFor={searchId}>
            Search
          </label>
          <div style={{ position: 'relative' }}>
            <Search size={16} aria-hidden="true" style={{ position: 'absolute', left: 12, top: 12, color: 'var(--color-text-muted)' }} />
            <input
              id={searchId}
              type="search"
              className="input"
              style={{ paddingLeft: 36 }}
              placeholder="Name or description"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
        </div>
        <Select label="Trigger" value={trigger} onChange={setTrigger} options={[['all', 'Any'], ['manual', 'Run on demand'], ['schedule', 'Scheduled']]} />
        <Select label="Connection" value={connector} onChange={setConnector} options={[['all', 'Any'], ['google', 'Google'], ['twilio', 'Twilio'], ['none', 'None needed']]} />
        <Select label="Processing" value={locality} onChange={setLocality} options={[['all', 'Local or cloud'], ['local', 'Local only'], ['cloud', 'Uses cloud']]} />
        <Select label="Installed" value={installed} onChange={setInstalled} options={[['all', 'All'], ['installed', 'In your crew'], ['available', 'Not installed']]} />
      </div>
      <QueryView
        query={catalog}
        errorTitle="Could not load the marketplace"
        loading={<div className="grid-3">{[0, 1, 2].map((i) => <SkeletonBlock key={i} lines={4} />)}</div>}
        isEmpty={(d) => d.length === 0}
        empty={<EmptyState icon={Store} title="No agents in the catalog">The device has no curated agents yet.</EmptyState>}
      >
        {() =>
          filtered.length === 0 ? (
            <EmptyState icon={Search} title="No agents match">
              Clear a filter or search for something else.
            </EmptyState>
          ) : (
            <div className="grid-3">
              {filtered.map((a) => (
                <AgentCard key={a.agentId} agent={a} connections={connections.data} />
              ))}
            </div>
          )
        }
      </QueryView>
    </Page>
  );
}
