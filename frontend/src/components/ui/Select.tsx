import { useMemo, useState, type CSSProperties, type ReactNode } from 'react';
import { Popover, TextField, ScrollArea, Flex, Text } from '@radix-ui/themes';
import { IconChevronDown, IconX, IconSearch } from '@tabler/icons-react';
import { Spinner } from '@radix-ui/themes';

export interface SelectOption {
  value: string;
  label: ReactNode;
  disabled?: boolean;
}

interface SelectProps {
  value?: string;
  onChange?: (value: string | undefined) => void;
  options?: SelectOption[];
  placeholder?: ReactNode;
  allowClear?: boolean;
  showSearch?: boolean;
  loading?: boolean;
  disabled?: boolean;
  prefix?: ReactNode;
  suffixIcon?: ReactNode;
  size?: 'small' | 'middle' | 'large';
  className?: string;
  style?: CSSProperties;
}

const HEIGHT: Record<string, number> = { small: 28, middle: 34, large: 40 };

export default function Select({
  value,
  onChange,
  options = [],
  placeholder,
  allowClear,
  showSearch,
  loading,
  disabled,
  prefix,
  suffixIcon,
  size = 'middle',
  className,
  style,
}: SelectProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');

  const selected = useMemo(() => options.find((o) => o.value === value), [options, value]);

  const filtered = useMemo(() => {
    if (!showSearch || !query.trim()) return options;
    const q = query.trim().toLowerCase();
    return options.filter((o) => String(o.label ?? o.value).toLowerCase().includes(q));
  }, [options, showSearch, query]);

  return (
    <Popover.Root open={open} onOpenChange={(next) => { if (!disabled) { setOpen(next); if (next) setQuery(''); } }}>
      <Popover.Trigger disabled={disabled}>
        <button
          type="button"
          className={`x-select-trigger ${className ?? ''}`}
          disabled={disabled}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            width: '100%',
            minHeight: HEIGHT[size],
            padding: '0 8px',
            borderRadius: 8,
            border: '1px solid var(--border)',
            background: 'var(--surface)',
            cursor: disabled ? 'not-allowed' : 'pointer',
            color: selected ? 'var(--text-primary)' : 'var(--text-disabled)',
            fontSize: 14,
            ...style,
          }}
        >
          {prefix && <span style={{ display: 'inline-flex', color: 'var(--text-disabled)' }}>{prefix}</span>}
          <span style={{ flex: 1, textAlign: 'left', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {selected ? selected.label : placeholder}
          </span>
          {loading && <Spinner size="1" />}
          {allowClear && value != null && !loading && (
            <span
              role="button"
              aria-label="clear"
              onClick={(e) => {
                e.stopPropagation();
                onChange?.(undefined);
              }}
              style={{ display: 'inline-flex', color: 'var(--text-disabled)' }}
            >
              <IconX size={14} />
            </span>
          )}
          {suffixIcon ?? <IconChevronDown size={15} color="var(--text-disabled)" />}
        </button>
      </Popover.Trigger>
      <Popover.Content size="1" style={{ width: 'var(--radix-popover-trigger-width)', minWidth: 180, padding: 4 }}>
        {showSearch && (
          <TextField.Root
            size="1"
            autoFocus
            placeholder="Search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{ marginBottom: 4 }}
          >
            <TextField.Slot><IconSearch size={14} /></TextField.Slot>
          </TextField.Root>
        )}
        <ScrollArea type="auto" scrollbars="vertical" style={{ maxHeight: 260 }}>
          <Flex direction="column" gap="1">
            {filtered.length === 0 && (
              <Text size="1" color="gray" style={{ padding: '8px 10px' }}>No options</Text>
            )}
            {filtered.map((option) => {
              const isSelected = option.value === value;
              return (
                <button
                  key={option.value}
                  type="button"
                  disabled={option.disabled}
                  onClick={() => {
                    onChange?.(option.value);
                    setOpen(false);
                  }}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    width: '100%',
                    textAlign: 'left',
                    padding: '6px 10px',
                    borderRadius: 6,
                    border: 'none',
                    cursor: option.disabled ? 'not-allowed' : 'pointer',
                    background: isSelected ? 'var(--surface-selected)' : 'transparent',
                    color: option.disabled ? 'var(--text-disabled)' : 'var(--text-primary)',
                    fontSize: 14,
                  }}
                  onMouseEnter={(e) => {
                    if (!isSelected && !option.disabled) e.currentTarget.style.background = 'var(--surface-hover)';
                  }}
                  onMouseLeave={(e) => {
                    if (!isSelected) e.currentTarget.style.background = 'transparent';
                  }}
                >
                  {option.label}
                </button>
              );
            })}
          </Flex>
        </ScrollArea>
      </Popover.Content>
    </Popover.Root>
  );
}

export { Select };
