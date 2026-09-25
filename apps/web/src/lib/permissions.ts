/**
 * Presents a manifest permission block (`spec.permissions`) as plain-language rows,
 * using the UI copy from packages/contracts/capabilities.yaml. This is display only:
 * the control API decides whether the approval matches the requested permissions and
 * whether a version change needs reapproval.
 */
import { PROVIDER_NAMES } from './status';

export type PermissionGroup = 'Local data' | 'External services' | 'Model use' | 'User interaction';

export const PERMISSION_GROUPS: PermissionGroup[] = [
  'Local data',
  'External services',
  'Model use',
  'User interaction',
];

export interface PermissionItem {
  id: string;
  group: PermissionGroup;
  capability: string;
  impact: string;
  resource?: string;
  /** Cloud and phone permissions get stronger treatment (section 13.6). */
  emphasis?: 'cloud' | 'phone';
}

interface Permissions {
  llmProfiles?: unknown;
  knowledge?: unknown;
  connectors?: unknown;
  cloudProviders?: unknown;
  userInput?: unknown;
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [];
}

const CONNECTOR_COPY: Record<string, { capability: string; impact: string; resource?: string; emphasis?: 'phone' }> = {
  'google.gmail.readonly': {
    capability: 'Read your Gmail messages',
    impact: 'The agent can read message headers and bodies from your connected Google account. It cannot send, change or delete mail.',
  },
  'google.spreadsheets': {
    capability: 'Read and write Google Sheets you configure',
    impact: 'Only the spreadsheet named in this agent’s configuration.',
    resource: 'Spreadsheet in configuration',
  },
  'twilio.call.fixed_script': {
    capability: 'Place phone calls with a fixed script',
    impact:
      'Real phone calls are placed through your Twilio account to consenting recipients, using only the script and call cap in the configuration. You approve each batch before it starts.',
    resource: 'Script and call cap in configuration',
    emphasis: 'phone',
  },
};

export function permissionItems(raw: Record<string, unknown> | null | undefined): PermissionItem[] {
  const p = (raw ?? {}) as Permissions;
  const items: PermissionItem[] = [];

  if (strings(p.knowledge).length > 0) {
    items.push({
      id: 'knowledge.search:config',
      group: 'Local data',
      capability: 'Search the knowledge base you select',
      impact: 'Passages from the selected knowledge base are read on this device.',
      resource: 'Knowledge base in configuration',
    });
  }

  const connectors = (typeof p.connectors === 'object' && p.connectors !== null ? p.connectors : {}) as Record<string, unknown>;
  for (const [provider, scopes] of Object.entries(connectors)) {
    for (const scope of strings(scopes)) {
      const key = `${provider}.${scope}`;
      const copy = CONNECTOR_COPY[key];
      items.push({
        id: key,
        group: 'External services',
        capability: copy?.capability ?? `${PROVIDER_NAMES[provider] ?? provider}: ${scope}`,
        impact: copy?.impact ?? `Uses your ${PROVIDER_NAMES[provider] ?? provider} connection (${scope}).`,
        resource: copy?.resource ?? `${PROVIDER_NAMES[provider] ?? provider} connection`,
        emphasis: copy?.emphasis,
      });
    }
  }

  for (const profile of strings(p.llmProfiles)) {
    items.push({
      id: `llm.profile:${profile}`,
      group: 'Model use',
      capability: `Use the ${profile} model`,
      impact: 'Runs on a local model on this device; data stays on the device.',
      resource: 'Local model',
    });
  }
  for (const provider of strings(p.cloudProviders)) {
    const name = PROVIDER_NAMES[provider] ?? provider;
    items.push({
      id: `cloud.${provider}`,
      group: 'Model use',
      capability: `Send data to ${name}`,
      impact: `Prompts and the data the agent includes in them leave this device and are processed by ${name} under your API key. This is never an automatic fallback for local models.`,
      resource: `Cloud · ${name}`,
      emphasis: 'cloud',
    });
  }

  if (p.userInput === true) {
    items.push({
      id: 'user_input',
      group: 'User interaction',
      capability: 'Ask you questions and wait for answers',
      impact: 'Runs can pause and show a Crew Request until you answer.',
    });
  }
  return items;
}

export function groupPermissions(items: PermissionItem[]): { group: PermissionGroup; items: PermissionItem[] }[] {
  return PERMISSION_GROUPS.map((group) => ({ group, items: items.filter((i) => i.group === group) })).filter(
    (g) => g.items.length > 0,
  );
}

/** Ids that are requested now but were not approved before (for highlighting only). */
export function addedPermissionIds(
  requested: Record<string, unknown> | null | undefined,
  approved: Record<string, unknown> | null | undefined,
): Set<string> {
  const before = new Set(permissionItems(approved).map((i) => i.id));
  return new Set(permissionItems(requested).map((i) => i.id).filter((id) => !before.has(id)));
}

export function usesCloud(raw: Record<string, unknown> | null | undefined): string[] {
  return strings((raw as Permissions | undefined)?.cloudProviders);
}

export function requiredConnectors(raw: Record<string, unknown> | null | undefined): string[] {
  const connectors = (raw as Permissions | undefined)?.connectors;
  return typeof connectors === 'object' && connectors !== null ? Object.keys(connectors) : [];
}
