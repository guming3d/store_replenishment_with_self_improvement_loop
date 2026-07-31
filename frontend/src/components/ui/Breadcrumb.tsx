import { Fragment, type CSSProperties, type ReactNode } from 'react';

export interface BreadcrumbItem {
  title: ReactNode;
}

interface BreadcrumbProps {
  items: BreadcrumbItem[];
  separator?: ReactNode;
  className?: string;
  style?: CSSProperties;
}

export default function Breadcrumb({ items, separator = '/', className, style }: BreadcrumbProps) {
  return (
    <nav className={className} style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', fontSize: 13, color: 'var(--text-secondary)', ...style }} aria-label="breadcrumb">
      {items.map((item, index) => (
        <Fragment key={index}>
          {index > 0 && <span style={{ color: 'var(--text-disabled)' }}>{separator}</span>}
          <span style={{ color: index === items.length - 1 ? 'var(--text-primary)' : undefined }}>{item.title}</span>
        </Fragment>
      ))}
    </nav>
  );
}

export { Breadcrumb };
