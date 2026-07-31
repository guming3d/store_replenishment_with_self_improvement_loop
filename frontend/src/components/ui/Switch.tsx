import type { CSSProperties } from 'react';
import { Switch as RSwitch } from '@radix-ui/themes';

interface SwitchProps {
  checked?: boolean;
  defaultChecked?: boolean;
  onChange?: (checked: boolean) => void;
  disabled?: boolean;
  size?: 'small' | 'default';
  className?: string;
  style?: CSSProperties;
}

export default function Switch({ checked, defaultChecked, onChange, disabled, size = 'default', className, style }: SwitchProps) {
  return (
    <RSwitch
      checked={checked}
      defaultChecked={defaultChecked}
      onCheckedChange={onChange}
      disabled={disabled}
      size={size === 'small' ? '1' : '2'}
      className={className}
      style={style}
    />
  );
}

export { Switch };
