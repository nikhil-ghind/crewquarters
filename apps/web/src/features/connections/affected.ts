import { useInstallations, useSchedules } from '../../api/queries';
import type { InstallationOut, ScheduleOut } from '../../api/schema';
import { requiredConnectors, usesCloud } from '../../lib/permissions';

export interface Affected {
  installations: InstallationOut[];
  schedules: ScheduleOut[];
}

/** Installations and schedules that use a provider (display only, for confirmations). */
export function useAffected(provider: string): Affected {
  const installations = useInstallations();
  const schedules = useSchedules();
  const affected = (installations.data ?? []).filter(
    (i) =>
      requiredConnectors(i.requestedPermissions).includes(provider) ||
      usesCloud(i.requestedPermissions).includes(provider),
  );
  const ids = new Set(affected.map((i) => i.id));
  return {
    installations: affected,
    schedules: (schedules.data ?? []).filter((s) => ids.has(s.installationId)),
  };
}

export function affectedList(a: Affected): string[] {
  return [
    ...a.installations.map((i) => `Agent: ${i.agentName}`),
    ...a.schedules.map((s) => `Schedule: ${s.agentName} (${s.cron}, ${s.timezone})`),
  ];
}
