import { useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import { TextField } from '@radix-ui/themes';

interface InputNumberProps {
  value?: number | null;
  onChange?: (value: number | null) => void;
  min?: number;
  max?: number;
  step?: number;
  precision?: number;
  addonAfter?: ReactNode;
  size?: 'small' | 'middle' | 'large';
  status?: 'error' | 'warning' | '';
  disabled?: boolean;
  placeholder?: string;
  className?: string;
  style?: CSSProperties;
}

const SIZE: Record<string, '1' | '2' | '3'> = { small: '1', middle: '2', large: '3' };

function statusColor(status?: string): 'red' | 'amber' | undefined {
  if (status === 'error') return 'red';
  if (status === 'warning') return 'amber';
  return undefined;
}

export default function InputNumber({
  value,
  onChange,
  min,
  max,
  step = 1,
  precision,
  addonAfter,
  size = 'middle',
  status,
  disabled,
  placeholder,
  className,
  style,
}: InputNumberProps) {
  const [text, setText] = useState<string>(value == null ? '' : String(value));

  useEffect(() => {
    const parsed = text === '' ? null : Number(text);
    if (value == null && text !== '') {
      setText('');
    } else if (value != null && parsed !== value) {
      setText(String(value));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  const clamp = (num: number): number => {
    let next = num;
    if (min != null) next = Math.max(min, next);
    if (max != null) next = Math.min(max, next);
    if (precision != null) next = Number(next.toFixed(precision));
    return next;
  };

  const handleChange = (raw: string) => {
    setText(raw);
    if (raw === '' || raw === '-' || raw === '.') {
      onChange?.(null);
      return;
    }
    const num = Number(raw);
    if (Number.isNaN(num)) {
      onChange?.(null);
      return;
    }
    onChange?.(num);
  };

  const handleBlur = () => {
    if (text === '' || text === '-' || text === '.') return;
    const num = Number(text);
    if (Number.isNaN(num)) {
      setText(value == null ? '' : String(value));
      return;
    }
    const clamped = clamp(num);
    setText(String(clamped));
    if (clamped !== value) onChange?.(clamped);
  };

  const stepBy = (dir: 1 | -1) => {
    const base = text === '' ? (min ?? 0) : Number(text);
    const next = clamp((Number.isNaN(base) ? 0 : base) + dir * step);
    setText(String(next));
    onChange?.(next);
  };

  return (
    <TextField.Root
      size={SIZE[size]}
      color={statusColor(status) as any}
      variant="surface"
      inputMode="decimal"
      value={text}
      disabled={disabled}
      placeholder={placeholder}
      className={className}
      style={style}
      onChange={(e) => handleChange(e.target.value)}
      onBlur={handleBlur}
      onKeyDown={(e) => {
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          stepBy(1);
        } else if (e.key === 'ArrowDown') {
          e.preventDefault();
          stepBy(-1);
        }
      }}
    >
      {addonAfter != null && (
        <TextField.Slot side="right" style={{ color: 'var(--text-muted)' }}>
          {addonAfter}
        </TextField.Slot>
      )}
    </TextField.Root>
  );
}

export { InputNumber };
