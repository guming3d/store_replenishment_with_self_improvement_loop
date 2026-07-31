import { useEffect, type CSSProperties, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { createRoot } from 'react-dom/client';
import { Theme } from '@radix-ui/themes';
import { IconX, IconAlertTriangle, IconInfoCircle, IconCircleCheck, IconAlertCircle } from '@tabler/icons-react';
import Button from './Button';
import message from './message';
import { cn, currentAppearance } from './utils';

interface ButtonProps {
  danger?: boolean;
  disabled?: boolean;
}

interface ModalProps {
  open?: boolean;
  title?: ReactNode;
  children?: ReactNode;
  okText?: ReactNode;
  cancelText?: ReactNode;
  confirmLoading?: boolean;
  onOk?: () => void;
  onCancel?: () => void;
  footer?: ReactNode | null;
  width?: number | string;
  closable?: boolean;
  okButtonProps?: ButtonProps;
  showCancel?: boolean;
  icon?: ReactNode;
  className?: string;
  style?: CSSProperties;
}

function Dialog({
  open,
  title,
  children,
  okText = 'OK',
  cancelText = 'Cancel',
  confirmLoading,
  onOk,
  onCancel,
  footer,
  width = 520,
  closable = true,
  okButtonProps,
  showCancel = true,
  icon,
  className,
  style,
}: ModalProps) {
  useEffect(() => {
    if (!open) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel?.();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open, onCancel]);

  if (!open) return null;

  const defaultFooter = (
    <>
      {showCancel && (
        <Button onClick={onCancel}>{cancelText}</Button>
      )}
      <Button type="primary" danger={okButtonProps?.danger} disabled={okButtonProps?.disabled} loading={confirmLoading} onClick={onOk}>
        {okText}
      </Button>
    </>
  );

  return createPortal(
    <Theme appearance={currentAppearance()} hasBackground={false} className="x-portal-theme">
    <div className="x-modal-root">
      <div className="x-modal-mask" onClick={onCancel} />
      <div className={cn('x-modal-panel', className)} role="dialog" aria-modal="true" style={{ width, maxWidth: 'calc(100vw - 32px)', ...style }}>
        {(title != null || closable) && (
          <div className="x-modal-header">
            <div className="x-modal-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {icon}
              {title}
            </div>
            {closable && (
              <button type="button" aria-label="Close" className="x-modal-close" onClick={onCancel}>
                <IconX size={18} />
              </button>
            )}
          </div>
        )}
        <div className="x-modal-body">{children}</div>
        {footer !== null && <div className="x-modal-footer">{footer ?? defaultFooter}</div>}
      </div>
    </div>
    </Theme>,
    document.body,
  );
}

type ConfirmType = 'confirm' | 'warning' | 'info' | 'error' | 'success';

interface ConfirmConfig {
  title?: ReactNode;
  content?: ReactNode;
  okText?: ReactNode;
  cancelText?: ReactNode;
  okButtonProps?: ButtonProps;
  onOk?: () => void | Promise<unknown>;
  onCancel?: () => void;
  width?: number | string;
}

function confirmIcon(type: ConfirmType): ReactNode {
  switch (type) {
    case 'warning':
      return <IconAlertTriangle size={22} color="var(--amber-9)" />;
    case 'error':
      return <IconAlertCircle size={22} color="var(--red-9)" />;
    case 'success':
      return <IconCircleCheck size={22} color="var(--green-9)" />;
    case 'info':
      return <IconInfoCircle size={22} color="var(--blue-9)" />;
    default:
      return <IconAlertTriangle size={22} color="var(--amber-9)" />;
  }
}

function spawn(type: ConfirmType, config: ConfirmConfig) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);

  const destroy = () => {
    root.unmount();
    container.remove();
  };

  const render = (loading: boolean) => {
    root.render(
      <Theme appearance={currentAppearance()}>
        <Dialog
          open
          title={config.title}
          icon={confirmIcon(type)}
          okText={config.okText}
          cancelText={config.cancelText}
          okButtonProps={config.okButtonProps}
          confirmLoading={loading}
          showCancel={type === 'confirm'}
          closable={false}
          onCancel={() => {
            config.onCancel?.();
            destroy();
          }}
          onOk={async () => {
            const result = config.onOk?.();
            if (result && typeof (result as Promise<unknown>).then === 'function') {
              render(true);
              try {
                await result;
                destroy();
              } catch (error) {
                render(false);
                message.error(error instanceof Error ? error.message : 'Operation failed');
              }
            } else {
              destroy();
            }
          }}
          width={config.width}
        >
          {config.content}
        </Dialog>
      </Theme>,
    );
  };

  render(false);
  return { destroy };
}

type ModalComponent = typeof Dialog & {
  confirm: (config: ConfirmConfig) => { destroy: () => void };
  warning: (config: ConfirmConfig) => { destroy: () => void };
  info: (config: ConfirmConfig) => { destroy: () => void };
  error: (config: ConfirmConfig) => { destroy: () => void };
  success: (config: ConfirmConfig) => { destroy: () => void };
};

const Modal = Dialog as ModalComponent;
Modal.confirm = (config) => spawn('confirm', config);
Modal.warning = (config) => spawn('warning', config);
Modal.info = (config) => spawn('info', config);
Modal.error = (config) => spawn('error', config);
Modal.success = (config) => spawn('success', config);

export default Modal;
export { Modal };
