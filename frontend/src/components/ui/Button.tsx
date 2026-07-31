import type { CSSProperties, MouseEventHandler, ReactNode } from 'react';
import { Button as RButton } from '@radix-ui/themes';

type ButtonType = 'primary' | 'default' | 'dashed' | 'text' | 'link';
type ButtonSize = 'small' | 'middle' | 'large';

interface ButtonProps {
  children?: ReactNode;
  type?: ButtonType;
  danger?: boolean;
  icon?: ReactNode;
  loading?: boolean;
  disabled?: boolean;
  size?: ButtonSize;
  block?: boolean;
  htmlType?: 'submit' | 'button' | 'reset';
  onClick?: MouseEventHandler<HTMLButtonElement>;
  className?: string;
  style?: CSSProperties;
  title?: string;
  'aria-label'?: string;
}

const VARIANT: Record<ButtonType, 'solid' | 'surface' | 'outline' | 'ghost'> = {
  primary: 'solid',
  default: 'surface',
  dashed: 'outline',
  text: 'ghost',
  link: 'ghost',
};

const SIZE: Record<ButtonSize, '1' | '2' | '3'> = {
  small: '1',
  middle: '2',
  large: '3',
};

export default function Button({
  children,
  type = 'default',
  danger,
  icon,
  loading,
  disabled,
  size = 'middle',
  block,
  htmlType = 'button',
  onClick,
  className,
  style,
  title,
  'aria-label': ariaLabel,
}: ButtonProps) {
  const variant = VARIANT[type];
  const isGhost = variant === 'ghost';

  let color: 'red' | 'blue' | 'gray' | undefined;
  if (danger) color = 'red';
  else if (type === 'link') color = 'blue';
  else if (type === 'text') color = 'gray';

  return (
    <RButton
      type={htmlType}
      variant={variant}
      color={color as any}
      size={SIZE[size]}
      radius={isGhost ? undefined : 'full'}
      loading={loading}
      disabled={disabled}
      onClick={onClick}
      className={className}
      title={title}
      aria-label={ariaLabel}
      style={{
        cursor: disabled ? 'not-allowed' : 'pointer',
        width: block ? '100%' : undefined,
        borderStyle: type === 'dashed' ? 'dashed' : undefined,
        ...style,
      }}
    >
      {icon}
      {children}
    </RButton>
  );
}

export { Button };
