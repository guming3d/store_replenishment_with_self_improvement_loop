/**
 * Chart / SVG palette.
 *
 * Charts render into SVG presentation attributes (`fill`, `stroke`), which do
 * not resolve CSS custom properties, so the palette is exported as literal
 * values. Every value is a Radix step-9 (or step-7) colour and is therefore
 * identical in light and dark appearance — the same 5 hues as the CSS tokens
 * in `styles.css`. Anywhere a normal DOM style is used, prefer the semantic
 * tokens (`var(--chart-1)`, `var(--success-text)`, …) instead.
 */

/** Categorical series order — never introduce a 7th hue. */
export const CHART_SERIES = [
  '#3e63dd', // indigo-9  · brand
  '#29a383', // jade-9    · success
  '#ffc53d', // amber-9   · warning
  '#e5484d', // red-9     · danger
  '#abbdf9', // indigo-7  · brand, lighter
  '#8b8d98', // slate-9   · neutral
] as const;

/** Signed values: positive / negative deltas inside charts. */
export const CHART_POSITIVE = '#29a383'; // jade-9
export const CHART_NEGATIVE = '#e5484d'; // red-9

/** Axes, reference lines, grid. */
export const CHART_AXIS = '#8b8d98'; // slate-9
