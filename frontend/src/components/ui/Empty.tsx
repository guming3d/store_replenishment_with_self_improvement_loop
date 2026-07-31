import type { CSSProperties, ReactNode } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { IconInbox } from '@tabler/icons-react';

interface EmptyProps {
  description?: ReactNode;
  image?: ReactNode;
  className?: string;
  style?: CSSProperties;
  children?: ReactNode;
}

function Empty({ description = 'No data', image, className, style, children }: EmptyProps) {
  return (
    <Flex direction="column" align="center" justify="center" gap="2" className={className} style={{ padding: 24, ...style }}>
      {image ?? <IconInbox size={40} color="var(--text-disabled)" stroke={1.5} />}
      <Text size="2" color="gray">{description}</Text>
      {children}
    </Flex>
  );
}

Empty.PRESENTED_IMAGE_SIMPLE = <IconInbox size={40} color="var(--text-disabled)" stroke={1.5} />;
Empty.PRESENTED_IMAGE_DEFAULT = <IconInbox size={40} color="var(--text-disabled)" stroke={1.5} />;

export default Empty;
export { Empty };
