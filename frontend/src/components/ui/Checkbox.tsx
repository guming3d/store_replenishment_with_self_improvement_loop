import type { ChangeEvent, CSSProperties, ReactNode } from 'react';
import { Checkbox as RCheckbox, Text } from '@radix-ui/themes';

interface CheckboxProps {
  checked?: boolean;
  defaultChecked?: boolean;
  onChange?: (event: { target: { checked: boolean } }) => void;
  disabled?: boolean;
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
}

export default function Checkbox({ checked, defaultChecked, onChange, disabled, children, className, style }: CheckboxProps) {
  return (
    <Text as="label" size="2" className={className} style={{ display: 'inline-flex', alignItems: 'center', gap: 8, cursor: disabled ? 'not-allowed' : 'pointer', ...style }}>
      <RCheckbox
        checked={checked}
        defaultChecked={defaultChecked}
        disabled={disabled}
        onCheckedChange={(next) => onChange?.({ target: { checked: next === true } } as { target: { checked: boolean } } & ChangeEvent)}
      />
      {children}
    </Text>
  );
}

export { Checkbox };
