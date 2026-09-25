/**
 * Types from the committed, generated TypeScript client
 * (packages/contracts/clients/typescript/schema.d.ts, produced by `make contracts` or
 * `npm run gen:api`). Never edit the generated file; import from here instead.
 */
import type { components, operations, paths } from '@contracts/clients/typescript/schema';

export type { paths, operations };
export type Schemas = components['schemas'];

export type RunOut = Schemas['RunOut'];
export type RunState = RunOut['state'];
export type RunEventOut = Schemas['RunEventOut'];
export type InputRequestOut = Schemas['InputRequestOut'];
export type AttentionOut = Schemas['AttentionOut'];
export type AttentionItem = Schemas['AttentionItem'];
export type CatalogAgentOut = Schemas['CatalogAgentOut'];
export type AgentVersionOut = Schemas['AgentVersionOut'];
export type InstallationOut = Schemas['InstallationOut'];
export type InstallationCreateIn = Schemas['InstallationCreateIn'];
export type InstallationPatchIn = Schemas['InstallationPatchIn'];
export type ReadinessCheck = Schemas['ReadinessCheck'];
export type ScheduleOut = Schemas['ScheduleOut'];
export type ScheduleCreateIn = Schemas['ScheduleCreateIn'];
export type SchedulePatchIn = Schemas['SchedulePatchIn'];
export type OccurrenceOut = Schemas['OccurrenceOut'];
export type ModelOut = Schemas['ModelOut'];
export type ModelLeaseOut = Schemas['ModelLeaseOut'];
export type MemoryOut = Schemas['MemoryOut'];
export type ConnectionOut = Schemas['ConnectionOut'];
export type ConnectionProvider = ConnectionOut['provider'];
export type ConnectionStatus = ConnectionOut['status'];
export type SettingsOut = Schemas['SettingsOut'];
export type SettingsPatchIn = Schemas['SettingsPatchIn'];
export type SystemStatusOut = Schemas['SystemStatusOut'];
export type StatusCheck = Schemas['StatusCheck'];
export type AuditEventOut = Schemas['AuditEventOut'];
export type SessionOut = Schemas['SessionOut'];
export type UserOut = Schemas['UserOut'];
export type ChatSessionOut = Schemas['ChatSessionOut'];
export type ChatSessionDetailOut = Schemas['ChatSessionDetailOut'];
export type ChatSessionCreateIn = Schemas['ChatSessionCreateIn'];
export type ChatMessageOut = Schemas['ChatMessageOut'];
export type HealthOut = Schemas['HealthOut'];
export type ErrorResponse = Schemas['ErrorResponse'];
export type GoogleStartIn = Schemas['GoogleStartIn'];
export type GoogleCapability = GoogleStartIn['capabilities'][number];
export type GoogleStartOut = Schemas['GoogleStartOut'];
export type TwilioCredentialsIn = Schemas['TwilioCredentialsIn'];
export type TwilioTestCallIn = Schemas['TwilioTestCallIn'];
export type TwilioTestCallOut = Schemas['TwilioTestCallOut'];
export type ProviderProfileOut = Schemas['ProviderProfileOut'];
export type ProviderProfileCreateIn = Schemas['ProviderProfileCreateIn'];
export type ProviderProfileTestOut = Schemas['ProviderProfileTestOut'];
export type KnowledgeBaseOut = Schemas['KnowledgeBaseOut'];
export type KnowledgeBaseCreateIn = Schemas['KnowledgeBaseCreateIn'];
export type DocumentOut = Schemas['DocumentOut'];
export type DocumentState = DocumentOut['state'];
export type KnowledgeQueryIn = Schemas['KnowledgeQueryIn'];
export type KnowledgeQueryOut = Schemas['KnowledgeQueryOut'];
export type PassageOut = Schemas['PassageOut'];
export type CitationOut = Schemas['CitationOut'];
export type BackupOut = Schemas['BackupOut'];
export type BackupStatus = BackupOut['status'];
export type BackupPage = Schemas['BackupPage'];
export type BootstrapStatusOut = Schemas['BootstrapStatusOut'];

export const RUN_STATES = [
  'QUEUED',
  'PREPARING',
  'LOADING_MODEL',
  'RUNNING',
  'WAITING_INPUT',
  'CANCELLING',
  'SUCCEEDED',
  'FAILED',
  'CANCELLED',
  'INTERRUPTED',
] as const satisfies readonly RunState[];

export const TERMINAL_RUN_STATES: readonly RunState[] = [
  'SUCCEEDED',
  'FAILED',
  'CANCELLED',
  'INTERRUPTED',
];

export function isTerminal(state: RunState): boolean {
  return TERMINAL_RUN_STATES.includes(state);
}
