/**
 * Mutations. Each takes an idempotency key chosen per user intent (see useIntentKey),
 * so a double click or a "try again" after an unknown outcome replays the stored
 * response instead of repeating the side effect.
 *
 * Nothing here is optimistic except reversible metadata (schedule enable/disable),
 * as required by section 13.16.
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useCallback, useRef, useState } from 'react';
import { api, idempotencyKey, mutate, withKey } from './client';
import { keys } from './queries';
import type {
  ChatSessionCreateIn,
  InstallationCreateIn,
  InstallationPatchIn,
  ModelOut,
  ScheduleCreateIn,
  ScheduleOut,
  SchedulePatchIn,
  SettingsPatchIn,
} from './schema';
import { storeModel } from './streams';

/** A stable key for one user intent; reset it after the intent succeeds or is abandoned. */
export function useIntentKey(): [string, () => void] {
  const [key, setKey] = useState(idempotencyKey);
  const reset = useCallback(() => setKey(idempotencyKey()), []);
  return [key, reset];
}

/** Like useIntentKey but readable synchronously inside handlers. */
export function useIntentKeyRef(): { current: () => string; reset: () => void } {
  const ref = useRef(idempotencyKey());
  return {
    current: () => ref.current,
    reset: () => {
      ref.current = idempotencyKey();
    },
  };
}

// --- Runs ----------------------------------------------------------------------------

export function useCreateRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ installationId, key }: { installationId: string; key: string }) =>
      mutate(api.POST('/api/v1/runs', { body: { installationId }, headers: withKey(key) })),
    onSuccess: (run) => {
      client.setQueryData(keys.run(run.id), run);
      void client.invalidateQueries({ queryKey: keys.runsAll });
    },
  });
}

export function useCancelRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ runId, key }: { runId: string; key: string }) =>
      mutate(api.POST('/api/v1/runs/{run_id}/cancel', { params: { path: { run_id: runId } }, headers: withKey(key) })),
    onSettled: (_d, _e, { runId }) => {
      void client.invalidateQueries({ queryKey: keys.run(runId) });
      void client.invalidateQueries({ queryKey: keys.runsAll });
      void client.invalidateQueries({ queryKey: keys.attention });
    },
  });
}

export function useRetryRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ runId, key }: { runId: string; key: string }) =>
      mutate(api.POST('/api/v1/runs/{run_id}/retry', { params: { path: { run_id: runId } }, headers: withKey(key) })),
    onSuccess: (run) => {
      client.setQueryData(keys.run(run.id), run);
    },
    onSettled: (_d, _e, { runId }) => {
      void client.invalidateQueries({ queryKey: keys.run(runId) });
      void client.invalidateQueries({ queryKey: keys.runsAll });
      void client.invalidateQueries({ queryKey: keys.attention });
    },
  });
}

export function useAcknowledgeRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ runId }: { runId: string }) =>
      mutate(api.POST('/api/v1/runs/{run_id}/acknowledge', { params: { path: { run_id: runId } } })),
    onSettled: (_d, _e, { runId }) => {
      void client.invalidateQueries({ queryKey: keys.run(runId) });
      void client.invalidateQueries({ queryKey: keys.attention });
    },
  });
}

export function useAnswerInput() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, version, value, key }: { id: string; version: number; value: unknown; key: string }) =>
      mutate(
        api.POST('/api/v1/input-requests/{input_request_id}/answer', {
          params: { path: { input_request_id: id } },
          body: { version, value },
          headers: withKey(key),
        }),
      ),
    onSettled: (data) => {
      void client.invalidateQueries({ queryKey: keys.inputRequestsAll });
      void client.invalidateQueries({ queryKey: keys.attention });
      if (data) void client.invalidateQueries({ queryKey: keys.run(data.runId) });
    },
  });
}

// --- Installations --------------------------------------------------------------------

export function useInstallAgent() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ body, key }: { body: InstallationCreateIn; key: string }) =>
      mutate(api.POST('/api/v1/agent-installations', { body, headers: withKey(key) })),
    onSuccess: (inst) => {
      client.setQueryData(keys.installation(inst.id), inst);
      void client.invalidateQueries({ queryKey: keys.installations });
      void client.invalidateQueries({ queryKey: keys.catalog });
    },
  });
}

export function usePatchInstallation(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ body, key }: { body: InstallationPatchIn; key: string }) =>
      mutate(
        api.PATCH('/api/v1/agent-installations/{installation_id}', {
          params: { path: { installation_id: id } },
          body,
          headers: withKey(key),
        }),
      ),
    onSuccess: (inst) => {
      client.setQueryData(keys.installation(id), inst);
      void client.invalidateQueries({ queryKey: keys.installations });
      void client.invalidateQueries({ queryKey: keys.schedules });
    },
  });
}

