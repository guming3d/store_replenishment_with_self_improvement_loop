import type { CSSProperties, ReactNode } from 'react';
import { cn } from './utils';

interface CardProps {
  children?: ReactNode;
  title?: ReactNode;
  extra?: ReactNode;
  size?: 'small' | 'default';
  bordered?: boolean;
  loading?: boolean;
  className?: string;
  style?: CSSProperties;
  bodyStyle?: CSSProperties;
  onClick?: () => void;
}

function LoadingBlock() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {[80, 100, 60].map((w, i) => (
        <div
          key={i}
          className="x-shimmer-block"
          style={{ height: 14, width: `${w}%`, borderRadius: 6 }}
        />
      ))}
    </div>
  );
}

export default function Card({
  children,
  title,
  extra,
  size = 'default',
  bordered = true,
  loading,
  className,
  style,
  bodyStyle,
  onClick,
}: CardProps) {
  const hasHeader = title != null || extra != null;
  return (
    <div
      className={cn('x-card', size === 'small' && 'x-card-small', !bordered && 'x-card-borderless', className)}
      style={style}
      onClick={onClick}
    >
      {hasHeader && (
        <div className="x-card-head">
          <div className="x-card-head-title">{title}</div>
          {extra != null && <div className="x-card-extra">{extra}</div>}
        </div>
      )}
      <div className="x-card-body" style={bodyStyle}>
        {loading ? <LoadingBlock /> : children}
      </div>
    </div>
  );
}

export { Card };
