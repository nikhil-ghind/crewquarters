import { Archive, Download, Plus } from 'lucide-react';
import type { ReactNode } from 'react';
import { backupDownloadUrl } from '../../api/endpoints';
import { useCreateBackup, useIntentKey } from '../../api/mutations';
import { isBackupActive, useBackups } from '../../api/queries';
import type { BackupOut, BackupPage } from '../../api/schema';
import { Button, DownloadLink } from '../../components/Button';
import { DataTable, type Column } from '../../components/DataTable';
import { Banner, CopyButton, EmptyState, ErrorPanel, SkeletonTable } from '../../components/Feedback';
import { Advanced, Card, KeyValue, Page, PageHeader } from '../../components/Layout';
import { QueryView } from '../../components/QueryView';
import { StatusBadge } from '../../components/StatusBadge';
import { useFeedback } from '../../components/Toast';
import { formatBytes, formatDateTime, formatRelative, formatUtc, plural } from '../../lib/format';
import { BACKUP_STATUS } from '../../lib/status';
import { useTimeZone } from '../common/useTimeZone';
import { SystemTabs } from './SystemTabs';

/** Where the installer keeps backups on the device (host path). */
const HOST_BACKUP_DIR = '/var/lib/crewquarters/backups';

const SOURCE_LABEL: Record<BackupOut['source'], string> = {
  api: 'Made from this page',
  device: 'Made on the device',
};

/** The API's include/exclude lines mark commands with backticks; show those as code. */
function withCode(text: string): ReactNode {
  return text.split('`').map((part, i) => (i % 2 === 1 ? <code key={i}>{part}</code> : part));
}

function createBlockedReason(data: BackupPage | undefined): string | null {
  if (!data) return 'Loading backups…';
  if (!data.enabled) return 'Backups are not configured on this device (CQ_BACKUP_DIR).';
  if (data.items.some(isBackupActive)) return 'A backup is already in progress.';
  return null;
}

function DownloadCell({ backup }: { backup: BackupOut }) {
  if (backup.downloadable) {
    return (
      <DownloadLink variant="tertiary" href={backupDownloadUrl(backup.id)} icon={<Download size={16} aria-hidden="true" />}>
        Download<span className="sr-only"> {backup.id}</span>
      </DownloadLink>
    );
  }
  if (backup.includesMasterKey) return <span className="muted">Contains the master key; copy it on the device</span>;
  if (isBackupActive(backup)) return <span className="muted">Available when finished</span>;
  return <span className="muted">—</span>;
}

/**
 * Backups (section 13.13). Creating and downloading happen here; restore replaces the
 * whole database with the platform stopped, so it is a device command only.
 */
