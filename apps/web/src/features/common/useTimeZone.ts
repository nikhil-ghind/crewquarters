import { useSettings } from '../../api/queries';
import { browserTimeZone } from '../../lib/format';

/** The owner's chosen timezone (Settings), falling back to the browser's. */
export function useTimeZone(): string {
  const settings = useSettings();
  return settings.data?.timezone || browserTimeZone();
}
