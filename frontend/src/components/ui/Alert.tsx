import { useState, type CSSProperties, type ReactNode } from 'react';
import { Callout, Flex, Box } from '@radix-ui/themes';
import {
  IconCircleCheck,
  IconInfoCircle,
  IconAlertTriangle,
  IconAlertCircle,
  IconX,
} from '@tabler/icons-react';

type AlertType = 'success' | 'info' | 'warning' | 'error';

interface AlertProps {
  type?: AlertType;
  message?: ReactNode;
  description?: ReactNode;
  showIcon?: boolean;
  closable?: boolean;
  onClose?: () => void;
  action?: ReactNode;
  icon?: ReactNode;
  banner?: boolean;
  className?: string;
  style?: CSSProperties;
}

const COLOR: Record<AlertType, 'green' | 'blue' | 'amber' | 'red'> = {
  success: 'green',
  info: 'blue',
  warning: 'amber',
  error: 'red',
};

function defaultIcon(type: AlertType) {
  switch (type) {
    case 'success':
      return <IconCircleCheck size={18} />;
    case 'warning':
      return <IconAlertTriangle size={18} />;
    case 'error':
      return <IconAlertCircle size={18} />;
    default:
      return <IconInfoCircle size={18} />;
  }
}

export default function Alert({
  type = 'info',
  message,
  description,
  showIcon,
  closable,
  onClose,
  action,
  icon,
  banner,
  className,
  style,
}: AlertProps) {
  const [closed, setClosed] = useState(false);
  if (closed) return null;

  return (
    <Callout.Root
      color={COLOR[type]}
      variant="soft"
      className={className}
      style={{ borderRadius: banner ? 0 : undefined, ...style }}
    >
      <Flex align={description ? 'start' : 'center'} gap="2" width="100%">
        {showIcon && <Callout.Icon>{icon ?? defaultIcon(type)}</Callout.Icon>}
        <Box style={{ flex: 1, minWidth: 0 }}>
          {message != null && <Callout.Text>{message}</Callout.Text>}
          {description != null && (
            <Callout.Text style={{ marginTop: message != null ? 4 : 0, opacity: 0.85 }}>
              {description}
            </Callout.Text>
          )}
        </Box>
        {action && <Box style={{ flexShrink: 0 }}>{action}</Box>}
        {closable && (
          <button
            type="button"
            aria-label="close"
            onClick={() => {
              setClosed(true);
              onClose?.();
            }}
            style={{ cursor: 'pointer', border: 'none', background: 'none', color: 'inherit', display: 'inline-flex' }}
          >
            <IconX size={16} />
          </button>
        )}
      </Flex>
    </Callout.Root>
  );
}

export { Alert };
