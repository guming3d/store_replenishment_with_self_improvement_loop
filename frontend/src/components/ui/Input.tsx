import {
  forwardRef,
  useState,
  type ChangeEvent,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import { TextField, TextArea as RTextArea } from '@radix-ui/themes';
import { IconEye, IconEyeOff, IconX } from '@tabler/icons-react';

type Status = 'error' | 'warning' | '';

interface InputProps {
  value?: string;
  defaultValue?: string;
  onChange?: (event: ChangeEvent<HTMLInputElement>) => void;
  onPressEnter?: (event: KeyboardEvent<HTMLInputElement>) => void;
  placeholder?: string;
  prefix?: ReactNode;
  suffix?: ReactNode;
  allowClear?: boolean;
  size?: 'small' | 'middle' | 'large';
  status?: Status;
  type?: string;
  disabled?: boolean;
  autoComplete?: string;
  className?: string;
  style?: CSSProperties;
  id?: string;
}

const SIZE: Record<string, '1' | '2' | '3'> = { small: '1', middle: '2', large: '3' };

function statusColor(status?: Status): 'red' | 'amber' | undefined {
  if (status === 'error') return 'red';
  if (status === 'warning') return 'amber';
  return undefined;
}

const InputBase = forwardRef<HTMLInputElement, InputProps>(function InputBase(
  { value, defaultValue, onChange, onPressEnter, placeholder, prefix, suffix, allowClear, size = 'middle', status, type = 'text', disabled, autoComplete, className, style, id },
  ref,
) {
  const showClear = allowClear && !!value;
  return (
    <TextField.Root
      ref={ref}
      id={id}
      size={SIZE[size]}
      color={statusColor(status) as any}
      variant="surface"
      type={type as any}
      value={value}
      defaultValue={defaultValue}
      onChange={onChange}
      onKeyDown={(e) => {
        if (e.key === 'Enter') onPressEnter?.(e);
      }}
      placeholder={placeholder}
      disabled={disabled}
      autoComplete={autoComplete}
      className={className}
      style={style}
    >
      {prefix && <TextField.Slot>{prefix}</TextField.Slot>}
      {(suffix || showClear) && (
        <TextField.Slot side="right">
          {showClear && (
            <button
              type="button"
              aria-label="clear"
              onClick={() =>
                onChange?.({ target: { value: '' } } as ChangeEvent<HTMLInputElement>)
              }
              style={{ cursor: 'pointer', border: 'none', background: 'none', display: 'inline-flex', color: 'var(--text-disabled)' }}
            >
              <IconX size={14} />
            </button>
          )}
          {suffix}
        </TextField.Slot>
      )}
    </TextField.Root>
  );
});

type PasswordProps = Omit<InputProps, 'type' | 'suffix'>;

function Password(props: PasswordProps) {
  const [visible, setVisible] = useState(false);
  return (
    <InputBase
      {...props}
      type={visible ? 'text' : 'password'}
      suffix={
        <button
          type="button"
          aria-label={visible ? 'hide password' : 'show password'}
          onClick={() => setVisible((v) => !v)}
          style={{ cursor: 'pointer', border: 'none', background: 'none', display: 'inline-flex', color: 'var(--text-disabled)' }}
        >
          {visible ? <IconEye size={16} /> : <IconEyeOff size={16} />}
        </button>
      }
    />
  );
}

interface TextAreaProps {
  value?: string;
  defaultValue?: string;
  onChange?: (event: ChangeEvent<HTMLTextAreaElement>) => void;
  placeholder?: string;
  rows?: number;
  status?: Status;
  disabled?: boolean;
  className?: string;
  style?: CSSProperties;
}

function TextAreaComp({ value, defaultValue, onChange, placeholder, rows = 3, status, disabled, className, style }: TextAreaProps) {
  return (
    <RTextArea
      value={value}
      defaultValue={defaultValue}
      onChange={onChange}
      placeholder={placeholder}
      rows={rows}
      color={statusColor(status) as any}
      variant="surface"
      disabled={disabled}
      className={className}
      style={style}
    />
  );
}

type InputComponent = typeof InputBase & {
  Password: typeof Password;
  TextArea: typeof TextAreaComp;
  Search: typeof InputBase;
};

const Input = InputBase as InputComponent;
Input.Password = Password;
Input.TextArea = TextAreaComp;
Input.Search = InputBase;

export default Input;
export { Input };
