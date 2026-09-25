import { Activity } from 'lucide-react';
import { useId, useState } from 'react';
import { useInputRequests, useInstallations, useRuns, type RunFilters } from '../../api/queries';
import { RUN_STATES, type RunState } from '../../api/schema';
import { ButtonLink } from '../../components/Button';
import { EmptyState, SkeletonTable } from '../../components/Feedback';
import { Page, PageHeader, RouteTabs } from '../../components/Layout';
import { QueryView } from '../../components/QueryView';
import { RUN_STATUS } from '../../lib/status';
import { RunsTable } from '../common/RunsTable';
import { useTimeZone } from '../common/useTimeZone';

export function useActivityTabs() {
  const pending = useInputRequests();
  const count = pending.data?.length ?? 0;
  return [
    { to: '/activity/runs', label: 'Runs' },
    {
      to: '/activity/approvals',
      label: 'Crew Requests',
      badge: count > 0 ? (
        <span className="count-badge">
          {count}
          <span className="sr-only"> pending</span>
        </span>
      ) : undefined,
    },
  ];
}

export default function RunsPage() {
  const timeZone = useTimeZone();
  const tabs = useActivityTabs();
  const installations = useInstallations();
  const [state, setState] = useState<RunState | 'all'>('all');
  const [installationId, setInstallationId] = useState('all');
  const [trigger, setTrigger] = useState('all');
  const filters: RunFilters = {
    state: state === 'all' ? undefined : [state],
    installationId: installationId === 'all' ? undefined : installationId,
    trigger: trigger === 'all' ? undefined : trigger,
    limit: 100,
  };
  const runs = useRuns(filters);
  const ids = { state: useId(), agent: useId(), trigger: useId() };

  return (
    <Page>
      <PageHeader title="Activity" purpose="Runs created manually or by schedules, and Crew Requests waiting for you." />
      <RouteTabs tabs={tabs} label="Activity views" />
      <div className="row" style={{ alignItems: 'flex-end' }}>
        <div className="field" style={{ minWidth: 180 }}>
          <label className="field-label" htmlFor={ids.state}>
            Status
          </label>
          <select id={ids.state} className="select" value={state} onChange={(e) => setState(e.target.value as RunState | 'all')}>
            <option value="all">All</option>
            {RUN_STATES.map((s) => (
              <option key={s} value={s}>
                {RUN_STATUS[s].label}
              </option>
            ))}
          </select>
        </div>
        <div className="field" style={{ minWidth: 180 }}>
          <label className="field-label" htmlFor={ids.agent}>
            Agent
          </label>
          <select id={ids.agent} className="select" value={installationId} onChange={(e) => setInstallationId(e.target.value)}>
            <option value="all">All agents</option>
            {(installations.data ?? []).map((i) => (
              <option key={i.id} value={i.id}>
                {i.agentName}
              </option>
            ))}
          </select>
        </div>
        <div className="field" style={{ minWidth: 160 }}>
          <label className="field-label" htmlFor={ids.trigger}>
            Trigger
          </label>
          <select id={ids.trigger} className="select" value={trigger} onChange={(e) => setTrigger(e.target.value)}>
            <option value="all">Any</option>
            <option value="manual">Manual</option>
            <option value="schedule">Scheduled</option>
          </select>
        </div>
      </div>
      <QueryView
        query={runs}
        errorTitle="Could not load runs"
        loading={<SkeletonTable label="Loading runs" />}
        isEmpty={(d) => d.items.length === 0}
        empty={
          <EmptyState
            icon={Activity}
            title={state === 'all' && installationId === 'all' && trigger === 'all' ? 'No runs yet' : 'No runs match'}
            action={
              <ButtonLink to="/agents/installed" variant="secondary">
                Run a crew member
              </ButtonLink>
            }
          >
            Runs appear here when you start an agent or a schedule fires.
          </EmptyState>
        }
      >
        {(d) => <RunsTable runs={d.items} caption="Runs, newest first" timeZone={timeZone} />}
      </QueryView>
    </Page>
  );
}
