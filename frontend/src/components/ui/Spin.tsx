import type { CSSProperties, ReactNode } from 'react';
import { Spinner, Flex, Text } from '@radix-ui/themes';

interface SpinProps {
  spinning?: boolean;
  tip?: ReactNode;
  size?: 'small' | 'default' | 'large';
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
}

const SIZE: Record<string, '1' | '2' | '3'> = { small: '1', default: '2', large: '3' };

export default function Spin({ spinning = true, tip, size = 'default', children, className, style }: SpinProps) {
  if (children != null) {
    return (
      <div className={className} style={{ position: 'relative', ...style }}>
        {children}
        {spinning && (
          <Flex
            align="center"
            justify="center"
            direction="column"
            gap="2"
            style={{
              position: 'absolute',
              inset: 0,
              background: 'var(--surface-translucent)',
              zIndex: 5,
            }}
          >
            <Spinner size={SIZE[size]} />
            {tip && <Text size="2" color="gray">{tip}</Text>}
          </Flex>
        )}
      </div>
    );
  }

  if (!spinning) return null;

  return (
    <Flex align="center" justify="center" direction="column" gap="2" className={className} style={style}>
      <Spinner size={SIZE[size]} />
      {tip && <Text size="2" color="gray">{tip}</Text>}
    </Flex>
  );
}

export { Spin };
