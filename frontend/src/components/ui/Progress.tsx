import type { CSSProperties } from 'react';

interface ProgressProps {
  percent?: number;
  size?: 'small' | 'default';
  showInfo?: boolean;
  status?: 'success' | 'exception' | 'normal' | 'active';
  strokeColor?: string;
  className?: string;
  style?: CSSProperties;
}

export default function Progress({ percent = 0, size = 'default', showInfo, status, strokeColor, className, style }: ProgressProps) {
  const clamped = Math.max(0, Math.min(100, percent));
  const color = strokeColor
    ?? (status === 'exception' ? 'var(--danger-solid)' : status === 'success' ? 'var(--success-solid)' : 'var(--brand-solid)');
  const height = size === 'small' ? 6 : 8;
  return (
    <div className={className} style={{ display: 'flex', alignItems: 'center', gap: 8, ...style }}>
      <div style={{ flex: 1, height, borderRadius: 999, background: 'var(--border-subtle)', overflow: 'hidden' }}>
        <div style={{ width: `${clamped}%`, height: '100%', background: color, borderRadius: 999, transition: 'width 0.2s ease' }} />
      </div>
      {showInfo && <span style={{ fontSize: 12, color: 'var(--text-secondary)', minWidth: 34, textAlign: 'right' }}>{clamped}%</span>}
    </div>
  );
}

export { Progress };