export default function BackupsPage() {
  const timeZone = useTimeZone();
  const backups = useBackups();
  const create = useCreateBackup();
  const [key, resetKey] = useIntentKey();
  const { toast } = useFeedback();
  const blocked = createBlockedReason(backups.data);

  const onCreate = () =>
    create.mutate(
      { key },
      {
        onSuccess: () => {
          resetKey();
          toast('Backup queued. It runs in the background.');
        },
        onError: () => resetKey(),
      },
    );

  const columns: Column<BackupOut>[] = [
    {
      key: 'created',
      header: 'Created',
      primary: true,
      sortValue: (b) => b.createdAt,
      cell: (b) => (
        <span className="stack-sm" style={{ gap: 2 }}>
          <span title={formatUtc(b.createdAt)}>{b.createdAt ? formatDateTime(b.createdAt, timeZone) : '—'}</span>
          <span className="mono muted break-anywhere">{b.id}</span>
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      cell: (b) => (
        <span className="stack-sm" style={{ gap: 2 }}>
          <StatusBadge status={BACKUP_STATUS[b.status]} context="Backup" />
          {b.error ? <span className="field-error break-anywhere">{b.error.message}</span> : null}
        </span>
      ),
    },
    { key: 'source', header: 'Source', cell: (b) => SOURCE_LABEL[b.source] },
    { key: 'size', header: 'Size', sortValue: (b) => b.sizeBytes, cell: (b) => (b.sizeBytes === null ? '—' : formatBytes(b.sizeBytes)) },
    {
      key: 'contents',
      header: 'Contents',
      cell: (b) => (
        <span className="muted">
          {b.documentCount === null ? '—' : plural(b.documentCount, 'document')}
          {b.includesMasterKey ? ' · includes master key' : ''}
        </span>
      ),
    },
    { key: 'download', header: 'Download', cell: (b) => <DownloadCell backup={b} /> },
  ];

  return (
    <Page>
      <PageHeader
        title="Backups"
        purpose="Back up settings, agents, schedules, history, documents and connection records."
        actions={
          <Button
            variant="primary"
            icon={<Plus size={16} aria-hidden="true" />}
            busy={create.isPending}
            busyLabel="Queuing…"
            disabledReason={backups.isError && !backups.data ? 'Backups could not be loaded.' : blocked}
            onClick={onCreate}
          >
            Create backup
          </Button>
        }
      />
      <SystemTabs />
      {create.isError ? <ErrorPanel error={create.error} title="Could not create a backup" /> : null}
      <QueryView query={backups} errorTitle="Could not load backups" loading={<SkeletonTable label="Loading backups" />}>
        {(data) => {
          const last = data.items.find((b) => b.status === 'succeeded');
          const active = data.items.find(isBackupActive);
          return (
            <>
              {!data.enabled ? (
                <Banner tone="warning" title="Backups are not configured on this device">
                  CQ_BACKUP_DIR is not set, so backups cannot be created here. Run <code>sudo crewquarters backup create</code>{' '}
                  on the device, or set a backup directory and restart.
                </Banner>
              ) : null}
              <Card title="Summary">
                <KeyValue
                  items={[
                    [
                      'Last successful backup',
                      last?.createdAt ? (
                        <span title={formatUtc(last.createdAt)}>
                          {formatRelative(last.createdAt)} · {formatDateTime(last.createdAt, timeZone)}
                          {last.sizeBytes !== null ? ` · ${formatBytes(last.sizeBytes)}` : ''}
                        </span>
                      ) : (
                        'None yet'
                      ),
                    ],
                    ['In progress', active ? <StatusBadge key="a" status={BACKUP_STATUS[active.status]} context="Backup" /> : 'Nothing'],
                    ['Location', data.location ? <span className="mono break-anywhere">{data.location}</span> : 'Not configured'],
                    ['Retention', `Newest ${plural(data.retention, 'backup')} kept; older ones are deleted`],
                  ]}
                />
              </Card>
              <Banner tone="info" title="Backups made here never include the device master key">
                Connection secrets stay encrypted in the backup. If you restore it on a device with a different master key,
                reconnect Google and Twilio and re-enter OpenAI and Anthropic keys afterwards.
              </Banner>
              <DataTable
                caption="Backups, newest first"
                columns={columns}
                rows={data.items}
                rowKey={(b) => b.id}
                empty={
                  <EmptyState icon={Archive} title="No backups yet">
                    {data.enabled ? 'Create one now; backups made on the device appear here too.' : 'Backups made on the device appear here.'}
                  </EmptyState>
                }
              />
              <Card title="What a backup contains">
                <div className="grid-2">
                  <div className="stack-sm">
                    <h3>Included</h3>
                    <ul style={{ marginLeft: 20 }} className="long-form">
                      {data.includes.map((line) => (
                        <li key={line}>{withCode(line)}</li>
                      ))}
                    </ul>
                  </div>
                  <div className="stack-sm">
                    <h3>Never included</h3>
                    <ul style={{ marginLeft: 20 }} className="long-form">
                      {data.excludes.map((line) => (
                        <li key={line}>{withCode(line)}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              </Card>
              <RestoreHelp name={last?.id} />
            </>
          );
        }}
      </QueryView>
    </Page>
  );
}

function RestoreHelp({ name }: { name: string | undefined }) {
  const file = `${HOST_BACKUP_DIR}/${name ?? '<file>'}.tar.gz`;
  const commands = `sudo crewquarters backup restore ${file} --stop\nsudo crewquarters up`;
  return (
    <Advanced label="Restore">
      <p>
        Restore replaces all current data and needs the platform stopped, so it runs on the device, not from this page.
        Run these in a terminal on the device{name ? ' (shown for the latest successful backup)' : ''}:
      </p>
      <pre className="raw-json mono" aria-label="Restore commands" tabIndex={0}>
        {commands}
      </pre>
      <div className="row">
        <CopyButton text={commands} label="Copy commands" />
      </div>
      <ul style={{ marginLeft: 20 }} className="long-form">
        <li>A backup of the current state is taken automatically before anything is replaced.</li>
        <li>Checksums and database schema compatibility are verified first; a mismatch stops the restore.</li>
        <li>
          If the device master key differs from the one used when the backup was made, reconnect Google and Twilio and re-enter
          OpenAI and Anthropic keys afterwards.
        </li>
      </ul>
    </Advanced>
  );
}
