import { createContext, useContext, useEffect, useState, type CSSProperties, type ReactNode } from 'react';

type Breakpoint = 'xs' | 'sm' | 'md' | 'lg' | 'xl';

const BREAKPOINTS: Array<{ key: Breakpoint; min: number }> = [
  { key: 'xs', min: 0 },
  { key: 'sm', min: 576 },
  { key: 'md', min: 768 },
  { key: 'lg', min: 992 },
  { key: 'xl', min: 1200 },
];

function useActiveBreakpoint(): Breakpoint {
  const compute = (): Breakpoint => {
    const width = typeof window === 'undefined' ? 1200 : window.innerWidth;
    let active: Breakpoint = 'xs';
    for (const bp of BREAKPOINTS) {
      if (width >= bp.min) active = bp.key;
    }
    return active;
  };
  const [bp, setBp] = useState<Breakpoint>(compute);
  useEffect(() => {
    const handler = () => setBp(compute());
    window.addEventListener('resize', handler);
    return () => window.removeEventListener('resize', handler);
  }, []);
  return bp;
}

const RowContext = createContext<{ hGutter: number }>({ hGutter: 0 });

interface RowProps {
  gutter?: number | [number, number];
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
  align?: 'top' | 'middle' | 'bottom';
  justify?: 'start' | 'end' | 'center' | 'space-between' | 'space-around';
}

export function Row({ gutter = 0, children, className, style, align, justify }: RowProps) {
  const [h, v] = Array.isArray(gutter) ? gutter : [gutter, 0];
  const alignItems = align === 'middle' ? 'center' : align === 'bottom' ? 'flex-end' : 'stretch';
  return (
    <RowContext.Provider value={{ hGutter: h }}>
      <div
        className={className}
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(24, minmax(0, 1fr))',
          columnGap: h,
          rowGap: v,
          alignItems,
          justifyContent: justify,
          ...style,
        }}
      >
        {children}
      </div>
    </RowContext.Provider>
  );
}

interface ColProps {
  span?: number;
  xs?: number;
  sm?: number;
  md?: number;
  lg?: number;
  xl?: number;
  flex?: string | number;
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
}

export function Col({ span, xs, sm, md, lg, xl, flex, children, className, style }: ColProps) {
  useContext(RowContext);
  const active = useActiveBreakpoint();
  const responsive: Record<Breakpoint, number | undefined> = { xs, sm, md, lg, xl };

  let effective = span ?? 24;
  const order: Breakpoint[] = ['xs', 'sm', 'md', 'lg', 'xl'];
  const activeIndex = order.indexOf(active);
  for (let i = activeIndex; i >= 0; i -= 1) {
    const value = responsive[order[i]];
    if (value != null) {
      effective = value;
      break;
    }
  }

  return (
    <div
      className={className}
      style={{
        gridColumn: flex ? undefined : `span ${effective} / span ${effective}`,
        flex: flex as CSSProperties['flex'],
        minWidth: 0,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

export default Row;
