/**
 * The tab title: the page title set by PageHeader, prefixed with "(N) " while N Crew
 * Requests are pending (0 → no prefix). Kept in one place so the page title and the
 * count never overwrite each other.
 */
const PREFIX = /^\(\d+\) /;

let base = typeof document === 'undefined' ? 'Crewquarters' : document.title.replace(PREFIX, '') || 'Crewquarters';
let count = 0;

function apply(): void {
  if (typeof document === 'undefined') return;
  document.title = count > 0 ? `(${count}) ${base}` : base;
}

export function setBaseTitle(title: string): void {
  base = title;
  apply();
}

export function setTitleCount(n: number): void {
  const next = Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
  if (next === count) return;
  count = next;
  apply();
}

