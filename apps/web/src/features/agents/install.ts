import type { AgentVersionOut, ModelOut } from '../../api/schema';
import { asSchema, defaultsFor, fieldGroups, type JsonSchema } from '../../lib/jsonSchema';
import { permissionItems } from '../../lib/permissions';

export function configSchema(version: AgentVersionOut): JsonSchema {
  return asSchema(version.configurationSchema);
}

/** Schema defaults, with timezone fields defaulting to the owner's timezone. */
export function initialConfig(version: AgentVersionOut, timeZone: string): Record<string, unknown> {
  const schema = configSchema(version);
  const config = defaultsFor(schema);
  for (const { fields } of fieldGroups(schema)) {
    for (const field of fields) {
      if (field.widget === 'timezone' && (config[field.name] === undefined || field.required)) {
        config[field.name] = timeZone;
      }
    }
  }
  return config;
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [];
}

/** Requested model profile families, e.g. ["local.general"]. */
export function profileFamilies(version: AgentVersionOut): string[] {
  return strings((version.permissions as { llmProfiles?: unknown }).llmProfiles);
}

/** Variants of a family the owner can bind, e.g. local.general → local.general.small. */
export function variantsFor(family: string, models: ModelOut[] | undefined): ModelOut[] {
  return (models ?? []).filter((m) => m.id === family || m.id.startsWith(`${family}.`));
}

export function defaultBindings(version: AgentVersionOut, models: ModelOut[] | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  for (const family of profileFamilies(version)) {
    const variants = variantsFor(family, models);
    const pick =
      variants.find((m) => m.downloadState === 'INSTALLED') ??
      variants.find((m) => m.id.endsWith('.small')) ??
      variants[0];
    if (pick) out[family] = pick.id;
  }
  return out;
}

export function allApproved(version: AgentVersionOut, approvals: Record<string, boolean>): boolean {
  return permissionItems(version.permissions).every((item) => approvals[item.id] === true);
}

export function requiredConnectionsText(version: AgentVersionOut): string[] {
  const connectors = (version.permissions as { connectors?: Record<string, unknown> }).connectors ?? {};
  return Object.keys(connectors);
}
