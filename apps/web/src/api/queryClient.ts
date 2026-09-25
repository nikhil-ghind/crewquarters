import { QueryClient } from '@tanstack/react-query';
import { ApiError } from './errors';

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Retry reads only for transient failures; 4xx answers are final.
        retry: (count, error) => {
          if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
          return count < 2;
        },
        retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
        staleTime: 5_000,
        // Keep the last good data for read-only display during outages.
        gcTime: 30 * 60_000,
        refetchOnWindowFocus: true,
      },
      mutations: {
        // Never repeat a side effect automatically from the browser (section 13.16).
        retry: false,
      },
    },
  });
}
