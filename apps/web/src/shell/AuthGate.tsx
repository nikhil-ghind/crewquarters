import { useQuery } from '@tanstack/react-query';
import { useEffect, type ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router';
import { api, unwrap } from '../api/client';
import { ApiError } from '../api/errors';
import { keys, useSettings } from '../api/queries';
import { session, useSessionStatus } from '../api/session';
import { ErrorPanel, SkeletonBlock } from '../components/Feedback';

/** Loads GET /me once; the server decides whether the browser is signed in. */
export function useMe() {
  const status = useSessionStatus();
  const query = useQuery({
    queryKey: keys.me,
    queryFn: async () => {
      try {
        const me = await unwrap(api.GET('/api/v1/me'));
        session.signedIn(me.csrfToken);
        return me;
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          session.anonymous();
          return null;
        }
        throw error;
      }
    },
    staleTime: 5 * 60_000,
    retry: 1,
  });
  return { status, query };
}

function loginPath(location: { pathname: string; search: string }, expired: boolean): string {
  const next = `${location.pathname}${location.search}`;
  const params = new URLSearchParams();
  if (next !== '/' && !next.startsWith('/login')) params.set('next', next);
  if (expired) params.set('expired', '1');
  const qs = params.toString();
  return `/login${qs ? `?${qs}` : ''}`;
}

/**
 * Guards the application shell: anonymous or expired sessions go to /login (and come
 * back afterwards), and an unfinished first-run setup resumes at /setup.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const location = useLocation();
  const { status, query } = useMe();
  const settings = useSettings({ enabled: status === 'authenticated' });

  useEffect(() => {
    document.documentElement.dataset.session = status;
  }, [status]);

  if (status === 'expired') return <Navigate to={loginPath(location, true)} replace />;
  if (query.isPending || status === 'unknown') {
    if (query.isError) {
      return (
        <main className="bare-main" id="main">
          <ErrorPanel error={query.error} title="Could not reach Crewquarters" onRetry={() => void query.refetch()} />
        </main>
      );
    }
    return (
      <main className="bare-main" id="main" aria-busy="true">
        <SkeletonBlock label="Connecting to the device" />
      </main>
    );
  }
  if (status === 'anonymous') return <Navigate to={loginPath(location, false)} replace />;
  if (settings.data && !settings.data.setupCompleted) {
    // The Google OAuth return lands on /connections/google; during setup, resume the
    // wizard's Connections step with the result instead.
    const target = location.pathname === '/connections/google' ? `/setup/connections${location.search}` : '/setup';
    return <Navigate to={target} replace />;
  }
  return <>{children}</>;
}
