import { ArrowUpRight, ShieldCheck } from 'lucide-react';
import type { CatalogAgentOut, ConnectionOut, InstallationOut } from '../../api/schema';
import { StatusBadge } from '../../components/StatusBadge';
import { AGENT_STATUS, CONNECTION_STATUS, PROVIDER_NAMES, READINESS_NAMES, READINESS_STATUS } from '../../lib/status';
import { usesCloud } from '../../lib/permissions';
import { requiredConnectionsText } from './install';

export function TrustBadge({ agent }: { agent: CatalogAgentOut }) {
  return agent.trustStatus === 'curated' ? (
    <span className="badge tone-success">
      <ShieldCheck size={14} aria-hidden="true" />
      Curated
    </span>
  ) : (
    <span className="badge tone-warning">Imported · not reviewed</span>
  );
}

/** `Uses cloud` is a visible label, never hidden in a detail page (section 13.6). */
export function CloudUseBadge({ permissions }: { permissions: Record<string, unknown> }) {
  const providers = usesCloud(permissions);
  if (providers.length === 0) return null;
  return (
    <span className="badge chip-cloud">
      <ArrowUpRight size={14} aria-hidden="true" />
      Uses cloud · {providers.map((p) => PROVIDER_NAMES[p] ?? p).join(', ')}
    </span>
  );
}

export function RequirementBadges({
  agent,
  connections,
}: {
  agent: CatalogAgentOut;
  connections?: ConnectionOut[];
}) {
  const required = requiredConnectionsText(agent.latest);
  if (required.length === 0) return <span className="muted">No connections needed</span>;
  return (
    <span className="row">
      {required.map((provider) => {
        const c = connections?.find((x) => x.provider === provider);
        const status = c ? CONNECTION_STATUS[c.status] : CONNECTION_STATUS.NOT_CONNECTED;
        return (
          <span key={provider} className="row" style={{ gap: 4 }}>
            <span>{PROVIDER_NAMES[provider] ?? provider}:</span>
            <StatusBadge status={status} />
          </span>
        );
      })}
    </span>
  );
}

export function installationStatus(inst: InstallationOut) {
  if (inst.needsReapproval) return AGENT_STATUS.reapproval;
  if (!inst.enabled) return AGENT_STATUS.disabled;
  return inst.readiness.ready ? AGENT_STATUS.ready : AGENT_STATUS.notReady;
}

/** Readiness checklist: model, connections, permissions, configuration. */
export function ReadinessList({ installation }: { installation: InstallationOut }) {
  return (
    <ul className="stack-sm" style={{ listStyle: 'none' }} aria-label="Readiness">
      {installation.readiness.checks.map((check) => (
        <li key={`${check.name}-${check.resource ?? ''}`} className="row-between" style={{ flexWrap: 'nowrap', alignItems: 'flex-start' }}>
          <span className="stack-sm" style={{ gap: 0 }}>
            <span className="field-label">
              {READINESS_NAMES[check.name]}
              {check.resource ? ` · ${check.resource}` : ''}
            </span>
            <span className="muted">{check.detail}</span>
          </span>
          <StatusBadge status={READINESS_STATUS[check.status]} context={READINESS_NAMES[check.name]} />
        </li>
      ))}
    </ul>
  );
}

export function triggerText(triggers: string[]): string {
  return triggers.map((t) => (t === 'manual' ? 'Run on demand' : t === 'schedule' ? 'Scheduled' : t === 'agent' ? 'Started by another agent' : t)).join(' · ');
}
