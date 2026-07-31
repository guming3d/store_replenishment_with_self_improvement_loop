import { isValidElement, type ReactNode } from 'react';
import { Tooltip as RTooltip } from '@radix-ui/themes';

type Placement =
  | 'top' | 'bottom' | 'left' | 'right'
  | 'topLeft' | 'topRight' | 'bottomLeft' | 'bottomRight'
  | 'leftTop' | 'leftBottom' | 'rightTop' | 'rightBottom';

interface TooltipProps {
  title?: ReactNode;
  placement?: Placement;
  children: ReactNode;
}

function side(placement?: Placement): 'top' | 'bottom' | 'left' | 'right' {
  if (!placement) return 'top';
  if (placement.startsWith('bottom')) return 'bottom';
  if (placement.startsWith('left')) return 'left';
  if (placement.startsWith('right')) return 'right';
  return 'top';
}

export default function Tooltip({ title, placement, children }: TooltipProps) {
  if (title == null || title === '') return <>{children}</>;
  const trigger = isValidElement(children) ? children : <span>{children}</span>;
  return (
    <RTooltip content={title} side={side(placement)}>
      {trigger}
    </RTooltip>
  );
}

export { Tooltip };
