import { BellRing } from 'lucide-react';
import { useState } from 'react';
import { Button } from '../../components/Button';
import { Banner } from '../../components/Feedback';
import { Card } from '../../components/Layout';
import { StatusBadge } from '../../components/StatusBadge';
import { useFeedback } from '../../components/Toast';
import { PROMPT_DISMISSED_KEY, useNotificationSettings, type NotificationSettings } from '../../lib/notifications';
import { NOTIFY_STATUS, type NotifyState } from '../../lib/status';
import { usePref } from '../../lib/storage';

export function notifyState({ permission, active }: Pick<NotificationSettings, 'permission' | 'active'>): NotifyState {
  if (permission === 'unsupported') return 'unsupported';
  if (permission === 'insecure') return 'insecure';
  if (permission === 'denied') return 'blocked';
  return active ? 'on' : 'off';
}

const STILL_WORKS = 'The Activity badge, the count in the tab title and Crew Requests still show what needs you.';

function currentAddress(): string {
  return typeof window === 'undefined' ? 'this address' : window.location.origin;
}

/** System › Settings: the per-browser "Notify me when an agent needs input" control. */
export function NotificationSettingsCard() {
  const settings = useNotificationSettings();
  const { announce } = useFeedback();
  const [busy, setBusy] = useState(false);
  const state = notifyState(settings);

  const turnOn = async () => {
    setBusy(true);
    try {
      const result = await settings.enable();
      if (result === 'granted') announce('Notifications are on for this browser.');
      else if (result === 'denied') announce('The browser blocked notifications.', 'assertive');
    } finally {
      setBusy(false);
    }
  };

  const turnOff = () => {
    settings.disable();
    announce('Notifications are off for this browser.');
  };

  return (
    <Card
      title="Browser notifications"
      subtitle="Applies to this browser only. Each browser and device you use has its own setting."
      actions={<StatusBadge status={NOTIFY_STATUS[state]} context="Notifications" />}
    >
      <div className="stack">
        {state === 'on' ? (
          <>
            <p>
              When an agent needs your input while this tab is in the background, this browser shows a notification.
              Selecting it opens the run so you can answer.
            </p>
            <div className="row">
              <Button onClick={turnOff}>Turn off notifications</Button>
            </div>
          </>
        ) : null}
        {state === 'off' ? (
          <>
            <p>
              Get a notification when an agent asks a question or needs approval while this tab is in the background.
              {settings.permission === 'default' ? ' Your browser asks for permission first.' : ''}
            </p>
            <div className="row">
              <Button
                icon={<BellRing size={16} aria-hidden="true" />}
                busy={busy}
                busyLabel="Waiting for the browser…"
                onClick={() => void turnOn()}
              >
                Notify me when an agent needs input
              </Button>
            </div>
          </>
        ) : null}
        {state === 'blocked' ? (
          <Banner tone="warning" title="Notifications are blocked for this site">
            To allow them, open the site settings for {currentAddress()} (the icon at the left of the address bar), set
            Notifications to Allow, then come back to this page. {STILL_WORKS}
          </Banner>
        ) : null}
        {state === 'insecure' ? (
          <Banner tone="warning" title="Notifications need HTTPS or localhost">
            Browsers only allow notifications on secure connections. You opened Crewquarters at {currentAddress()}, which is
            plain HTTP. To turn them on, open Crewquarters at the appliance&apos;s HTTPS address, or at localhost on the
            appliance itself. {STILL_WORKS}
          </Banner>
        ) : null}
        {state === 'unsupported' ? (
          <Banner tone="neutral" title="This browser can't show notifications for Crewquarters">
            Some browsers, including most phone browsers, only show notifications for installed apps. {STILL_WORKS}
          </Banner>
        ) : null}
      </div>
    </Card>
  );
}

/**
 * A small prompt on Crew Requests while something is pending and the browser has not
 * been asked yet. Nothing is requested until the owner clicks.
 */
export function NotificationPrompt() {
  const settings = useNotificationSettings();
  const { toast } = useFeedback();
  const [dismissed, setDismissed] = usePref<boolean>(PROMPT_DISMISSED_KEY, false);
  const [busy, setBusy] = useState(false);
  if (dismissed || settings.permission !== 'default' || settings.wanted) return null;

  const turnOn = async () => {
    setBusy(true);
    try {
      const result = await settings.enable();
      if (result === 'granted') toast('Notifications are on for this browser.');
      else if (result === 'denied') toast('The browser blocked notifications. System › Settings explains how to allow them.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Banner
      tone="info"
      role="none"
      className="notify-prompt"
      title="Get notified when an agent needs input"
      action={
        <>
          <Button busy={busy} busyLabel="Waiting for the browser…" onClick={() => void turnOn()}>
            Notify me
          </Button>
          <Button variant="tertiary" onClick={() => setDismissed(true)}>
            Not now
          </Button>
        </>
      }
    >
      This browser can show a notification when a new Crew Request arrives while the tab is in the background. You can
      change this later in System › Settings.
    </Banner>
  );
}
