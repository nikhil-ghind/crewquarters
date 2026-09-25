import { useMemo } from 'react';
import { useKnowledgeBases, useModels } from '../../api/queries';
import type { SchemaFormOptions } from '../../components/SchemaForm';
import { timeZones } from '../../lib/format';
import { MODEL_DOWNLOAD_STATUS } from '../../lib/status';

/** Selector options for the generated configuration form. */
export function useFormOptions(): SchemaFormOptions {
  const models = useModels();
  const kbs = useKnowledgeBases();
  return useMemo(
    () => ({
      timezones: timeZones(),
      models: (models.data ?? [])
        .filter((m) => m.id.startsWith('local.') && !(m.capabilities ?? []).includes('embedding'))
        .map((m) => ({ value: m.id, label: `${m.displayName} (${MODEL_DOWNLOAD_STATUS[m.downloadState].label})` })),
      knowledgeBases: (kbs.data ?? []).map((kb) => ({ value: kb.id, label: kb.name })),
    }),
    [models.data, kbs.data],
  );
}
