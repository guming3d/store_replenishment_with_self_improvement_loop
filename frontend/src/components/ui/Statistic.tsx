import type { CSSProperties, ReactNode } from 'react';

interface StatisticProps {
  title?: ReactNode;
  value?: ReactNode;
  precision?: number;
  prefix?: ReactNode;
  suffix?: ReactNode;
  valueStyle?: CSSProperties;
  className?: string;
  style?: CSSProperties;
}

function formatValue(value: ReactNode, precision?: number): ReactNode {
  if (precision != null && typeof value === 'number') {
    return value.toFixed(precision);
  }
  return value;
}

export default function Statistic({
  title,
  value,
  precision,
  prefix,
  suffix,
  valueStyle,
  className,
  style,
}: StatisticProps) {
  return (
    <div className={`x-statistic ${className ?? ''}`} style={style}>
      {title != null && <div className="x-statistic-title">{title}</div>}
      <div className="x-statistic-content" style={valueStyle}>
        {prefix && <span className="x-statistic-prefix">{prefix}</span>}
        <span className="x-statistic-value tabular">{formatValue(value, precision)}</span>
        {suffix && <span className="x-statistic-suffix">{suffix}</span>}
      </div>
    </div>
  );
}

export { Statistic };
