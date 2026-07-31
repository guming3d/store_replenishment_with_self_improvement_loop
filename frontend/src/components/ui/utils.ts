import type { CSSProperties } from 'react';

/** Merge class names, dropping falsy values. */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}

export type RadixColor =
  | 'gray' | 'gold' | 'bronze' | 'brown' | 'yellow' | 'amber' | 'orange' | 'tomato'
  | 'red' | 'ruby' | 'crimson' | 'pink' | 'plum' | 'purple' | 'violet' | 'iris'
  | 'indigo' | 'blue' | 'cyan' | 'teal' | 'jade' | 'green' | 'grass' | 'lime'
  | 'mint' | 'sky';

/**
 * Collapse the legacy Ant Design colour vocabulary onto the 5-hue palette:
 * indigo (brand/info) · gray/slate (neutral) · jade (success) ·
 * amber (warning) · red (danger). No other hue may be introduced.
 */
const COLOR_MAP: Record<string, RadixColor> = {
  // brand / info
  blue: 'indigo',
  geekblue: 'indigo',
  processing: 'indigo',
  indigo: 'indigo',
  purple: 'indigo',
  // success
  green: 'jade',
  jade: 'jade',
  cyan: 'jade',
  teal: 'jade',
  lime: 'jade',
  success: 'jade',
  // warning
  gold: 'amber',
  orange: 'amber',
  amber: 'amber',
  volcano: 'amber',
  warning: 'amber',
  // danger
  red: 'red',
  magenta: 'red',
  error: 'red',
  danger: 'red',
  // neutral
  default: 'gray',
  gray: 'gray',
  slate: 'gray',
};

export function mapColor(color?: string): RadixColor {
  if (!color) return 'gray';
  return COLOR_MAP[color] ?? 'gray';
}

export type AntSize = 'small' | 'middle' | 'large';

/** Map an Ant Design control size to a Radix size token (1-3). */
export function mapControlSize(size?: AntSize | 'default'): '1' | '2' | '3' {
  if (size === 'small') return '1';
  if (size === 'large') return '3';
  return '2';
}

/** Resolve an Ant `size` gap value (keyword, number, or [h, v]) to pixels. */
export function resolveGap(size?: AntSize | number | [number | AntSize, number | AntSize]): {
  column: number;
  row: number;
} {
  const toPx = (value: number | AntSize | undefined): number => {
    if (typeof value === 'number') return value;
    if (value === 'small') return 8;
    if (value === 'large') return 24;
    return 16; // middle / default
  };
  if (Array.isArray(size)) {
    return { column: toPx(size[0]), row: toPx(size[1]) };
  }
  const gap = toPx(size);
  return { column: gap, row: gap };
}

export type StyleProps = { style?: CSSProperties; className?: string };

/**
 * Current Radix appearance, read from the root `<Theme>` element. Used by
 * portalled surfaces (Modal, Drawer) which render outside the root theme and
 * would otherwise fall back to the light token set.
 */
export function currentAppearance(): 'light' | 'dark' {
  if (typeof document === 'undefined') return 'light';
  const root = document.querySelector('.radix-themes[data-is-root-theme="true"]');
  return root?.classList.contains('dark') ? 'dark' : 'light';
}
