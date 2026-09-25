import { Download, RotateCw } from 'lucide-react';
import { DIAGNOSTICS_URL } from '../../api/endpoints';
import { useConnections, useMemory, useModels, useSystemStatus } from '../../api/queries';
import type { StatusCheck } from '../../api/schema';
import { Button, DownloadLink } from '../../components/Button';
import { SkeletonBlock } from '../../components/Feedback';
import { Card, KeyValue, Page, PageHeader } from '../../components/Layout';
import { ResourceMeter } from '../../components/Meters';
import { QueryView } from '../../components/QueryView';
import { StatusBadge } from '../../components/StatusBadge';
import { formatBytes, formatRelative } from '../../lib/format';
import { CONNECTION_STATUS, DEVICE_STATUS, MODEL_MEMORY_STATUS } from '../../lib/status';
import { CheckList, fromStatusCheck, type CheckRow } from '../common/CheckList';
import { CallbackUrls } from '../connections/CallbackUrls';
import { memoryUsed } from '../../shell/TopBar';
import { SystemTabs } from './SystemTabs';

const GROUPS: { id: StatusCheck['group']; label: string }[] = [
  { id: 'device', label: 'Device' },
  { id: 'runtime', label: 'Runtime' },
  { id: 'storage', label: 'Storage' },
  { id: 'database', label: 'Database' },
  { id: 'network', label: 'Network callbacks' },
  { id: 'models', label: 'Model serving' },
];

export default function StatusPage() {
  const status = useSystemStatus({ refetchInterval: 15_000 });
  const memory = useMemory();
  const models = useModels();
  const connections = useConnections();

  const used = memory.data ? memoryUsed(memory.data) : null;
  const google = connections.data?.find((c) => c.provider === 'google');
  const twilio = connections.data?.find((c) => c.provider === 'twilio');

  return (
    <Page>
      <PageHeader
        title="System status"
        purpose="Device, runtime, storage, database, callbacks and model serving checks."
        actions={
          <DownloadLink
            variant="primary"
            href={DIAGNOSTICS_URL}
            icon={<Download size={16} aria-hidden="true" />}
            title="A redacted zip built on the device: checks, versions, recent logs. No secrets."
          >
            Download diagnostics
          </DownloadLink>
        }
      />
      <SystemTabs />
      <QueryView query={status} errorTitle="Could not read system status" loading={<SkeletonBlock lines={6} label="Checking the device" />}>
        {(s) => {
          const byGroup = (g: StatusCheck['group']): CheckRow[] => s.checks.filter((c) => c.group === g).map(fromStatusCheck);
          return (
            <>
              <Card
                title="Summary"
                actions={
                  <Button variant="tertiary" icon={<RotateCw size={16} aria-hidden="true" />} onClick={() => void status.refetch()}>
                    Check again
                  </Button>
                }
              >
                <KeyValue
                  items={[
                    ['Device', <StatusBadge key="s" status={DEVICE_STATUS[s.status]} context="Device" />],
                    ['Architecture', s.architecture],
                    ['Profile', s.profile],
                    ['Version', s.version],
                    ['Last checked', s.checks[0] ? formatRelative(s.checks[0].checkedAt) : '—'],
                  ]}
                />
              </Card>
              <div className="grid-2">
                {GROUPS.map((g) => {
                  const rows = byGroup(g.id);
                  if (g.id === 'network') {
                    const extra: CheckRow[] = [google, twilio]
                      .filter((c): c is NonNullable<typeof c> => c !== undefined)
                      .map((c) => ({
                        id: `callback:${c.provider}`,
                        name: `${c.displayName} callbacks`,
                        state: c.status === 'CONNECTED' ? 'passed' : c.status === 'NOT_CONNECTED' ? 'warning' : 'failed',
                        explanation: CONNECTION_STATUS[c.status].label + (c.detail ? ` — ${c.detail}` : ''),
                        remediation: c.status === 'CONNECTED' ? undefined : 'Open Connections to connect or reconnect.',
                        checkedAt: c.lastCheckedAt ?? undefined,
                      }));
                    return (
                      <Card key={g.id} title={g.label}>
                        <div className="stack">
                          <CheckList rows={[...rows, ...extra]} label={g.label} />
                          <CallbackUrls />
                        </div>
                      </Card>
                    );
                  }
                  if (g.id === 'models') {
                    return (
                      <Card key={g.id} title={g.label}>
                        <div className="stack">
                          {rows.length > 0 ? <CheckList rows={rows} label={g.label} /> : null}
                          {used && memory.data ? (
                            <ResourceMeter
                              label="Unified memory"
                              value={used.used}
                              max={used.total}
                              valueText={`${formatBytes(used.used)} of ${formatBytes(used.total)} used · ${formatBytes(memory.data.systemReserveBytes)} system reserve`}
                              warnAt={0.85}
                              dangerAt={0.95}
                            />
                          ) : null}
                          <ul className="stack-sm" style={{ listStyle: 'none' }}>
                            {(models.data ?? [])
                              .filter((m) => m.memoryState !== 'NOT_LOADED')
                              .map((m) => (
                                <li key={m.id} className="row-between">
                                  <span>{m.displayName}</span>
                                  <StatusBadge status={MODEL_MEMORY_STATUS[m.memoryState]} context="Memory" />
                                </li>
                              ))}
                          </ul>
                          {(models.data ?? []).every((m) => m.memoryState === 'NOT_LOADED') ? <p className="muted">No active model.</p> : null}
                        </div>
                      </Card>
                    );
                  }
                  return (
                    <Card key={g.id} title={g.label}>
                      {rows.length > 0 ? <CheckList rows={rows} label={g.label} /> : <p className="muted">No checks reported.</p>}
                    </Card>
                  );
                })}
              </div>
            </>
          );
        }}
      </QueryView>
    </Page>
  );
}
