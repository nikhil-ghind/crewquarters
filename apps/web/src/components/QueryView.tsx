import type { UseQueryResult } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { Banner, ErrorPanel, SkeletonBlock } from './Feedback';

interface QueryViewProps<T> {
  query: UseQueryResult<T>;
  /** Plain-language failure title, e.g. "Could not load runs". */
  errorTitle: string;
  loading?: ReactNode;
  /** Rendered instead of children when the data is empty. */
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  children: (data: T) => ReactNode;
}

/**
 * The five screen states (section 13.16): initial loading (skeleton), empty, ready,
 * partial/degraded (cached data kept visible read-only with a warning when a refresh
 * fails) and error (nothing cached).
 */
export function QueryView<T>({ query, errorTitle, loading, isEmpty, empty, children }: QueryViewProps<T>) {
  if (query.isPending) return <>{loading ?? <SkeletonBlock />}</>;
  if (query.isError && query.data === undefined) {
    return <ErrorPanel error={query.error} title={errorTitle} onRetry={() => void query.refetch()} />;
  }
  const data = query.data as T;
  return (
    <>
      {query.isError ? (
        <Banner tone="warning" title="Showing the last loaded information">
          The latest refresh failed, so this may be out of date. It updates when the device responds.
        </Banner>
      ) : null}
      {isEmpty?.(data) && empty ? empty : children(data)}
    </>
  );
}
