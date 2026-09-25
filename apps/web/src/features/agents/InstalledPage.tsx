import { Bot } from 'lucide-react';
import { Link } from 'react-router';
import { useInstallations } from '../../api/queries';
import { ButtonLink } from '../../components/Button';
import { EmptyState, SkeletonBlock } from '../../components/Feedback';
import { Page, PageHeader, RouteTabs } from '../../components/Layout';
import { QueryView } from '../../components/QueryView';
import { StatusBadge } from '../../components/StatusBadge';
import { READINESS_NAMES } from '../../lib/status';
import { installationStatus } from './AgentBits';
import { CREW_TABS } from './MarketplacePage';
import { RunNowButton } from './RunNowButton';

export default function InstalledPage() {
  const installations = useInstallations();
  return (
    <Page>
      <PageHeader
        title="Your Crew"
        purpose="Agents installed on this device. Installed agents use no memory until they run."
        actions={
          <ButtonLink to="/agents/marketplace" variant="primary">
            Add to your crew
          </ButtonLink>
        }
      />
      <RouteTabs tabs={CREW_TABS} label="Crew views" />
      <QueryView
        query={installations}
        errorTitle="Could not load your crew"
        loading={<div className="grid-3">{[0, 1].map((i) => <SkeletonBlock key={i} />)}</div>}
        isEmpty={(d) => d.length === 0}
        empty={
          <EmptyState
            icon={Bot}
            title="Your crew is empty"
            action={
              <ButtonLink to="/agents/marketplace" variant="secondary">
                Browse the marketplace
              </ButtonLink>
            }
          >
            Install an agent to run it on demand or on a schedule.
          </EmptyState>
        }
      >
        {(list) => (
          <div className="grid-3">
            {list.map((inst) => {
              const blockers = inst.readiness.checks.filter((c) => c.status !== 'ok');
              return (
                <article key={inst.id} className="card stack-sm" aria-labelledby={`inst-${inst.id}`}>
                  <div className="row-between" style={{ alignItems: 'flex-start' }}>
                    <h2 id={`inst-${inst.id}`} className="card-title">
                      <Link to={`/agents/${encodeURIComponent(inst.id)}`}>{inst.agentName}</Link>
                    </h2>
                    <StatusBadge status={installationStatus(inst)} context="Readiness" />
                  </div>
                  <span className="muted">Version {inst.agentVersion}</span>
                  {blockers.length > 0 ? (
                    <ul className="muted" style={{ marginLeft: 20 }}>
                      {blockers.map((b) => (
                        <li key={`${b.name}-${b.resource ?? ''}`}>
                          {READINESS_NAMES[b.name]}: {b.detail}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <span className="muted">Model, connections, permissions and configuration are ready.</span>
                  )}
                  <div className="row">
                    <RunNowButton installation={inst} variant="secondary" />
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </QueryView>
    </Page>
  );
}
