import { PanelLeftClose, PanelLeftOpen, Server } from 'lucide-react';
import { Link, useLocation } from 'react-router';
import { useAttention, useSystemStatus } from '../api/queries';
import { StatusBadge } from '../components/StatusBadge';
import { DEVICE_STATUS } from '../lib/status';
import { isActive, NAV_ITEMS } from './navigation';

interface SidebarProps {
  collapsed: boolean;
  onToggle?: () => void;
  onNavigate?: () => void;
  id?: string;
}

export function deviceName(): string {
  return typeof window === 'undefined' ? 'This device' : window.location.hostname || 'This device';
}

export function Sidebar({ collapsed, onToggle, onNavigate, id }: SidebarProps) {
  const location = useLocation();
  const attention = useAttention();
  const status = useSystemStatus();
  const count = attention.data?.count ?? 0;

  return (
    <aside className="sidebar" data-collapsed={collapsed ? 'true' : 'false'} id={id} aria-label="Sidebar">
      <div className="sidebar-brand">
        <span className="brand-mark" aria-hidden="true">
          <Server size={18} />
        </span>
        <span className="brand-text">
          <span className="brand-name">Crewquarters</span>
          <span className="brand-device" title={deviceName()}>
            {deviceName()}
          </span>
        </span>
      </div>
      <nav className="sidebar-nav" aria-label="Primary">
        <ul>
          {NAV_ITEMS.map((item) => {
            const active = isActive(item, location.pathname);
            const Icon = item.icon;
            const showBadge = item.badge === 'attention' && count > 0;
            return (
              <li key={item.to}>
                <Link
                  to={item.to}
                  className="nav-link"
                  aria-current={active ? 'page' : undefined}
                  title={collapsed ? item.label : undefined}
                  onClick={onNavigate}
                >
                  <Icon size={20} aria-hidden="true" />
                  <span className="nav-label">{item.label}</span>
                  {showBadge ? (
                    <span className="count-badge">
                      {count}
                      <span className="sr-only"> {count === 1 ? 'item needs' : 'items need'} attention</span>
                    </span>
                  ) : null}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
      <div className="sidebar-footer">
        {status.data ? (
          <Link to="/system/status" className="nav-link" onClick={onNavigate} title="System status">
            <span className="sidebar-text">
              <StatusBadge status={DEVICE_STATUS[status.data.status]} context="Device" />
            </span>
            {collapsed ? <Server size={20} aria-hidden="true" /> : null}
          </Link>
        ) : null}
        {onToggle ? (
          <button
            type="button"
            className="nav-link collapse-toggle"
            style={{ border: 'none', background: 'none', cursor: 'pointer', width: '100%' }}
            aria-expanded={!collapsed}
            aria-controls={id}
            onClick={onToggle}
          >
            {collapsed ? <PanelLeftOpen size={20} aria-hidden="true" /> : <PanelLeftClose size={20} aria-hidden="true" />}
            <span className="nav-label">{collapsed ? 'Expand sidebar' : 'Collapse sidebar'}</span>
          </button>
        ) : null}
      </div>
    </aside>
  );
}
