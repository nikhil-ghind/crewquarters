import { RouteTabs } from '../../components/Layout';

export function SystemTabs() {
  return (
    <RouteTabs
      label="System views"
      tabs={[
        { to: '/system/status', label: 'Status' },
        { to: '/system/audit', label: 'Audit' },
        { to: '/system/settings', label: 'Settings' },
        { to: '/system/backups', label: 'Backups' },
      ]}
    />
  );
}
