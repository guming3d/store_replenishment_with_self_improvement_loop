import type { CSSProperties, ReactNode } from 'react';
import { mapColor } from './utils';

export interface TimelineItem {
  color?: string;
  dot?: ReactNode;
  label?: ReactNode;
  children?: ReactNode;
}

interface TimelineProps {
  items: TimelineItem[];
  mode?: 'left' | 'right' | 'alternate';
  className?: string;
  style?: CSSProperties;
}

export default function Timeline({ items, className, style }: TimelineProps) {
  return (
    <div className={className} style={{ display: 'flex', flexDirection: 'column', ...style }}>
      {items.map((item, index) => {
        const isLast = index === items.length - 1;
        const dotColor = `var(--${mapColor(item.color)}-9)`;
        return (
          <div key={index} style={{ display: 'flex', gap: 12 }}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: 16,
                  height: 16,
                  flexShrink: 0,
                  marginTop: 4,
                  color: dotColor,
                }}
              >
                {item.dot ?? (
                  <span style={{ width: 10, height: 10, borderRadius: '50%', background: dotColor, display: 'block' }} />
                )}
              </span>
              {!isLast && <span style={{ flex: 1, width: 2, background: 'var(--border-subtle)', marginTop: 2 }} />}
            </div>
            <div style={{ paddingBottom: isLast ? 0 : 20, minWidth: 0, flex: 1 }}>
              {item.label != null && (
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>{item.label}</div>
              )}
              {item.children}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export { Timeline };
