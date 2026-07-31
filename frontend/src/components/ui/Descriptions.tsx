import { Children, isValidElement, type CSSProperties, type ReactElement, type ReactNode } from 'react';
import { cn } from './utils';

interface ItemProps {
  label?: ReactNode;
  span?: number;
  children?: ReactNode;
}

function Item(_props: ItemProps) {
  void _props;
  return null;
}

interface DescriptionsProps {
  children?: ReactNode;
  column?: number;
  bordered?: boolean;
  size?: 'small' | 'default';
  title?: ReactNode;
  layout?: 'horizontal' | 'vertical';
  className?: string;
  style?: CSSProperties;
}

function Descriptions({ children, column = 3, bordered, size = 'default', title, className, style }: DescriptionsProps) {
  const items = Children.toArray(children).filter(
    (child): child is ReactElement<ItemProps> => isValidElement(child),
  );

  return (
    <div className={cn('x-descriptions', bordered && 'x-descriptions-bordered', size === 'small' && 'x-descriptions-small', className)} style={style}>
      {title != null && <div className="x-descriptions-title">{title}</div>}
      <div
        className="x-descriptions-view"
        style={{ display: 'grid', gridTemplateColumns: `repeat(${column}, minmax(0, 1fr))` }}
      >
        {items.map((item, index) => {
          const span = Math.min(item.props.span ?? 1, column);
          return (
            <div
              key={index}
              className="x-descriptions-item"
              style={{ gridColumn: `span ${span} / span ${span}` }}
            >
              {item.props.label != null && <div className="x-descriptions-label">{item.props.label}</div>}
              <div className="x-descriptions-content">{item.props.children}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

Descriptions.Item = Item;

export default Descriptions;
export { Descriptions };
