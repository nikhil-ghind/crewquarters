import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react';
import { useMemo, useState, type ReactNode } from 'react';
import { useLayout } from '../lib/breakpoints';

export interface Column<T> {
  key: string;
  header: string;
  cell: (row: T) => ReactNode;
  /** Enables sorting on this column. */
  sortValue?: (row: T) => string | number | null;
  /** Emphasized first field in the mobile card. */
  primary?: boolean;
  className?: string;
}

interface DataTableProps<T> {
  caption: string;
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  /**
   * Mouse convenience only. Keyboard and screen-reader users use the link that the
   * primary cell must contain, so every row stays reachable by Tab.
   */
  onRowClick?: (row: T) => void;
  empty?: ReactNode;
  initialSort?: { key: string; direction: 'asc' | 'desc' };
}

/**
 * Semantic table with sortable headers and keyboard-focusable rows; below 768 px it
 * becomes a card list (section 13.18).
 */
export function DataTable<T>({
  caption,
  columns,
  rows,
  rowKey,
  onRowClick,
  empty,
  initialSort,
}: DataTableProps<T>) {
  const layout = useLayout();
  const [sort, setSort] = useState(initialSort ?? null);

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const column = columns.find((c) => c.key === sort.key);
    if (!column?.sortValue) return rows;
    const get = column.sortValue;
    return [...rows].sort((a, b) => {
      const av = get(a);
      const bv = get(b);
      if (av === bv) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
      const result = av < bv ? -1 : 1;
      return sort.direction === 'asc' ? result : -result;
    });
  }, [rows, columns, sort]);

  if (rows.length === 0 && empty) return <>{empty}</>;

  if (layout === 'mobile') {
    return (
      <ul className="card-list" aria-label={caption}>
        {sorted.map((row) => (
          <li key={rowKey(row)} className="card-list-item">
            <dl>
              {columns.map((c) => (
                <div key={c.key} style={{ display: 'contents' }}>
                  <dt>{c.header}</dt>
                  <dd>{c.cell(row)}</dd>
                </div>
              ))}
            </dl>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <div className="table-wrap">
      <table className="data-table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((c) => {
              const active = sort?.key === c.key;
              const ariaSort = active ? (sort?.direction === 'asc' ? 'ascending' : 'descending') : undefined;
              return (
                <th key={c.key} scope="col" aria-sort={ariaSort} className={c.className}>
                  {c.sortValue ? (
                    <button
                      type="button"
                      className="sort-button"
                      onClick={() =>
                        setSort({
                          key: c.key,
                          direction: active && sort?.direction === 'asc' ? 'desc' : 'asc',
                        })
                      }
                    >
                      {c.header}
                      {active ? (
                        sort?.direction === 'asc' ? (
                          <ArrowUp size={14} aria-hidden="true" />
                        ) : (
                          <ArrowDown size={14} aria-hidden="true" />
                        )
                      ) : (
                        <ArrowUpDown size={14} aria-hidden="true" />
                      )}
                    </button>
                  ) : (
                    c.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr
              key={rowKey(row)}
              data-clickable={onRowClick ? 'true' : undefined}
              onClick={
                onRowClick
                  ? (e) => {
                      // Links and buttons inside the row keep their own behavior.
                      if ((e.target as HTMLElement).closest('a,button,input,select,label')) return;
                      onRowClick(row);
                    }
                  : undefined
              }
            >
              {columns.map((c) => (
                <td key={c.key} className={c.className}>
                  {c.cell(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
