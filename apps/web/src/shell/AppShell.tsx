import { X } from 'lucide-react';
import { Suspense, useEffect, useRef, useState } from 'react';
import { Outlet, useLocation } from 'react-router';
import { SkeletonBlock } from '../components/Feedback';
import { useLayout } from '../lib/breakpoints';
import { usePref } from '../lib/storage';
import { GlobalBanners } from './GlobalBanners';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import { useInputRequestAlerts } from './useInputRequestAlerts';

/**
 * Persistent left sidebar, compact device-status top bar and content canvas
 * (section 13.3). Below 768 px the sidebar becomes a navigation drawer.
 */
export function AppShell() {
  const layout = useLayout();
  // null = automatic: expanded on wide desktop, icon-only at medium widths.
  const [collapsedPref, setCollapsedPref] = usePref<boolean | null>('sidebar.collapsed', null);
  const collapsed = layout === 'mobile' ? false : (collapsedPref ?? layout === 'tablet');
  const [navOpen, setNavOpen] = useState(false);
  const drawerRef = useRef<HTMLDialogElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const location = useLocation();
  // "(N) " tab-title prefix and optional browser notifications for Crew Requests.
  useInputRequestAlerts();

  useEffect(() => {
    const dialog = drawerRef.current;
    if (!dialog) return;
    if (navOpen && !dialog.open) dialog.showModal();
    if (!navOpen && dialog.open) dialog.close();
  }, [navOpen]);

  useEffect(() => {
    setNavOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (layout !== 'mobile') setNavOpen(false);
  }, [layout]);

  return (
    <div className="app-shell" data-collapsed={collapsed ? 'true' : 'false'}>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      {layout !== 'mobile' ? (
        <Sidebar
          id="sidebar"
          collapsed={collapsed}
          onToggle={() => setCollapsedPref(!collapsed)}
        />
      ) : null}
      <div className="main-column">
        <TopBar onOpenNav={() => setNavOpen(true)} />
        <GlobalBanners />
        <main id="main" ref={mainRef} className="main-content" tabIndex={-1}>
          <Suspense fallback={<div className="page"><SkeletonBlock /></div>}>
            <Outlet />
          </Suspense>
        </main>
      </div>
      {layout === 'mobile' ? (
        <dialog
          ref={drawerRef}
          className="nav-drawer"
          aria-label="Navigation"
          onClose={() => setNavOpen(false)}
          onCancel={() => setNavOpen(false)}
        >
          <div className="row-between" style={{ padding: 8 }}>
            <span className="field-label" style={{ paddingLeft: 8 }}>
              Menu
            </span>
            <button type="button" className="btn btn-tertiary btn-icon" aria-label="Close navigation" onClick={() => setNavOpen(false)}>
              <X size={20} aria-hidden="true" />
            </button>
          </div>
          <Sidebar collapsed={false} onNavigate={() => setNavOpen(false)} />
        </dialog>
      ) : null}
    </div>
  );
}
