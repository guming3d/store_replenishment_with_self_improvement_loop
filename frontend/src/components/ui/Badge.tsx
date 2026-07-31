import type { CSSProperties, ReactNode } from 'react';

interface BadgeProps {
  count?: number;
  showZero?: boolean;
  overflowCount?: number;
  size?: 'small' | 'default';
  dot?: boolean;
  color?: string;
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
}

function Pill({ count, overflowCount = 99, size, color, dot }: BadgeProps) {
  const display = dot ? '' : count! > overflowCount ? `${overflowCount}+` : String(count);
  return (
    <span
      className="x-badge-count"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        minWidth: dot ? 8 : size === 'small' ? 16 : 18,
        height: dot ? 8 : size === 'small' ? 16 : 18,
        padding: dot ? 0 : '0 5px',
        borderRadius: 999,
        fontSize: 11,
        fontWeight: 700,
        lineHeight: 1,
        color: 'var(--brand-on-solid)',
        background: color ?? 'var(--danger-solid)',
      }}
    >
      {display}
    </span>
  );
}

export default function Badge({
  count = 0,
  showZero,
  overflowCount = 99,
  size = 'default',
  dot,
  color,
  children,
  className,
  style,
}: BadgeProps) {
  const visible = dot || count > 0 || showZero;

  if (!children) {
    return visible ? (
      <span className={className} style={style}>
        <Pill count={count} overflowCount={overflowCount} size={size} color={color} dot={dot} />
      </span>
    ) : null;
  }

  return (
    <span className={className} style={{ position: 'relative', display: 'inline-flex', ...style }}>
      {children}
      {visible && (
        <span style={{ position: 'absolute', top: 0, right: 0, transform: 'translate(50%, -50%)' }}>
          <Pill count={count} overflowCount={overflowCount} size={size} color={color} dot={dot} />
        </span>
      )}
    </span>
  );
}

export { Badge };
