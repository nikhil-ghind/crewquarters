import {
  Activity,
  BookOpen,
  Bot,
  CalendarClock,
  Cpu,
  LayoutDashboard,
  MessagesSquare,
  Plug,
  Settings2,
  type LucideIcon,
} from 'lucide-react';

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** Path prefixes that mark this item as the current destination. */
  match: string[];
  badge?: 'attention';
}

/** Information architecture from PLAN.md section 13.2. */
export const NAV_ITEMS: NavItem[] = [
  { to: '/', label: 'Overview', icon: LayoutDashboard, match: ['/'] },
  { to: '/agents/installed', label: 'Crew', icon: Bot, match: ['/agents'] },
  { to: '/activity/runs', label: 'Activity', icon: Activity, match: ['/activity', '/runs'], badge: 'attention' },
  { to: '/models', label: 'Models', icon: Cpu, match: ['/models'] },
  { to: '/knowledge', label: 'Knowledge', icon: BookOpen, match: ['/knowledge'] },
  { to: '/chat', label: 'Chat', icon: MessagesSquare, match: ['/chat'] },
  { to: '/connections', label: 'Connections', icon: Plug, match: ['/connections'] },
  { to: '/schedules', label: 'Schedules', icon: CalendarClock, match: ['/schedules'] },
  { to: '/system/status', label: 'System', icon: Settings2, match: ['/system'] },
];

export function isActive(item: NavItem, pathname: string): boolean {
  return item.match.some((prefix) =>
    prefix === '/' ? pathname === '/' : pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}
