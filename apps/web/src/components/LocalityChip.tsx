import { ArrowUpRight, HardDrive } from 'lucide-react';
import { PROVIDER_NAMES } from '../lib/status';

interface LocalityChipProps {
  /** "local" or a cloud provider id (openai, anthropic). */
  provider?: string | null;
  /** Long form "Local on this device" instead of "Local". */
  long?: boolean;
}

export function isCloudProvider(provider: string | null | undefined): boolean {
  return !!provider && provider !== 'local';
}

/**
 * Local resources: teal `Local` chip. Cloud resources: purple `Cloud · Provider` chip
 * with an outbound-arrow icon and text (section 13.10). Never shown by color alone.
 */
export function LocalityChip({ provider, long = false }: LocalityChipProps) {
  if (isCloudProvider(provider)) {
    const name = PROVIDER_NAMES[provider ?? ''] ?? provider;
    return (
      <span className="badge chip-cloud" title="Data leaves this device for the named provider">
        <ArrowUpRight size={14} aria-hidden="true" />
        <span>Cloud · {name}</span>
      </span>
    );
  }
  return (
    <span className="badge chip-local" title="Processed on this device">
      <HardDrive size={14} aria-hidden="true" />
      <span>{long ? 'Local on this device' : 'Local'}</span>
    </span>
  );
}
