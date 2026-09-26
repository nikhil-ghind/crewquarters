import { ChevronRight } from 'lucide-react';
import { useEffect, type ReactNode } from 'react';
import { Link, NavLink } from 'react-router';
import { setBaseTitle } from '../lib/documentTitle';

export interface Crumb {
  label: string;
  to?: string;
}

interface PageHeaderProps {
  title: ReactNode;
  purpose?: ReactNode;
  breadcrumbs?: Crumb[];
  /** One primary action on the right (section 13.3). */
  actions?: ReactNode;
  status?: ReactNode;
  /** Plain-text document title if `title` is not a string. */
  documentTitle?: string;
}

export function useDocumentTitle(title: string | undefined): void {
  useEffect(() => {
    if (title) setBaseTitle(`${title} · Crewquarters`);
  }, [title]);
}

export function PageHeader({ title, purpose, breadcrumbs, actions, status, documentTitle }: PageHeaderProps) {
  useDocumentTitle(documentTitle ?? (typeof title === 'string' ? title : undefined));
  return (
    <header className="page-header">
      {breadcrumbs && breadcrumbs.length > 0 ? (
        <nav aria-label="Breadcrumb" className="breadcrumb">
          <ol>
            {breadcrumbs.map((crumb, i) => (
              <li key={`${crumb.label}-${i}`}>
                {crumb.to && i < breadcrumbs.length - 1 ? (
                  <Link to={crumb.to}>{crumb.label}</Link>
                ) : (
                  <span aria-current={i === breadcrumbs.length - 1 ? 'page' : undefined}>
                    {crumb.label}
                  </span>
                )}
              </li>
            ))}
          </ol>
        </nav>
      ) : null}
      <div className="page-title-row">
        <div className="page-title-block">
          <h1 className="break-anywhere">{title}</h1>
          {purpose ? <p className="page-purpose">{purpose}</p> : null}
        </div>
        {actions ? <div className="page-actions">{actions}</div> : null}
      </div>
      {status ? <div className="row">{status}</div> : null}
    </header>
  );
}

export function Page({ children, label }: { children: ReactNode; label?: string }) {
  return (
    <div className="page" aria-label={label}>
      {children}
    </div>
  );
}

interface CardProps {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
  as?: 'section' | 'article' | 'div';
  compact?: boolean;
  headingLevel?: 2 | 3;
  labelledBy?: string;
}

export function Card({
  title,
  subtitle,
  actions,
  children,
  className,
  as = 'section',
  compact = false,
  headingLevel = 2,
}: CardProps) {
  const Tag = as;
  const Heading = headingLevel === 2 ? 'h2' : 'h3';
  return (
    <Tag className={`card${compact ? ' card-compact' : ''} ${className ?? ''}`}>
      {title || actions ? (
        <div className="card-header">
          <div className="stack-sm" style={{ gap: 2 }}>
            {title ? <Heading className="card-title">{title}</Heading> : null}
            {subtitle ? <p className="card-subtitle">{subtitle}</p> : null}
          </div>
          {actions ? <div className="row">{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </Tag>
  );
}

export function Section({
  title,
  actions,
  children,
  id,
}: {
  title: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  id?: string;
}) {
  return (
    <section className="section" aria-labelledby={id}>
      <div className="section-header">
        <h2 id={id}>{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

export interface TabLink {
  to: string;
  label: string;
  end?: boolean;
  badge?: ReactNode;
}

/** Route-backed tabs: related views use tabs rather than extra sidebar levels. */
export function RouteTabs({ tabs, label }: { tabs: TabLink[]; label: string }) {
  return (
    <nav className="tabs" aria-label={label}>
      {tabs.map((tab) => (
        <NavLink key={tab.to} to={tab.to} end={tab.end} className="tab">
          {tab.label}
          {tab.badge}
        </NavLink>
      ))}
    </nav>
  );
}

/** "Advanced" disclosure for logs, raw JSON, digests and IDs (section 13.1). */
export function Advanced({
  children,
  label = 'Advanced',
  defaultOpen = false,
}: {
  children: ReactNode;
  label?: string;
  defaultOpen?: boolean;
}) {
  return (
    <details className="disclosure" open={defaultOpen || undefined}>
      <summary>
        <ChevronRight size={16} aria-hidden="true" />
        {label}
      </summary>
      <div className="disclosure-body">{children}</div>
    </details>
  );
}

export function KeyValue({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([k, v], i) => (
        <div key={i} style={{ display: 'contents' }}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function RawJson({ value, label }: { value: unknown; label: string }) {
  // JSON.stringify output is rendered as a text node: nothing is interpreted as HTML.
  return (
    <pre className="raw-json mono" aria-label={label} tabIndex={0}>
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}
