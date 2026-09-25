import { useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router';
import { useConnectivity } from '../api/connectivity';
import { useConnections, useSystemStatus } from '../api/queries';
import { Banner } from '../components/Feedback';
import { formatRelative } from '../lib/format';

/**
 * Global degraded/offline behavior (section 13.13): a persistent red banner when the
 * API is unreachable (cached pages stay readable, risky actions disabled), amber when
 * live updates are reconnecting, low disk, runtime unavailable, and expired Google
 * access linking to the one reconnect flow.
 */
export function GlobalBanners() {
  const { api, reconnecting, lastOkAt } = useConnectivity();
  const status = useSystemStatus();
  const connections = useConnections();
  const client = useQueryClient();

  const disk = status.data?.checks.find((c) => c.group === 'storage' && c.name === 'disk');
  const runtime = status.data?.checks.find((c) => c.name === 'runtime daemon');
  const google = connections.data?.find((c) => c.provider === 'google');

  return (
    <div className="stack-sm" style={{ gap: 0 }}>
      {api === 'offline' ? (
        <Banner
          tone="danger"
          role="alert"
          className="global-banner"
          title="Crewquarters is not responding"
          action={
            <button type="button" className="btn btn-secondary" onClick={() => void client.refetchQueries({ type: 'active' })}>
              Retry now
            </button>
          }
        >
          Showing the last information loaded{lastOkAt ? ` (${formatRelative(new Date(lastOkAt).toISOString())})` : ''}. Actions are
          disabled until the device reconnects; retrying automatically.
        </Banner>
      ) : null}
      {api !== 'offline' && reconnecting > 0 ? (
        <Banner tone="warning" className="global-banner" title="Live updates paused—reconnecting">
          Progress may lag for a moment. Nothing is lost or started again.
        </Banner>
      ) : null}
      {disk && disk.status !== 'passed' ? (
        <Banner
          tone={disk.status === 'failed' ? 'danger' : 'warning'}
          className="global-banner"
          title="Storage is running low"
          action={<Link to="/system/status">View storage</Link>}
        >
          {disk.detail}. New model downloads and document uploads are blocked when space runs out.
        </Banner>
      ) : null}
      {runtime && runtime.status === 'failed' ? (
        <Banner tone="danger" className="global-banner" title="Agent runtime unavailable" action={<Link to="/system/status">Details</Link>}>
          History stays available, but runs and models cannot start until the runtime is back.
        </Banner>
      ) : null}
      {google && google.status === 'NEEDS_ATTENTION' ? (
        <Banner
          tone="warning"
          className="global-banner"
          title="Google access expired"
          action={
            <Link className="btn btn-secondary" to="/connections/google">
              Reconnect Google
            </Link>
          }
        >
          Agents that read Gmail or Sheets will fail readiness until you reconnect.
        </Banner>
      ) : null}
    </div>
  );
}
