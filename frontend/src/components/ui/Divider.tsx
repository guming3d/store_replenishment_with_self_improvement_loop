import type { CSSProperties, ReactNode } from 'react';

interface DividerProps {
  type?: 'horizontal' | 'vertical';
  orientation?: 'left' | 'right' | 'center';
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
}

export default function Divider({ type = 'horizontal', orientation = 'center', children, className, style }: DividerProps) {
  if (type === 'vertical') {
    return (
      <span
        className={className}
        style={{
          display: 'inline-block',
          width: 1,
          height: '0.9em',
          margin: '0 8px',
          background: 'var(--border)',
          verticalAlign: 'middle',
          ...style,
        }}
      />
    );
  }

  if (children) {
    return (
      <div
        className={className}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          margin: '16px 0',
          color: 'var(--text-secondary)',
          fontSize: 14,
          ...style,
        }}
      >
        {orientation !== 'left' && <span style={{ flex: 1, height: 1, background: 'var(--border)' }} />}
        <span>{children}</span>
        {orientation !== 'right' && <span style={{ flex: 1, height: 1, background: 'var(--border)' }} />}
      </div>
    );
  }

  return (
    <div
      className={className}
      style={{ height: 1, background: 'var(--border)', margin: '16px 0', ...style }}
      role="separator"
    />
  );
}

export { Divider };
