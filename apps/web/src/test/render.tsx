import { QueryClient } from '@tanstack/react-query';
import { render, type RenderResult } from '@testing-library/react';
import type { ReactElement } from 'react';
import { createMemoryRouter, RouterProvider, type RouteObject } from 'react-router';
import { Providers } from '../App';

export function testQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } },
  });
}

/** Render inside the app providers and a data router (needed for useBlocker). */
export function renderWithProviders(
  ui: ReactElement,
  { path = '/', route = '/', extraRoutes = [] }: { path?: string; route?: string; extraRoutes?: RouteObject[] } = {},
): RenderResult & { client: QueryClient } {
  const client = testQueryClient();
  const router = createMemoryRouter([{ path, element: ui }, ...extraRoutes, { path: '*', element: <p>Other page</p> }], {
    initialEntries: [route],
  });
  const result = render(
    <Providers client={client}>
      <RouterProvider router={router} />
    </Providers>,
  );
  return { ...result, client };
}
