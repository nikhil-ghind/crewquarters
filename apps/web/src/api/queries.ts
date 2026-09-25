/**
 * TanStack Query hooks: the only owner of server state in the UI (section 13.19).
 * Query keys live here so invalidation stays consistent across pages.
 */
import { keepPreviousData, useQuery, type UseQueryOptions } from '@tanstack/react-query';
import { api, unwrap } from './client';
import { pendingApi } from './pending';
import {
  isTerminal,
  type AttentionOut,
  type AuditEventOut,
  type CatalogAgentOut,
  type ChatSessionDetailOut,
  type ChatSessionOut,
  type InputRequestOut,
  type InstallationOut,
  type MemoryOut,
  type ModelOut,
  type RunOut,
  type RunState,
  type ScheduleOut,
  type SystemStatusOut,
} from './schema';
import type { ConnectionOut, SettingsOut } from './pending-contracts';

export const keys = {
  me: ['me'] as const,
  settings: ['settings'] as const,
  attention: ['attention'] as const,
  systemStatus: ['system', 'status'] as const,
  memory: ['system', 'memory'] as const,
  health: ['health'] as const,
  catalog: ['catalog'] as const,
  catalogAgent: (id: string) => ['catalog', id] as const,
  installations: ['installations'] as const,
  installation: (id: string) => ['installations', id] as const,
  runs: (filters: RunFilters = {}) => ['runs', filters] as const,
  runsAll: ['runs'] as const,
  run: (id: string) => ['run', id] as const,
  runEvents: (id: string) => ['run', id, 'events'] as const,
  inputRequests: (filters: { state?: string; runId?: string } = {}) => ['inputRequests', filters] as const,
  inputRequestsAll: ['inputRequests'] as const,
  schedules: ['schedules'] as const,
  models: ['models'] as const,
  model: (id: string) => ['models', id] as const,
  connections: ['connections'] as const,
  audit: (filters: AuditFilters) => ['audit', filters] as const,
  chatSessions: ['chat', 'sessions'] as const,
  chatSession: (id: string) => ['chat', 'sessions', id] as const,
  knowledgeBases: ['knowledge'] as const,
  knowledgeBase: (id: string) => ['knowledge', id] as const,
  documents: (kbId: string) => ['knowledge', kbId, 'documents'] as const,
  providerProfiles: ['providerProfiles'] as const,
};

export interface RunFilters {
  installationId?: string;
  state?: RunState[];
  trigger?: string;
  limit?: number;
}

export interface AuditFilters {
  action?: string;
  outcome?: 'success' | 'denied' | 'failure';
  since?: string;
  cursor?: string;
}

type Opts<T> = Omit<UseQueryOptions<T, Error, T, readonly unknown[]>, 'queryKey' | 'queryFn'>;

/** Follow nextCursor to collect a small list (catalog, installations, models). */
async function all<T>(
  fetchPage: (cursor: string | undefined) => Promise<{ items: T[]; nextCursor?: string | null }>,
  maxPages = 10,
): Promise<T[]> {
  const out: T[] = [];
  let cursor: string | undefined;
  for (let i = 0; i < maxPages; i++) {
    const page = await fetchPage(cursor);
    out.push(...page.items);
    if (!page.nextCursor) break;
    cursor = page.nextCursor;
  }
  return out;
}

// --- Platform ---------------------------------------------------------------------

export function useSettings(opts?: Opts<SettingsOut>) {
  return useQuery({
    queryKey: keys.settings,
    queryFn: (): Promise<SettingsOut> => unwrap(api.GET('/api/v1/settings')),
    staleTime: 30_000,
    ...opts,
  });
}

export function useAttention(opts?: Opts<AttentionOut>) {
  return useQuery({
    queryKey: keys.attention,
    queryFn: () => unwrap(api.GET('/api/v1/attention')),
    refetchInterval: 10_000,
    ...opts,
  });
}

export function useSystemStatus(opts?: Opts<SystemStatusOut>) {
  return useQuery({
    queryKey: keys.systemStatus,
    queryFn: () => unwrap(api.GET('/api/v1/system/status')),
    refetchInterval: 30_000,
    ...opts,
  });
}

export function useMemory(opts?: Opts<MemoryOut>) {
  return useQuery({
    queryKey: keys.memory,
    queryFn: () => unwrap(api.GET('/api/v1/system/memory')),
    refetchInterval: 10_000,
    ...opts,
  });
}

export function useAudit(filters: AuditFilters) {
  return useQuery({
    queryKey: keys.audit(filters),
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/audit-events', {
          params: {
            query: {
              action: filters.action || undefined,
              outcome: filters.outcome,
              since: filters.since,
              cursor: filters.cursor,
              limit: 50,
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
  });
}

export type AuditPage = { items: AuditEventOut[]; nextCursor?: string | null };

// --- Agents -------------------------------------------------------------------------

export function useCatalog(opts?: Opts<CatalogAgentOut[]>) {
  return useQuery({
    queryKey: keys.catalog,
    queryFn: () =>
      all((cursor) => unwrap(api.GET('/api/v1/catalog/agents', { params: { query: { cursor, limit: 200 } } }))),
    staleTime: 60_000,
    ...opts,
  });
}

export function useCatalogAgent(agentId: string) {
  return useQuery({
    queryKey: keys.catalogAgent(agentId),
    queryFn: () =>
      unwrap(api.GET('/api/v1/catalog/agents/{agent_id}', { params: { path: { agent_id: agentId } } })),
    staleTime: 60_000,
  });
}

export function useCatalogVersion(agentId: string, version: string) {
  return useQuery({
    queryKey: [...keys.catalogAgent(agentId), version] as const,
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/catalog/agents/{agent_id}/versions/{version}', {
          params: { path: { agent_id: agentId, version } },
        }),
      ),
    staleTime: Infinity,
  });
}

