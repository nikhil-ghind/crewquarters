import { useSettings } from '../../api/queries';
import { CopyButton, SkeletonBlock } from '../../components/Feedback';
import { KeyValue } from '../../components/Layout';

/** The exact callback addresses to register with Google and Twilio (read-only). */
export function CallbackUrls() {
  const settings = useSettings();
  const urls = settings.data?.callbackUrls ?? {};
  const entries: [string, string][] = [
    ['Google redirect URI', urls.googleRedirectUri ?? ''],
    ['Twilio callback base', urls.twilioCallbackBase ?? ''],
  ];
  if (!settings.data) return <SkeletonBlock lines={2} />;
  if (!settings.data.callbackBaseUrl && entries.every(([, v]) => !v)) {
    return <p>No public callback address is configured. Google and Twilio stay unavailable until the installer sets one.</p>;
  }
  return (
    <KeyValue
      items={entries
        .filter(([, v]) => v)
        .map(([k, v]) => [
          k,
          <span key={k} className="row">
            <span className="mono break-anywhere">{v}</span>
            <CopyButton text={v} label="Copy" />
          </span>,
        ])}
    />
  );
}
