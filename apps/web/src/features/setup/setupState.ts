/**
 * First-run wizard progress lives on the server (`settings.setupState`), so a refresh
 * or the Google OAuth redirect resumes at the right step (section 13.4).
 */
import { useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';
import { isApiError } from '../../api/errors';
import { usePatchSettings } from '../../api/mutations';
import { keys } from '../../api/queries';
import type { SettingsOut } from '../../api/pending-contracts';

export const SETUP_STEPS = [
  { id: 'welcome', label: 'Welcome' },
  { id: 'preflight', label: 'System preflight' },
  { id: 'owner', label: 'Owner account' },
  { id: 'storage', label: 'Storage and network' },
  { id: 'services', label: 'Platform services' },
  { id: 'model', label: 'Local model', optional: true },
  { id: 'connections', label: 'Connections', optional: true },
  { id: 'agents', label: 'Demo agents', optional: true },
  { id: 'validation', label: 'Validation and finish' },
] as const;

export type SetupStepId = (typeof SETUP_STEPS)[number]['id'];

export interface SetupState {
  current?: SetupStepId;
  completed: SetupStepId[];
  skipped: SetupStepId[];
  acceptedWarnings: string[];
  exposure?: 'local' | 'lan';
  modelId?: string;
  agents?: string[];
}

export function isStepId(value: string | undefined): value is SetupStepId {
  return SETUP_STEPS.some((s) => s.id === value);
}

export function readSetupState(settings: SettingsOut | undefined): SetupState {
  const raw = (settings?.setupState ?? {}) as Partial<SetupState>;
  const ids = (v: unknown): SetupStepId[] =>
    Array.isArray(v) ? v.filter((x): x is SetupStepId => typeof x === 'string' && isStepId(x)) : [];
  return {
    current: isStepId(raw.current) ? raw.current : undefined,
    completed: ids(raw.completed),
    skipped: ids(raw.skipped),
    acceptedWarnings: Array.isArray(raw.acceptedWarnings) ? raw.acceptedWarnings.filter((x) => typeof x === 'string') : [],
    exposure: raw.exposure === 'lan' || raw.exposure === 'local' ? raw.exposure : undefined,
    modelId: typeof raw.modelId === 'string' ? raw.modelId : undefined,
    agents: Array.isArray(raw.agents) ? raw.agents.filter((x) => typeof x === 'string') : undefined,
  };
}

export function nextStep(id: SetupStepId): SetupStepId {
  const index = SETUP_STEPS.findIndex((s) => s.id === id);
  return SETUP_STEPS[Math.min(index + 1, SETUP_STEPS.length - 1)]?.id ?? 'validation';
}

export function firstIncomplete(state: SetupState): SetupStepId {
  const done = new Set([...state.completed, ...state.skipped]);
  return SETUP_STEPS.find((s) => !done.has(s.id))?.id ?? 'validation';
}

/** Save a partial update of the wizard state with optimistic concurrency. */
export function useSaveSetup() {
  const client = useQueryClient();
  const patch = usePatchSettings();
  return useCallback(
    async (update: Partial<SetupState> & { setupCompleted?: boolean }): Promise<void> => {
      const attempt = async () => {
        const settings = client.getQueryData<SettingsOut>(keys.settings);
        const current = readSetupState(settings);
        const { setupCompleted, ...rest } = update;
        const next: SetupState = {
          ...current,
          ...rest,
          completed: Array.from(new Set([...(current.completed ?? []), ...(rest.completed ?? [])])),
          skipped: Array.from(new Set([...(current.skipped ?? []), ...(rest.skipped ?? [])])).filter(
            (s) => !(rest.completed ?? []).includes(s),
          ),
          acceptedWarnings: Array.from(new Set([...current.acceptedWarnings, ...(rest.acceptedWarnings ?? [])])),
        };
        const versions: Record<string, number> = {};
        if (settings?.versions.setupState !== undefined) versions.setupState = settings.versions.setupState;
        await patch.mutateAsync({
          setupState: next as unknown as Record<string, unknown>,
          ...(setupCompleted !== undefined ? { setupCompleted } : {}),
          versions,
        });
      };
      try {
        await attempt();
      } catch (error) {
        if (isApiError(error) && error.code === 'VERSION_CONFLICT') {
          await client.refetchQueries({ queryKey: keys.settings });
          await attempt();
          return;
        }
        throw error;
      }
    },
    [client, patch],
  );
}
