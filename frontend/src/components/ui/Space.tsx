import type { CSSProperties, ReactNode } from 'react';
import { Children } from 'react';
import { resolveGap, type AntSize } from './utils';

type Align = 'start' | 'end' | 'center' | 'baseline';

interface SpaceProps {
  children?: ReactNode;
  direction?: 'horizontal' | 'vertical';
  size?: AntSize | number | [number | AntSize, number | AntSize];
  align?: Align;
  wrap?: boolean;
  split?: ReactNode;
  className?: string;
  style?: CSSProperties;
}

const alignMap: Record<Align, string> = {
  start: 'flex-start',
  end: 'flex-end',
  center: 'center',
  baseline: 'baseline',
};

function Space({
  children,
  direction = 'horizontal',
  size = 'small',
  align,
  wrap,
  split,
  className,
  style,
}: SpaceProps) {
  const gap = resolveGap(size);
  const items = Children.toArray(children);

  const resolvedAlign =
    align ?? (direction === 'horizontal' ? 'center' : undefined);

  const containerStyle: CSSProperties = {
    display: 'inline-flex',
    flexDirection: direction === 'vertical' ? 'column' : 'row',
    rowGap: gap.row,
    columnGap: gap.column,
    flexWrap: wrap ? 'wrap' : undefined,
    alignItems: resolvedAlign ? alignMap[resolvedAlign] : undefined,
    ...style,
  };

  return (
    <div className={className} style={containerStyle}>
      {split
        ? items.map((child, index) => (
            <div key={index} style={{ display: 'inline-flex', alignItems: 'center', gap: gap.column }}>
              {child}
              {index < items.length - 1 ? split : null}
            </div>
          ))
        : items}
    </div>
  );
}

interface CompactProps {
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
  block?: boolean;
}

function Compact({ children, className, style, block }: CompactProps) {
  return (
    <div
      className={cnCompact(className)}
      style={{ display: block ? 'flex' : 'inline-flex', width: block ? '100%' : undefined, ...style }}
    >
      {children}
    </div>
  );
}

function cnCompact(className?: string): string {
  return ['x-space-compact', className].filter(Boolean).join(' ');
}

Space.Compact = Compact;

export default Space;
export { Space };
