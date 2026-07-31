import type { CSSProperties, FormEvent, ReactNode } from 'react';
import { Text } from '@radix-ui/themes';

interface FormProps {
  children?: ReactNode;
  layout?: 'vertical' | 'horizontal' | 'inline';
  onFinish?: (values: any) => void;
  onValuesChange?: (changed: any, all: any) => void;
  className?: string;
  style?: CSSProperties;
  initialValues?: Record<string, unknown>;
}

function Form({ children, onFinish, className, style }: FormProps) {
  return (
    <form
      className={className}
      style={style}
      onSubmit={(event: FormEvent) => {
        event.preventDefault();
        onFinish?.({});
      }}
    >
      {children}
    </form>
  );
}

interface FormItemProps {
  label?: ReactNode;
  name?: string;
  required?: boolean;
  help?: ReactNode;
  children?: ReactNode;
  rules?: unknown[];
  className?: string;
  style?: CSSProperties;
}

function FormItem({ label, required, help, children, className, style }: FormItemProps) {
  return (
    <div className={className} style={{ marginBottom: 16, ...style }}>
      {label != null && (
        <div style={{ marginBottom: 6 }}>
          <Text as="label" size="2" weight="medium">
            {required && <span style={{ color: 'var(--danger-solid)', marginRight: 4 }}>*</span>}
            {label}
          </Text>
        </div>
      )}
      {children}
      {help != null && (
        <div style={{ marginTop: 4 }}>
          <Text size="1" color="gray">{help}</Text>
        </div>
      )}
    </div>
  );
}

function useForm() {
  const instance = {
    getFieldValue: () => undefined,
    setFieldsValue: () => undefined,
    resetFields: () => undefined,
    validateFields: async () => ({}),
    submit: () => undefined,
  };
  return [instance] as const;
}

Form.Item = FormItem;
Form.useForm = useForm;

export default Form;
export { Form };
