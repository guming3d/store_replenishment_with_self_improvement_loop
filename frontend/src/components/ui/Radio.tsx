import type { CSSProperties, ReactNode } from 'react';
import { RadioGroup, Text } from '@radix-ui/themes';

interface RadioGroupProps {
  value?: string;
  defaultValue?: string;
  onChange?: (event: { target: { value: string } }) => void;
  disabled?: boolean;
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
}

function Group({ value, defaultValue, onChange, disabled, children, className, style }: RadioGroupProps) {
  return (
    <RadioGroup.Root
      value={value}
      defaultValue={defaultValue}
      disabled={disabled}
      onValueChange={(next) => onChange?.({ target: { value: next } })}
      className={className}
      style={style}
    >
      {children}
    </RadioGroup.Root>
  );
}

interface RadioProps {
  value: string;
  disabled?: boolean;
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
}

function Radio({ value, disabled, children, className, style }: RadioProps) {
  return (
    <Text as="label" size="2" className={className} style={{ display: 'inline-flex', alignItems: 'center', gap: 8, cursor: disabled ? 'not-allowed' : 'pointer', ...style }}>
      <RadioGroup.Item value={value} disabled={disabled} />
      {children}
    </Text>
  );
}

Radio.Group = Group;

export default Radio;
export { Radio };
