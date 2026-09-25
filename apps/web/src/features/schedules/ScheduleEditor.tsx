import { useQuery } from '@tanstack/react-query';
import { useId } from 'react';
import { api, unwrap } from '../../api/client';
import { Banner, SkeletonBlock } from '../../components/Feedback';
import { Field } from '../../components/Field';
import { Advanced } from '../../components/Layout';
import { currentTimeIn, formatUtc, timeZones } from '../../lib/format';

export type Preset = 'daily' | 'weekdays' | 'weekly' | 'custom';

export interface ScheduleDraft {
  preset: Preset;
  time: string; // HH:MM
  weekday: number; // 0 = Sunday
  cron: string;
  timezone: string;
  misfirePolicy: 'fire_once' | 'skip';
}

const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

export function newDraft(timezone: string): ScheduleDraft {
  return { preset: 'daily', time: '10:00', weekday: 1, cron: '0 10 * * *', timezone, misfirePolicy: 'fire_once' };
}

/** Presets are only a friendlier way to write the cron expression the server evaluates. */
export function cronFor(draft: ScheduleDraft): string {
  if (draft.preset === 'custom') return draft.cron.trim();
  const [hh = '0', mm = '0'] = draft.time.split(':');
  const h = String(Number(hh));
  const m = String(Number(mm));
  if (draft.preset === 'weekdays') return `${m} ${h} * * 1-5`;
  if (draft.preset === 'weekly') return `${m} ${h} * * ${draft.weekday}`;
  return `${m} ${h} * * *`;
}

/** Recognize the presets in an existing cron expression (for editing). */
export function draftFromCron(cron: string, timezone: string, misfirePolicy: 'fire_once' | 'skip'): ScheduleDraft {
  const parts = cron.trim().split(/\s+/);
  const base = { ...newDraft(timezone), cron, misfirePolicy };
  if (parts.length !== 5) return { ...base, preset: 'custom' };
  const [m, h, dom, mon, dow] = parts;
  if (!/^\d+$/.test(m ?? '') || !/^\d+$/.test(h ?? '') || dom !== '*' || mon !== '*') return { ...base, preset: 'custom' };
  const time = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
  if (dow === '*') return { ...base, preset: 'daily', time };
  if (dow === '1-5') return { ...base, preset: 'weekdays', time };
  if (/^[0-6]$/.test(dow ?? '')) return { ...base, preset: 'weekly', time, weekday: Number(dow) };
  return { ...base, preset: 'custom' };
}

export function summary(draft: ScheduleDraft): string {
  switch (draft.preset) {
    case 'daily':
      return `Every day at ${draft.time} (${draft.timezone})`;
    case 'weekdays':
      return `Weekdays at ${draft.time} (${draft.timezone})`;
    case 'weekly':
      return `Every ${DAYS[draft.weekday]} at ${draft.time} (${draft.timezone})`;
    default:
      return `Custom: ${draft.cron} (${draft.timezone})`;
  }
}

export function useSchedulePreview(cron: string, timezone: string) {
  return useQuery({
    queryKey: ['schedulePreview', cron, timezone],
    queryFn: () => unwrap(api.POST('/api/v1/schedules/preview', { body: { cron, timezone, count: 3 } })),
    enabled: cron.length > 0 && timezone.length > 0,
    retry: false,
    staleTime: 60_000,
  });
}

export function OccurrencePreview({ cron, timezone }: { cron: string; timezone: string }) {
  const preview = useSchedulePreview(cron, timezone);
  if (preview.isPending) return <SkeletonBlock lines={3} label="Calculating next runs" />;
  if (preview.isError) {
    return (
      <Banner tone="danger" role="alert" title="This schedule is not valid">
        {preview.error.message}
      </Banner>
    );
  }
  return (
    <div className="stack-sm" aria-live="polite">
      <span className="field-label">Next three runs</span>
      <ol style={{ marginLeft: 20 }}>
        {preview.data.occurrences.map((o) => (
          <li key={o.at} title={formatUtc(o.at)}>
            {formatLocal(o.local)} {o.zoneAbbreviation}
          </li>
        ))}
      </ol>
    </div>
  );
}

