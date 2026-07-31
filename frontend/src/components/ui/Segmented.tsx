import type { CSSProperties, ReactNode } from 'react';
import { SegmentedControl } from '@radix-ui/themes';

export interface SegmentedOption {
  value: string;
  label: ReactNode;
  disabled?: boolean;
}

interface SegmentedProps {
  value?: string;
  onChange?: (value: string) => void;
  options: SegmentedOption[];
  size?: 'small' | 'middle' | 'large';
  className?: string;
  style?: CSSProperties;
}

const SIZE: Record<string, '1' | '2' | '3'> = { small: '1', middle: '2', large: '3' };

export default function Segmented({ value, onChange, options, size = 'middle', className, style }: SegmentedProps) {
  return (
    <SegmentedControl.Root
      value={value}
      onValueChange={(next) => {
        const option = options.find((o) => o.value === next);
        if (option?.disabled) return;
        onChange?.(next);
      }}
      size={SIZE[size]}
      radius="full"
      className={className}
      style={style}
    >
      {options.map((option) => (
        <SegmentedControl.Item
          key={option.value}
          value={option.value}
          style={option.disabled ? { opacity: 0.5, pointerEvents: 'none' } : undefined}
        >
          {option.label}
        </SegmentedControl.Item>
      ))}
    </SegmentedControl.Root>
  );
}

export { Segmented };
