import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { lazy, Suspense, useState, type ReactNode } from 'react';
import { createBrowserRouter, Navigate, RouterProvider, type RouteObject } from 'react-router';
import { createQueryClient } from './api/queryClient';
import { SkeletonBlock } from './components/Feedback';
import { FeedbackProvider } from './components/Toast';
import { AppShell } from './shell/AppShell';
import { RequireAuth } from './shell/AuthGate';

// Route-level code splitting (section 13.19).
const LoginPage = lazy(() => import('./features/auth/LoginPage'));
const SetupWizard = lazy(() => import('./features/setup/SetupWizard'));
const OverviewPage = lazy(() => import('./features/overview/OverviewPage'));
const MarketplacePage = lazy(() => import('./features/agents/MarketplacePage'));
const AgentDetailPage = lazy(() => import('./features/agents/AgentDetailPage'));
const InstallWizard = lazy(() => import('./features/agents/InstallWizard'));
const InstalledPage = lazy(() => import('./features/agents/InstalledPage'));
const InstallationPage = lazy(() => import('./features/agents/InstallationPage'));
const RunsPage = lazy(() => import('./features/activity/RunsPage'));
const ApprovalsPage = lazy(() => import('./features/activity/ApprovalsPage'));
const RunDetailPage = lazy(() => import('./features/activity/RunDetailPage'));
const ModelsPage = lazy(() => import('./features/models/ModelsPage'));
const ModelDetailPage = lazy(() => import('./features/models/ModelDetailPage'));
const KnowledgePage = lazy(() => import('./features/knowledge/KnowledgePage'));
const KnowledgeBasePage = lazy(() => import('./features/knowledge/KnowledgeBasePage'));
const ChatPage = lazy(() => import('./features/chat/ChatPage'));
const ConnectionsPage = lazy(() => import('./features/connections/ConnectionsPage'));
const ProviderPage = lazy(() => import('./features/connections/ProviderPage'));
const SchedulesPage = lazy(() => import('./features/schedules/SchedulesPage'));
const StatusPage = lazy(() => import('./features/system/StatusPage'));
const AuditPage = lazy(() => import('./features/system/AuditPage'));
const SettingsPage = lazy(() => import('./features/system/SettingsPage'));
const BackupsPage = lazy(() => import('./features/system/BackupsPage'));
const GalleryPage = lazy(() => import('./features/gallery/GalleryPage'));
const NotFoundPage = lazy(() => import('./features/NotFoundPage'));

function Bare({ children }: { children: ReactNode }) {
  return (
    <Suspense
      fallback={
        <main className="bare-main" id="main">
          <SkeletonBlock />
        </main>
      }
    >
      {children}
    </Suspense>
  );
}

export const routes: RouteObject[] = [
  { path: '/login', element: <Bare><LoginPage /></Bare> },
  { path: '/setup', element: <Bare><SetupWizard /></Bare> },
  { path: '/setup/:step', element: <Bare><SetupWizard /></Bare> },
  // Component gallery: static fixtures only, no API calls (see docs/web-ui.md).
  { path: '/__gallery', element: <Bare><GalleryPage /></Bare> },
  {
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <OverviewPage /> },
      { path: 'agents', element: <Navigate to="/agents/installed" replace /> },
      { path: 'agents/marketplace', element: <MarketplacePage /> },
      { path: 'agents/marketplace/:agentId', element: <AgentDetailPage /> },
      { path: 'agents/marketplace/:agentId/install', element: <InstallWizard /> },
      { path: 'agents/installed', element: <InstalledPage /> },
      { path: 'agents/:installationId', element: <InstallationPage /> },
      { path: 'agents/:installationId/:tab', element: <InstallationPage /> },
      { path: 'activity', element: <Navigate to="/activity/runs" replace /> },
      { path: 'activity/runs', element: <RunsPage /> },
      { path: 'activity/approvals', element: <ApprovalsPage /> },
      { path: 'runs/:runId', element: <RunDetailPage /> },
      { path: 'models', element: <ModelsPage /> },
      { path: 'models/:modelId', element: <ModelDetailPage /> },
      { path: 'knowledge', element: <KnowledgePage /> },
      { path: 'knowledge/:kbId', element: <KnowledgeBasePage /> },
      { path: 'chat', element: <ChatPage /> },
      { path: 'chat/:sessionId', element: <ChatPage /> },
      { path: 'connections', element: <ConnectionsPage /> },
      { path: 'connections/:provider', element: <ProviderPage /> },
      { path: 'schedules', element: <SchedulesPage /> },
      { path: 'system', element: <Navigate to="/system/status" replace /> },
      { path: 'system/status', element: <StatusPage /> },
      { path: 'system/audit', element: <AuditPage /> },
      { path: 'system/settings', element: <SettingsPage /> },
      { path: 'system/backups', element: <BackupsPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
];

export function Providers({ client, children }: { client: QueryClient; children: ReactNode }) {
  return (
    <QueryClientProvider client={client}>
      <FeedbackProvider>{children}</FeedbackProvider>
    </QueryClientProvider>
  );
}

export function App() {
  const [client] = useState(createQueryClient);
  const [router] = useState(() => createBrowserRouter(routes));
  return (
    <Providers client={client}>
      <RouterProvider router={router} />
    </Providers>
  );
}