/** "2026-09-26T10:00:00+05:30" → "Sat, Sep 26, 10:00 (UTC+05:30)". */
export function formatLocal(local: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2})?(?:\.\d+)?([+-]\d{2}:\d{2}|Z)?$/.exec(local);
  if (!match) return local;
  const [, y, mo, d, h, mi, off] = match;
  const date = new Date(Date.UTC(Number(y), Number(mo) - 1, Number(d)));
  const day = new Intl.DateTimeFormat(undefined, { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' }).format(date);
  return `${day}, ${h}:${mi}${off ? ` (UTC${off === 'Z' ? '' : off})` : ''}`;
}

interface ScheduleEditorProps {
  draft: ScheduleDraft;
  onChange: (draft: ScheduleDraft) => void;
  disabled?: boolean;
}

export function ScheduleEditor({ draft, onChange, disabled }: ScheduleEditorProps) {
  const set = (patch: Partial<ScheduleDraft>) => onChange({ ...draft, ...patch });
  const listed = timeZones();
  const zones = listed.includes(draft.timezone) ? listed : [draft.timezone, ...listed];
  const cron = cronFor(draft);
  const presetName = useId();
  const misfireName = useId();
  return (
    <div className="stack">
      <fieldset className="fieldset" disabled={disabled}>
        <legend>Repeat</legend>
        {(
          [
            ['daily', 'Daily at a selected time'],
            ['weekdays', 'Weekdays at a selected time'],
            ['weekly', 'Weekly on a selected day and time'],
          ] as const
        ).map(([value, label]) => (
          <label key={value} className="check-row">
            <input type="radio" name={presetName} checked={draft.preset === value} onChange={() => set({ preset: value })} />
            <span>{label}</span>
          </label>
        ))}
        {draft.preset !== 'custom' ? (
          <div className="grid-2">
            <Field label="Time" required>
              <input className="input" type="time" value={draft.time} onChange={(e) => set({ time: e.target.value })} />
            </Field>
            {draft.preset === 'weekly' ? (
              <Field label="Day" required>
                <select className="select" value={draft.weekday} onChange={(e) => set({ weekday: Number(e.target.value) })}>
                  {DAYS.map((d, i) => (
                    <option key={d} value={i}>
                      {d}
                    </option>
                  ))}
                </select>
              </Field>
            ) : null}
          </div>
        ) : null}
      </fieldset>
      <Field label="Timezone" required help={`Current time there: ${currentTimeIn(draft.timezone)}`}>
        <select className="select" value={draft.timezone} onChange={(e) => set({ timezone: e.target.value })} disabled={disabled}>
          {zones.map((z) => (
            <option key={z} value={z}>
              {z}
            </option>
          ))}
        </select>
      </Field>
      <fieldset className="fieldset" disabled={disabled}>
        <legend>If the device is off at the scheduled time</legend>
        <label className="check-row">
          <input type="radio" name={misfireName} checked={draft.misfirePolicy === 'fire_once'} onChange={() => set({ misfirePolicy: 'fire_once' })} />
          <span>Run once when device returns</span>
        </label>
        <label className="check-row">
          <input type="radio" name={misfireName} checked={draft.misfirePolicy === 'skip'} onChange={() => set({ misfirePolicy: 'skip' })} />
          <span>Skip missed run</span>
        </label>
      </fieldset>
      <Advanced label="Advanced: custom cron">
        <label className="check-row">
          <input
            type="checkbox"
            checked={draft.preset === 'custom'}
            disabled={disabled}
            onChange={(e) => set(e.target.checked ? { preset: 'custom', cron } : { preset: 'daily' })}
          />
          <span>Use a custom cron expression</span>
        </label>
        {draft.preset === 'custom' ? (
          <Field label="Cron expression" help="Five fields: minute hour day-of-month month day-of-week.">
            <input className="input mono" value={draft.cron} disabled={disabled} onChange={(e) => set({ cron: e.target.value })} />
          </Field>
        ) : (
          <p className="muted">
            Cron: <span className="mono">{cron}</span>
          </p>
        )}
      </Advanced>
      <p>
        <span className="field-label">Summary: </span>
        {summary(draft)}
      </p>
      <OccurrencePreview cron={cron} timezone={draft.timezone} />
    </div>
  );
}