export function useUninstall() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, key }: { id: string; key: string }) =>
      mutate(
        api.DELETE('/api/v1/agent-installations/{installation_id}', {
          params: { path: { installation_id: id } },
          headers: withKey(key),
        }),
      ),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: keys.installations });
      void client.invalidateQueries({ queryKey: keys.catalog });
      void client.invalidateQueries({ queryKey: keys.schedules });
    },
  });
}

// --- Schedules ----------------------------------------------------------------------------

export function useCreateSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ body, key }: { body: ScheduleCreateIn; key: string }) =>
      mutate(api.POST('/api/v1/schedules', { body, headers: withKey(key) })),
    onSettled: () => void client.invalidateQueries({ queryKey: keys.schedules }),
  });
}

export function usePatchSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: SchedulePatchIn }) =>
      mutate(api.PATCH('/api/v1/schedules/{schedule_id}', { params: { path: { schedule_id: id } }, body })),
    // Enable/disable is reversible metadata: optimistic with rollback (section 13.16).
    onMutate: async ({ id, body }) => {
      await client.cancelQueries({ queryKey: keys.schedules });
      const previous = client.getQueryData<ScheduleOut[]>(keys.schedules);
      if (body.enabled !== undefined && body.enabled !== null && Object.keys(body).length === 2) {
        client.setQueryData<ScheduleOut[]>(keys.schedules, (old) =>
          old?.map((s) => (s.id === id ? { ...s, enabled: body.enabled ?? s.enabled } : s)),
        );
      }
      return { previous };
    },
    onError: (_e, _v, context) => {
      if (context?.previous) client.setQueryData(keys.schedules, context.previous);
    },
    onSettled: () => void client.invalidateQueries({ queryKey: keys.schedules }),
  });
}

export function useDeleteSchedule() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id }: { id: string }) =>
      mutate(api.DELETE('/api/v1/schedules/{schedule_id}', { params: { path: { schedule_id: id } } })),
    onSettled: () => void client.invalidateQueries({ queryKey: keys.schedules }),
  });
}

// --- Models ---------------------------------------------------------------------------------

export type ModelAction = 'install' | 'load' | 'unload' | 'cancel' | 'delete';

export function useModelAction() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      modelId,
      action,
      key,
      clear = false,
    }: {
      modelId: string;
      action: ModelAction;
      key: string;
      clear?: boolean;
    }): Promise<ModelOut> => {
      const params = { path: { model_id: modelId } };
      const headers = withKey(key);
      switch (action) {
        case 'install':
          return mutate(api.POST('/api/v1/models/{model_id}/install', { params, headers }));
        case 'load':
          return mutate(api.POST('/api/v1/models/{model_id}/load', { params, headers }));
        case 'unload':
          return mutate(api.POST('/api/v1/models/{model_id}/unload', { params, headers, body: { force: false } }));
        case 'cancel':
          return mutate(api.POST('/api/v1/models/{model_id}/install/cancel', { params, headers, body: { clear } }));
        case 'delete':
          return mutate(api.DELETE('/api/v1/models/{model_id}', { params, headers }));
      }
    },
    onSuccess: (model) => storeModel(client, model),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: keys.models });
      void client.invalidateQueries({ queryKey: keys.memory });
      void client.invalidateQueries({ queryKey: keys.attention });
    },
  });
}

// --- Settings ---------------------------------------------------------------------------------

export function usePatchSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: SettingsPatchIn) => mutate(api.PATCH('/api/v1/settings', { body })),
    onSuccess: (settings) => client.setQueryData(keys.settings, settings),
  });
}

// --- Chat --------------------------------------------------------------------------------------

export function useCreateChat() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ body, key }: { body: ChatSessionCreateIn; key: string }) =>
      mutate(api.POST('/api/v1/chat/sessions', { body, headers: withKey(key) })),
    onSettled: () => void client.invalidateQueries({ queryKey: keys.chatSessions }),
  });
}

export function useToggleChat() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, enable, key }: { id: string; enable: boolean; key: string }) =>
      enable
        ? mutate(api.POST('/api/v1/chat/sessions/{session_id}/enable', { params: { path: { session_id: id } }, headers: withKey(key) }))
        : mutate(api.POST('/api/v1/chat/sessions/{session_id}/disable', { params: { path: { session_id: id } }, headers: withKey(key) })),
    onSettled: (_d, _e, { id }) => {
      void client.invalidateQueries({ queryKey: keys.chatSession(id) });
      void client.invalidateQueries({ queryKey: keys.chatSessions });
      void client.invalidateQueries({ queryKey: keys.models });
      void client.invalidateQueries({ queryKey: keys.memory });
    },
  });
}

export function useDeleteChat() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id }: { id: string }) =>
      mutate(api.DELETE('/api/v1/chat/sessions/{session_id}', { params: { path: { session_id: id } } })),
    onSettled: () => void client.invalidateQueries({ queryKey: keys.chatSessions }),
  });
}
