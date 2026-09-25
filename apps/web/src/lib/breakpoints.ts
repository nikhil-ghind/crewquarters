/** Responsive breakpoints from PLAN.md section 13.18. Keep in sync with the CSS. */
import { useSyncExternalStore } from 'react';

export const BREAKPOINT_TABLET = 768;
export const BREAKPOINT_DESKTOP = 1280;

export type Layout = 'mobile' | 'tablet' | 'desktop';

function current(): Layout {
  if (typeof window === 'undefined') return 'desktop';
  const w = window.innerWidth;
  if (w >= BREAKPOINT_DESKTOP) return 'desktop';
  if (w >= BREAKPOINT_TABLET) return 'tablet';
  return 'mobile';
}

function subscribe(listener: () => void): () => void {
  window.addEventListener('resize', listener);
  return () => window.removeEventListener('resize', listener);
}

export function useLayout(): Layout {
  return useSyncExternalStore(subscribe, current, () => 'desktop');
}
