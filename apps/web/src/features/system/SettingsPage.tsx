import { useEffect, useState, type FormEvent } from 'react';
import { isApiError, remediation } from '../../api/errors';
import { useActionGuard } from '../../api/guards';
import { usePatchSettings } from '../../api/mutations';
import { useSettings } from '../../api/queries';
import { Button } from '../../components/Button';
import { Banner, ErrorPanel } from '../../components/Feedback';
import { Field } from '../../components/Field';
import { Card, KeyValue, Page, PageHeader } from '../../components/Layout';
import { QueryView } from '../../components/QueryView';
import { useFeedback } from '../../components/Toast';
import { currentTimeIn, isValidTimeZone, timeZones } from '../../lib/format';
import { CallbackUrls } from '../connections/CallbackUrls';
import { SystemTabs } from './SystemTabs';

export default function SettingsPage() {
  const settings = useSettings();
  const patch = usePatchSettings();
  const guard = useActionGuard();
  const { toast } = useFeedback();
  const [timezone, setTimezone] = useState('');
  const [idleMinutes, setIdleMinutes] = useState('');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (settings.data) {
      setTimezone(settings.data.timezone);
      setIdleMinutes(String(Math.round(settings.data.idleUnloadSeconds / 60)));
    }
  }, [settings.data]);

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!settings.data) return;
    const minutes = Number(idleMinutes);
    if (!isValidTimeZone(timezone)) return setError('Choose a valid timezone.');
    if (!Number.isInteger(minutes) || minutes < 1 || minutes > 24 * 60) return setError('Idle unload time must be between 1 and 1440 minutes.');
    setError(null);
    const { versions } = settings.data;
    patch.mutate(
      {
        timezone,
        idleUnloadSeconds: minutes * 60,
        versions: { timezone: versions.timezone ?? 0, idleUnloadSeconds: versions.idleUnloadSeconds ?? 0 },
      },
      { onSuccess: () => toast('Settings saved.') },
    );
  };

  return (
    <Page>
      <PageHeader title="Settings" purpose="Device-wide preferences." />
      <SystemTabs />
      <QueryView query={settings} errorTitle="Could not load settings">
        {(s) => (
          <>
            <Card title="Preferences">
              <form className="form" onSubmit={onSubmit} noValidate>
                <Field label="Timezone" required help={`Times across Crewquarters show in this zone. Current time there: ${currentTimeIn(timezone || s.timezone)}.`}>
                  <select className="select" value={timezone} onChange={(e) => setTimezone(e.target.value)}>
                    {(timeZones().includes(timezone) || !timezone ? timeZones() : [timezone, ...timeZones()]).map((z) => (
                      <option key={z} value={z}>
                        {z}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Unload idle models after (minutes)" required help="A loaded model with no chat or run using it is unloaded after this long, freeing memory.">
                  <input className="input" inputMode="numeric" value={idleMinutes} onChange={(e) => setIdleMinutes(e.target.value)} />
                </Field>
                {error ? (
                  <p className="field-error" role="alert">
                    {error}
                  </p>
                ) : null}
                {patch.isError ? (
                  isApiError(patch.error) && patch.error.code === 'VERSION_CONFLICT' ? (
                    <Banner tone="warning" role="alert" title="Settings changed elsewhere">
                      {remediation(patch.error)}
                    </Banner>
                  ) : (
                    <ErrorPanel error={patch.error} title="Could not save settings" />
                  )
                ) : null}
                <div className="row">
                  <Button type="submit" variant="primary" busy={patch.isPending} busyLabel="Saving…" disabledReason={guard.offline}>
                    Save settings
                  </Button>
                </div>
              </form>
            </Card>
            <Card title="Public callback address" subtitle="Set by the installer; shown here so you can register it with Google and Twilio.">
              <KeyValue items={[['Callback base URL', <span key="u" className="mono break-anywhere">{s.callbackBaseUrl ?? 'Not configured'}</span>]]} />
              <div style={{ marginTop: 12 }}>
                <CallbackUrls />
              </div>
            </Card>
          </>
        )}
      </QueryView>
    </Page>
  );
}
