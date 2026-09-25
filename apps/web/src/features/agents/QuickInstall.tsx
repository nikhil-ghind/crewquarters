import { useMemo, useState } from 'react';
import { Link } from 'react-router';
import { isApiError, type FieldError } from '../../api/errors';
import { useActionGuard } from '../../api/guards';
import { useInstallAgent, useIntentKey } from '../../api/mutations';
import { useConnections, useModels } from '../../api/queries';
import type { CatalogAgentOut, InstallationOut } from '../../api/schema';
import { Button } from '../../components/Button';
import { Banner, ErrorPanel } from '../../components/Feedback';
import { ErrorSummary } from '../../components/Field';
import { Card } from '../../components/Layout';
import { PermissionList } from '../../components/PermissionRow';
import { SchemaForm } from '../../components/SchemaForm';
import { StatusBadge } from '../../components/StatusBadge';
import { fieldNameFromPath, validate } from '../../lib/jsonSchema';
import { permissionItems } from '../../lib/permissions';
import { useTimeZone } from '../common/useTimeZone';
import { CloudUseBadge, installationStatus, ReadinessList, RequirementBadges, TrustBadge } from './AgentBits';
import { allApproved, configSchema, defaultBindings, initialConfig } from './install';
import { useFormOptions } from './useFormOptions';

/**
 * Compact install used by the setup wizard: requirements, every permission with its own
 * approval, the generated configuration form, then Install agent.
 */
export function QuickInstall({ agent, installation }: { agent: CatalogAgentOut; installation?: InstallationOut }) {
  const version = agent.latest;
  const timeZone = useTimeZone();
  const connections = useConnections();
  const models = useModels();
  const options = useFormOptions();
  const guard = useActionGuard();
  const install = useInstallAgent();
  const [key, resetKey] = useIntentKey();
  const [selected, setSelected] = useState(false);
  const [config, setConfig] = useState(() => initialConfig(version, timeZone));
  const [approvals, setApprovals] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<FieldError[]>([]);
  const schema = useMemo(() => configSchema(version), [version]);
  const items = useMemo(() => permissionItems(version.permissions), [version.permissions]);

  if (installation) {
    return (
      <Card title={agent.name} subtitle={agent.summary} actions={<StatusBadge status={installationStatus(installation)} />}>
        <div className="stack">
          <p>In your crew.</p>
          <ReadinessList installation={installation} />
          <Link to={`/agents/${encodeURIComponent(installation.id)}`}>Open {agent.name}</Link>
        </div>
      </Card>
    );
  }

  const submit = () => {
    const local = validate(schema, config);
    setErrors(local);
    if (local.length > 0) return;
    install.mutate(
      {
        key,
        body: {
          agentId: agent.agentId,
          version: version.version,
          config,
          approvedPermissions: version.permissions,
          modelBindings: defaultBindings(version, models.data),
          enabled: true,
        },
      },
      {
        onSuccess: resetKey,
        onError: (e) => {
          if (isApiError(e)) {
            setErrors(e.fieldErrors.map((f) => ({ path: `/${fieldNameFromPath(f.path)}`, message: f.message })));
          }
        },
      },
    );
  };

  return (
    <Card
      title={agent.name}
      subtitle={agent.summary}
      actions={
        <>
          <TrustBadge agent={agent} />
          <CloudUseBadge permissions={version.permissions} />
        </>
      }
    >
      <div className="stack">
        <div className="row">
          <span className="field-label">Requirements:</span>
          <RequirementBadges agent={agent} connections={connections.data} />
        </div>
        <label className="check-row">
          <input type="checkbox" checked={selected} onChange={(e) => setSelected(e.target.checked)} />
          <span>Add {agent.name} to my crew</span>
        </label>
        {selected ? (
          <>
            <h3>Review permissions</h3>
            <PermissionList
              items={items}
              approvals={approvals}
              onApprove={(id, v) => setApprovals((a) => ({ ...a, [id]: v }))}
            />
            <h3>Configure</h3>
            <ErrorSummary errors={errors} />
            <SchemaForm
              schema={schema}
              value={config}
              onChange={setConfig}
              errors={errors}
              options={options}
              idPrefix={`quick-${agent.agentId}`}
            />
            {install.isError ? (
              <Banner tone="danger" role="alert" title="Installation incomplete">
                {agent.name} was not added. Your choices are kept; fix the problem and try again.
              </Banner>
            ) : null}
            {install.isError && !(isApiError(install.error) && install.error.fieldErrors.length > 0) ? (
              <ErrorPanel error={install.error} title="Why it failed" />
            ) : null}
            <div className="row">
              <Button
                variant="primary"
                busy={install.isPending}
                busyLabel="Installing…"
                disabledReason={
                  guard.offline ?? (allApproved(version, approvals) ? null : 'Approve each permission to install.')
                }
                onClick={submit}
              >
                Install {agent.name}
              </Button>
            </div>
          </>
        ) : null}
      </div>
    </Card>
  );
}
