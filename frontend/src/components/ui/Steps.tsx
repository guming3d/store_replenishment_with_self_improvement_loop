import type { CSSProperties } from 'react';
import type { ReactNode } from 'react';

export interface StepItem {
  title: ReactNode;
  description?: ReactNode;
}

interface StepsProps {
  current?: number;
  status?: 'wait' | 'process' | 'finish' | 'error';
  items: StepItem[];
  className?: string;
  style?: CSSProperties;
}

export default function Steps({ current = 0, status = 'process', items, className, style }: StepsProps) {
  return (
    <div className={className} style={{ display: 'flex', alignItems: 'flex-start', width: '100%', ...style }}>
      {items.map((item, index) => {
        const done = index < current;
        const active = index === current;
        const isError = active && status === 'error';
        const color = isError
          ? 'var(--danger-solid)'
          : done || (active && status === 'finish')
            ? 'var(--success-solid)'
            : active
              ? 'var(--brand-solid)'
              : 'var(--border-subtle)';
        const textColor = active || done ? 'var(--text-primary)' : 'var(--text-muted)';
        return (
          <div key={index} style={{ display: 'flex', flex: 1, alignItems: 'center', minWidth: 0 }}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, minWidth: 0 }}>
              <span
                style={{
                  width: 28,
                  height: 28,
                  borderRadius: '50%',
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 13,
                  fontWeight: 700,
                  color: done || active ? 'var(--brand-on-solid)' : 'var(--text-secondary)',
                  background: done || active ? color : 'transparent',
                  border: done || active ? 'none' : '1px solid var(--border)',
                }}
              >
                {done ? '✓' : index + 1}
              </span>
              <span style={{ fontSize: 12.5, color: textColor, textAlign: 'center', whiteSpace: 'nowrap' }}>{item.title}</span>
            </div>
            {index < items.length - 1 && (
              <span style={{ flex: 1, height: 2, margin: '0 8px', background: index < current ? 'var(--success-solid)' : 'var(--border-subtle)', alignSelf: 'flex-start', marginTop: 13 }} />
            )}
          </div>
        );
      })}
    </div>
  );
}

export { Steps };
