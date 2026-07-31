import { useEffect, type CSSProperties, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Theme } from '@radix-ui/themes';
import { IconX } from '@tabler/icons-react';
import { cn, currentAppearance } from './utils';

interface DrawerProps {
  open?: boolean;
  onClose?: () => void;
  title?: ReactNode;
  extra?: ReactNode;
  footer?: ReactNode;
  width?: number | string;
  placement?: 'left' | 'right';
  destroyOnClose?: boolean;
  closable?: boolean;
  rootClassName?: string;
  className?: string;
  style?: CSSProperties;
  children?: ReactNode;
}

export default function Drawer({
  open,
  onClose,
  title,
  extra,
  footer,
  width = 378,
  placement = 'right',
  closable = true,
  rootClassName,
  className,
  style,
  children,
}: DrawerProps) {
  useEffect(() => {
    if (!open) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose?.();
    };
    document.addEventListener('keydown', handler);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', handler);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <Theme appearance={currentAppearance()} hasBackground={false} className="x-portal-theme">
    <div className={cn('x-drawer-root', rootClassName)}>
      <div className="x-drawer-mask" onClick={onClose} />
      <div
        className={cn('x-drawer-panel', `x-drawer-${placement}`, className)}
        role="dialog"
        aria-modal="true"
        style={{ width, maxWidth: '100vw', ...style }}
      >
        {(title != null || closable || extra != null) && (
          <div className="x-drawer-header">
            <div className="x-drawer-title">{title}</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              {extra}
              {closable && (
                <button type="button" aria-label="Close" className="x-drawer-close" onClick={onClose}>
                  <IconX size={18} />
                </button>
              )}
            </div>
          </div>
        )}
        <div className="x-drawer-body">{children}</div>
        {footer != null && <div className="x-drawer-footer">{footer}</div>}
      </div>
    </div>
    </Theme>,
    document.body,
  );
}

export { Drawer };
