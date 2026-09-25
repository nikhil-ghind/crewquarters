import { Archive } from 'lucide-react';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/Feedback';
import { Advanced, Card, Page, PageHeader } from '../../components/Layout';
import { SystemTabs } from './SystemTabs';

/**
 * Backups (section 13.13). The control API has no backup endpoints yet, so this page
 * explains what a backup contains and keeps the actions visibly disabled with a reason.
 * TODO(contracts): wire to the backup API when it is published.
 */
export default function BackupsPage() {
  return (
    <Page>
      <PageHeader
        title="Backups"
        purpose="Back up settings, agents, schedules, history, documents and connection records."
        actions={
          <Button variant="primary" disabledReason="Backups are created from the device console in this version (crewquarters backup).">
            Create backup
          </Button>
        }
      />
      <SystemTabs />
      <EmptyState icon={Archive} title="No backups listed">
        Backups made on the device are not listed here yet. Model files are not included; they can be downloaded again.
      </EmptyState>
      <Card title="What a backup contains">
        <ul style={{ marginLeft: 20 }} className="long-form">
          <li>The database: settings, agents, schedules, runs, Crew Requests, chat history and audit history.</li>
          <li>Uploaded knowledge documents.</li>
          <li>Connection records with their secrets still encrypted. The key that decrypts them is not included by default.</li>
        </ul>
        <p className="muted" style={{ marginTop: 8 }}>
          Including secret recovery material requires your password and a separate confirmation, because anyone with that
          backup could use your connected accounts.
        </p>
      </Card>
      <Advanced label="Restore">
        <p>Restoring replaces the current data. A backup of the current state is taken first.</p>
        <Button disabledReason="Restore runs from the device console in this version.">Restore from backup…</Button>
      </Advanced>
    </Page>
  );
}
