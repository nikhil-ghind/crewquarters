import { Hand } from 'lucide-react';
import { useInputRequests } from '../../api/queries';
import { EmptyState, SkeletonBlock } from '../../components/Feedback';
import { Page, PageHeader, RouteTabs } from '../../components/Layout';
import { QueryView } from '../../components/QueryView';
import { InputRequestCard } from '../common/InputRequestCard';
import { NotificationPrompt } from '../common/NotificationControls';
import { useTimeZone } from '../common/useTimeZone';
import { useActivityTabs } from './RunsPage';

export default function ApprovalsPage() {
  const requests = useInputRequests();
  const tabs = useActivityTabs();
  const timeZone = useTimeZone();
  return (
    <Page>
      <PageHeader title="Crew Requests" purpose="Questions and approvals from running agents. Each one waits until you answer or it expires." breadcrumbs={[{ label: 'Activity', to: '/activity/runs' }, { label: 'Crew Requests' }]} />
      <RouteTabs tabs={tabs} label="Activity views" />
      <QueryView
        query={requests}
        errorTitle="Could not load Crew Requests"
        loading={<SkeletonBlock label="Loading Crew Requests" />}
        isEmpty={(d) => d.length === 0}
        empty={
          <EmptyState icon={Hand} title="Nothing needs you">
            When an agent asks a question or needs approval, it appears here, on the Overview and on the run page.
          </EmptyState>
        }
      >
        {(list) => (
          <div className="stack">
            <NotificationPrompt />
            {list.map((r) => (
              <InputRequestCard key={r.id} request={r} showRunLink timeZone={timeZone} />
            ))}
          </div>
        )}
      </QueryView>
    </Page>
  );
}
