import type { CSSProperties, ReactNode } from 'react';
import { Badge } from '@radix-ui/themes';
import { mapColor } from './utils';

interface TagProps {
  children?: ReactNode;
  color?: string;
  icon?: ReactNode;
  closable?: boolean;
  onClose?: () => void;
  bordered?: boolean;
  className?: string;
  style?: CSSProperties;
}

export default function Tag({ children, color, icon, closable, onClose, className, style }: TagProps) {
  return (
    <Badge
      color={mapColor(color) as any}
      variant="soft"
      radius="full"
      className={className}
      style={{ display: 'inline-flex', alignItems: 'center', gap: 4, ...style }}
    >
      {icon}
      {children}
      {closable && (
        <button
          type="button"
          aria-label="close"
          onClick={onClose}
          style={{ cursor: 'pointer', border: 'none', background: 'none', padding: 0, lineHeight: 1 }}
        >
          ×
        </button>
      )}
    </Badge>
  );
}

export { Tag };
