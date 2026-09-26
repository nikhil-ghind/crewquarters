import { useParams } from 'react-router';
import { useCatalogAgent, useConnections, useInstallations } from '../../api/queries';
import { Button, ButtonLink } from '../../components/Button';
import { Advanced, Card, KeyValue, Page, PageHeader } from '../../components/Layout';
import { PermissionList } from '../../components/PermissionRow';
import { QueryView } from '../../components/QueryView';
import { StatusBadge } from '../../components/StatusBadge';
import { asSchema, fieldGroups } from '../../lib/jsonSchema';
import { permissionItems } from '../../lib/permissions';
import { CloudUseBadge, RequirementBadges, TrustBadge, triggerText } from './AgentBits';
import { profileFamilies } from './install';

const EXAMPLES: Record<string, string> = {
  'crewquarters.gmail-digest/v1':
    'A morning summary of yesterday’s mail in three groups — Urgent, Important and Low priority — with a one-line reason, a suggested next step and a link to each message in Gmail.',
  'crewquarters.caller/v1':
    'A table of the people called from your sheet: consent check, call status, the transcribed reply, and whether the result was written back to the sheet.',
  'crewquarters.personal-space/v1':
    'A brief built around what you want from your knowledge base: highlights with a reason and a next step, the main themes, questions to explore next, and numbered sources from your own documents.',
};

function defaultText(value: unknown): string {
  if (value === undefined) return '—';
  if (value === null) return 'None';
  if (Array.isArray(value)) return value.length ? value.map((v) => (typeof v === 'string' ? v : JSON.stringify(v))).join(', ') : 'None';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return `${value}`;
  return JSON.stringify(value);
}

export default function AgentDetailPage() {
  const { agentId = '' } = useParams();
  const agent = useCatalogAgent(agentId);
  const connections = useConnections();
  const installations = useInstallations();

  return (
    <Page>
      <QueryView query={agent} errorTitle="Could not load this agent">
        {(a) => {
          const v = a.latest;
          const installation = installations.data?.find((i) => i.agentId === a.agentId);
          const renderer = asSchema(v.resultSchema)['x-crewquarters-renderer'];
          const fields = fieldGroups(asSchema(v.configurationSchema)).flatMap((g) => g.fields.map((f) => ({ ...f, group: g.group })));
          return (
            <>
              <PageHeader
                title={a.name}
                purpose={a.summary}
                breadcrumbs={[
                  { label: 'Crew', to: '/agents/installed' },
                  { label: 'Marketplace', to: '/agents/marketplace' },
                  { label: a.name },
                ]}
                status={
                  <>
                    <TrustBadge agent={a} />
                    <CloudUseBadge permissions={v.permissions} />
                    <span className="muted">
                      {a.publisher} · v{a.currentVersion}
                    </span>
                  </>
                }
                actions={
                  installation ? (
                    <ButtonLink to={`/agents/${encodeURIComponent(installation.id)}`} variant="primary">
                      Open in your crew
                    </ButtonLink>
                  ) : v.compatible ? (
                    <ButtonLink to={`/agents/marketplace/${encodeURIComponent(a.agentId)}/install`} variant="primary">
                      Install agent
                    </ButtonLink>
                  ) : (
                    <Button variant="primary" disabledReason={`Not compatible with this device: ${v.compatibilityIssues.join(' ')}`}>
                      Install agent
                    </Button>
                  )
                }
              />
              <Card title="What it does">
                <p className="long-form">{a.summary}</p>
                {typeof renderer === 'string' && EXAMPLES[renderer] ? (
                  <p className="muted" style={{ marginTop: 8 }}>
                    Example result: {EXAMPLES[renderer]}
                  </p>
                ) : null}
              </Card>
              <Card title="Requirements and compatibility">
                <KeyValue
                  items={[
                    ['Connections', <RequirementBadges key="c" agent={a} connections={connections.data} />],
                    ['Model', profileFamilies(v).length > 0 ? profileFamilies(v).join(', ') : 'No model needed'],
                    ['Runs', triggerText(v.triggers)],
                    [
                      'This device',
                      v.compatible ? (
                        <StatusBadge key="ok" status={{ label: 'Compatible', tone: 'success', icon: 'check' }} />
                      ) : (
                        <span key="no">
                          <StatusBadge status={{ label: 'Not compatible', tone: 'danger', icon: 'x' }} /> {v.compatibilityIssues.join(' ')}
                        </span>
                      ),
                    ],
                    ['Resources', resourcesText(v.resources)],
                  ]}
                />
              </Card>
              <Card title="Permissions" subtitle="What this agent can do once installed. You approve each one during installation.">
                <PermissionList items={permissionItems(v.permissions)} />
              </Card>
              <Card title="Configuration" subtitle="You can change these during installation and later.">
                {fields.length === 0 ? (
                  <p className="muted">No configuration.</p>
                ) : (
                  <dl className="kv">
                    {fields.map((f) => (
                      <div key={f.name} style={{ display: 'contents' }}>
                        <dt>
                          {f.label}
                          {f.required ? ' (required)' : ''}
                        </dt>
                        <dd>
                          {f.schema.description ? <span className="muted">{f.schema.description} </span> : null}
                          Default: {defaultText(f.schema.default)}
                        </dd>
                      </div>
                    ))}
                  </dl>
                )}
              </Card>
              <Advanced>
                <KeyValue
                  items={[
                    ['Version', v.version],
                    ['Publisher', a.publisher],
                    ['Image', <span key="i" className="mono break-anywhere">{v.image}</span>],
                    ['Image digest', <span key="d" className="mono break-anywhere">{v.imageDigest}</span>],
                    ['SDK protocol', v.sdkProtocol],
                    ['Architectures', v.architectures.join(', ')],
                    ['Source', a.source === 'bundled' ? 'Bundled with this device' : 'Imported'],
                  ]}
                />
              </Advanced>
            </>
          );
        }}
      </QueryView>
    </Page>
  );
}

export function resourcesText(resources: Record<string, unknown>): string {
  const parts: string[] = [];
  if (typeof resources.cpu === 'number') parts.push(`${resources.cpu} CPU`);
  if (typeof resources.memoryMb === 'number') parts.push(`${resources.memoryMb} MiB memory`);
  if (typeof resources.activeTimeoutSeconds === 'number') parts.push(`up to ${Math.round(resources.activeTimeoutSeconds / 60)} min working`);
  return parts.join(' · ') || '—';
}
