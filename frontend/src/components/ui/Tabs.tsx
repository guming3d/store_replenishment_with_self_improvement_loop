import { useState, type CSSProperties, type ReactNode } from 'react';
import { Tabs as RTabs } from '@radix-ui/themes';

export interface TabItem {
  key: string;
  label: ReactNode;
  children?: ReactNode;
}

interface TabsProps {
  items: TabItem[];
  activeKey?: string;
  defaultActiveKey?: string;
  onChange?: (key: string) => void;
  className?: string;
  style?: CSSProperties;
}

export default function Tabs({ items, activeKey, defaultActiveKey, onChange, className, style }: TabsProps) {
  const [internal, setInternal] = useState(defaultActiveKey ?? items[0]?.key);
  const value = activeKey ?? internal;

  return (
    <RTabs.Root
      value={value}
      onValueChange={(next) => {
        setInternal(next);
        onChange?.(next);
      }}
      className={className}
      style={style}
    >
      <RTabs.List>
        {items.map((item) => (
          <RTabs.Trigger key={item.key} value={item.key}>
            {item.label}
          </RTabs.Trigger>
        ))}
      </RTabs.List>
      {items.map((item) => (
        <RTabs.Content key={item.key} value={item.key} style={{ paddingTop: 16 }}>
          {item.children}
        </RTabs.Content>
      ))}
    </RTabs.Root>
  );
}

export { Tabs };
