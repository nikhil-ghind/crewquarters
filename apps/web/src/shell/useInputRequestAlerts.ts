import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { useInputRequests } from '../api/queries';
import { setTitleCount } from '../lib/documentTitle';
import {
  CHANNEL_NAME,
  inBackground,
  InputRequestNotifier,
  requestPath,
  showBrowserNotification,
  useNotificationSettings,
  type ChannelMessage,
} from '../lib/notifications';

/** Poll interval for pending Crew Requests while a tab with notifications on is in the background. */
export const BACKGROUND_POLL_MS = 5_000;
/** The regular interval (the same as useInputRequests' default). */
export const FOREGROUND_POLL_MS = 10_000;

function useInBackground(): boolean {
  const [background, setBackground] = useState(inBackground);
  useEffect(() => {
    const update = () => setBackground(inBackground());
    document.addEventListener('visibilitychange', update);
    window.addEventListener('focus', update);
    window.addEventListener('blur', update);
    return () => {
      document.removeEventListener('visibilitychange', update);
      window.removeEventListener('focus', update);
      window.removeEventListener('blur', update);
    };
  }, []);
  return background;
}

/**
 * Mounted once in the app shell: keeps the "(N) " tab-title prefix in step with the
 * pending Crew Requests, and shows a browser notification for each new one while this
 * tab is in the background (when the owner turned notifications on for this browser).
 */
export function useInputRequestAlerts(): void {
  const navigate = useNavigate();
  const settings = useNotificationSettings();
  const background = useInBackground();
  const { active } = settings;
  // Same query (and cache entry) as Crew Requests and Overview. With notifications on,
  // a hidden tab keeps polling, faster, so a new request is noticed within ~5 s.
  const pending = useInputRequests(
    {},
    {
      refetchInterval: active && background ? BACKGROUND_POLL_MS : FOREGROUND_POLL_MS,
      refetchIntervalInBackground: active,
    },
  );

  const activeRef = useRef(active);
  activeRef.current = active;
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;

  const [notifier] = useState(
    () =>
      new InputRequestNotifier({
        active: () => activeRef.current,
        inBackground,
        show: showBrowserNotification,
        open: (request) => {
          window.focus();
          void navigateRef.current(requestPath(request));
        },
      }),
  );

  useEffect(() => {
    if (typeof BroadcastChannel !== 'function') return;
    const channel = new BroadcastChannel(CHANNEL_NAME);
    channel.onmessage = (event: MessageEvent) => notifier.receive(event.data);
    notifier.setChannel({ postMessage: (message: ChannelMessage) => channel.postMessage(message) });
    return () => {
      notifier.setChannel(null);
      channel.close();
    };
  }, [notifier]);

  const count = pending.data?.length;
  useEffect(() => {
    if (count !== undefined) setTitleCount(count);
  }, [count]);
  useEffect(() => () => setTitleCount(0), []);

  useEffect(() => {
    if (pending.data) notifier.update(pending.data);
  }, [notifier, pending.data]);

  useEffect(() => {
    if (!active) notifier.closeAll();
  }, [notifier, active]);

  useEffect(() => () => notifier.closeAll(), [notifier]);
}
