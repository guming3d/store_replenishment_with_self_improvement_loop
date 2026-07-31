import { useState, type ReactNode } from 'react';
import { Popover, Flex, Text } from '@radix-ui/themes';
import Button from './Button';

interface PopconfirmProps {
  title?: ReactNode;
  onConfirm?: () => void;
  onCancel?: () => void;
  okText?: ReactNode;
  cancelText?: ReactNode;
  okButtonProps?: { danger?: boolean };
  children: ReactNode;
}

export default function Popconfirm({ title, onConfirm, onCancel, okText = 'OK', cancelText = 'Cancel', okButtonProps, children }: PopconfirmProps) {
  const [open, setOpen] = useState(false);
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger>{children}</Popover.Trigger>
      <Popover.Content size="1" style={{ maxWidth: 260 }}>
        <Flex direction="column" gap="3">
          <Text size="2">{title}</Text>
          <Flex gap="2" justify="end">
            <Button
              size="small"
              onClick={() => {
                setOpen(false);
                onCancel?.();
              }}
            >
              {cancelText}
            </Button>
            <Button
              size="small"
              type="primary"
              danger={okButtonProps?.danger}
              onClick={() => {
                setOpen(false);
                onConfirm?.();
              }}
            >
              {okText}
            </Button>
          </Flex>
        </Flex>
      </Popover.Content>
    </Popover.Root>
  );
}

export { Popconfirm };
