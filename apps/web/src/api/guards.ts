/**
 * Reasons to disable risky actions while the platform is degraded (section 13.13).
 * These only read server-reported status; the server still rejects anything unsafe.
 */
import { useConnectivity } from './connectivity';
import { useSystemStatus } from './queries';

export interface ActionGuard {
  /** Set when the API cannot be reached: every mutation is disabled. */
  offline: string | null;
  /** Set when the runtime daemon is unavailable: run and model actions are disabled. */
  runtime: string | null;
}

export const OFFLINE_REASON = 'The device is not responding. Actions resume when it reconnects.';
export const RUNTIME_REASON = 'The agent runtime is unavailable, so runs and models cannot start. See System status.';

export function useActionGuard(): ActionGuard {
  const { api } = useConnectivity();
  const status = useSystemStatus({ staleTime: 30_000 });
  const runtimeDown =
    status.data?.checks.some((c) => c.name === 'runtime daemon' && c.status === 'failed') ?? false;
  const offline = api === 'offline' ? OFFLINE_REASON : null;
  return {
    offline,
    runtime: offline ?? (runtimeDown ? RUNTIME_REASON : null),
  };
}