export function useInstallations(opts?: Opts<InstallationOut[]>) {
  return useQuery({
    queryKey: keys.installations,
    queryFn: () =>
      all((cursor) =>
        unwrap(api.GET('/api/v1/agent-installations', { params: { query: { cursor, limit: 200 } } })),
      ),
    ...opts,
  });
}

export function useInstallation(id: string) {
  return useQuery({
    queryKey: keys.installation(id),
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/agent-installations/{installation_id}', {
          params: { path: { installation_id: id } },
        }),
      ),
  });
}

// --- Runs ----------------------------------------------------------------------------

export function useRuns(filters: RunFilters = {}, opts?: Opts<{ items: RunOut[]; nextCursor?: string | null }>) {
  return useQuery({
    queryKey: keys.runs(filters),
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/runs', {
          params: {
            query: {
              installationId: filters.installationId,
              state: filters.state,
              trigger: filters.trigger,
              limit: filters.limit ?? 50,
            },
          },
        }),
      ),
    // Active runs change quickly; the run detail page uses SSE instead.
    refetchInterval: (query) =>
      query.state.data?.items.some((r) => !isTerminal(r.state)) ? 5_000 : 30_000,
    placeholderData: keepPreviousData,
    ...opts,
  });
}

export function useRun(id: string) {
  return useQuery({
    queryKey: keys.run(id),
    queryFn: () => unwrap(api.GET('/api/v1/runs/{run_id}', { params: { path: { run_id: id } } })),
  });
}

export function useInputRequests(filters: { state?: string; runId?: string } = {}, opts?: Opts<InputRequestOut[]>) {
  return useQuery({
    queryKey: keys.inputRequests(filters),
    queryFn: async () =>
      (
        await unwrap(
          api.GET('/api/v1/input-requests', {
            params: { query: { state: filters.state ?? 'pending', runId: filters.runId, limit: 100 } },
          }),
        )
      ).items,
    refetchInterval: 10_000,
    ...opts,
  });
}

// --- Schedules ----------------------------------------------------------------------

export function useSchedules(opts?: Opts<ScheduleOut[]>) {
  return useQuery({
    queryKey: keys.schedules,
    queryFn: () =>
      all((cursor) => unwrap(api.GET('/api/v1/schedules', { params: { query: { cursor, limit: 200 } } }))),
    refetchInterval: 60_000,
    ...opts,
  });
}

// --- Models --------------------------------------------------------------------------

const MODEL_BUSY = (m: ModelOut) =>
  m.downloadState === 'DOWNLOADING' ||
  m.downloadState === 'DELETING' ||
  m.memoryState === 'LOADING' ||
  m.memoryState === 'DRAINING';

export function useModels(opts?: Opts<ModelOut[]>) {
  return useQuery({
    queryKey: keys.models,
    queryFn: () =>
      all((cursor) => unwrap(api.GET('/api/v1/models', { params: { query: { cursor, limit: 200 } } }))),
    refetchInterval: (query) => (query.state.data?.some(MODEL_BUSY) ? 3_000 : 30_000),
    ...opts,
  });
}

export function useModel(id: string) {
  return useQuery({
    queryKey: keys.model(id),
    queryFn: () => unwrap(api.GET('/api/v1/models/{model_id}', { params: { path: { model_id: id } } })),
    refetchInterval: (query) => (query.state.data && MODEL_BUSY(query.state.data) ? 5_000 : 30_000),
  });
}

export function isModelBusy(m: ModelOut): boolean {
  return MODEL_BUSY(m);
}

// --- Connections ------------------------------------------------------------------------

export function useConnections(opts?: Opts<ConnectionOut[]>) {
  return useQuery({
    queryKey: keys.connections,
    queryFn: (): Promise<ConnectionOut[]> =>
      all((cursor) => unwrap(api.GET('/api/v1/connections', { params: { query: { cursor, limit: 50 } } }))),
    staleTime: 15_000,
    ...opts,
  });
}

export function useProviderProfiles() {
  return useQuery({
    queryKey: keys.providerProfiles,
    queryFn: async () => (await pendingApi.providerProfiles()).items,
    retry: false,
  });
}

// --- Chat --------------------------------------------------------------------------------

export function useChatSessions(opts?: Opts<ChatSessionOut[]>) {
  return useQuery({
    queryKey: keys.chatSessions,
    queryFn: () =>
      all((cursor) => unwrap(api.GET('/api/v1/chat/sessions', { params: { query: { cursor, limit: 50 } } })), 2),
    ...opts,
  });
}

export function useChatSession(id: string, opts?: Opts<ChatSessionDetailOut>) {
  return useQuery({
    queryKey: keys.chatSession(id),
    queryFn: () =>
      unwrap(api.GET('/api/v1/chat/sessions/{session_id}', { params: { path: { session_id: id } } })),
    ...opts,
  });
}

// --- Knowledge (pending contracts) ---------------------------------------------------------

export function useKnowledgeBases() {
  return useQuery({
    queryKey: keys.knowledgeBases,
    queryFn: async () => (await pendingApi.knowledgeBases()).items,
    retry: false,
  });
}

export function useKnowledgeBase(id: string) {
  return useQuery({
    queryKey: keys.knowledgeBase(id),
    queryFn: () => pendingApi.knowledgeBase(id),
    retry: false,
  });
}

export function useDocuments(kbId: string) {
  return useQuery({
    queryKey: keys.documents(kbId),
    queryFn: async () => (await pendingApi.documents(kbId)).items,
    refetchInterval: (query) =>
      query.state.data?.some((d) => d.state === 'PENDING' || d.state === 'PROCESSING') ? 2_000 : 30_000,
  });
}
