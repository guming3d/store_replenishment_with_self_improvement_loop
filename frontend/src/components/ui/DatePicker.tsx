import type { CSSProperties } from 'react';
import { TextField } from '@radix-ui/themes';

export interface DateValue {
  endOf: (unit?: string) => { toISOString: () => string };
  startOf: (unit?: string) => { toISOString: () => string };
  toISOString: () => string;
  format: () => string;
}

function makeDateValue(str: string): DateValue {
  return {
    endOf: () => ({ toISOString: () => new Date(`${str}T23:59:59.999`).toISOString() }),
    startOf: () => ({ toISOString: () => new Date(`${str}T00:00:00.000`).toISOString() }),
    toISOString: () => new Date(`${str}T00:00:00.000`).toISOString(),
    format: () => str,
  };
}

interface DatePickerProps {
  value?: string;
  defaultValue?: string;
  onChange?: (value: DateValue | null, dateString: string) => void;
  placeholder?: string;
  disabled?: boolean;
  size?: 'small' | 'middle' | 'large';
  className?: string;
  style?: CSSProperties;
}

const SIZE: Record<string, '1' | '2' | '3'> = { small: '1', middle: '2', large: '3' };

export default function DatePicker({ value, defaultValue, onChange, placeholder, disabled, size = 'middle', className, style }: DatePickerProps) {
  return (
    <TextField.Root
      type="date"
      size={SIZE[size]}
      variant="surface"
      value={value}
      defaultValue={defaultValue}
      disabled={disabled}
      placeholder={placeholder}
      className={className}
      style={style}
      onChange={(e) => {
        const str = e.target.value;
        onChange?.(str ? makeDateValue(str) : null, str);
      }}
    />
  );
}

export { DatePicker };
