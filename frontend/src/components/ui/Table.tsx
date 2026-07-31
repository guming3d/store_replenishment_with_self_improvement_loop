import {
  Fragment,
  useMemo,
  useState,
  type CSSProperties,
  type Key,
  type ReactNode,
} from 'react';
import { Table as RTable, Popover, Flex, Text, ScrollArea } from '@radix-ui/themes';
import {
  IconChevronRight,
  IconChevronDown,
  IconSelector,
  IconSortAscending,
  IconSortDescending,
  IconFilter,
} from '@tabler/icons-react';
import Spin from './Spin';
import Empty from './Empty';
import Checkbox from './Checkbox';
import Button from './Button';

type Align = 'left' | 'right' | 'center';
type SortOrder = 'ascend' | 'descend' | null;

export interface ColumnType<T> {
  title?: ReactNode;
  dataIndex?: string;
  key?: string;
  render?: (value: any, record: T, index: number) => ReactNode;
  align?: Align;
  width?: number | string;
  fixed?: 'left' | 'right' | boolean;
  sorter?: (a: T, b: T) => number;
  defaultSortOrder?: 'ascend' | 'descend';
  filters?: { text: ReactNode; value: any }[];
  onFilter?: (value: any, record: T) => boolean;
  ellipsis?: boolean;
}

export type ColumnsType<T> = ColumnType<T>[];

export interface TablePaginationConfig {
  current?: number;
  pageSize?: number;
  total?: number;
  showSizeChanger?: boolean;
  pageSizeOptions?: number[];
  onChange?: (page: number, pageSize: number) => void;
}

interface ExpandableConfig<T> {
  expandedRowRender: (record: T, index: number) => ReactNode;
  rowExpandable?: (record: T) => boolean;
}

interface TableProps<T> {
  columns: ColumnsType<T>;
  dataSource?: T[];
  rowKey?: string | ((record: T, index?: number) => Key);
  loading?: boolean;
  pagination?: TablePaginationConfig | false;
  expandable?: ExpandableConfig<T>;
  scroll?: { x?: number | string; y?: number | string };
  size?: 'small' | 'middle' | 'default';
  bordered?: boolean;
  locale?: { emptyText?: ReactNode };
  onRow?: (record: T, index: number) => Record<string, any>;
  rowClassName?: (record: T, index: number) => string;
  className?: string;
  style?: CSSProperties;
}

function columnKey<T>(col: ColumnType<T>, index: number): string {
  return col.key ?? col.dataIndex ?? String(index);
}

function getValue<T>(record: T, col: ColumnType<T>): any {
  if (!col.dataIndex) return undefined;
  return (record as any)[col.dataIndex];
}

function FilterDropdown<T>({
  column,
  selected,
  onApply,
}: {
  column: ColumnType<T>;
  selected: any[];
  onApply: (values: any[]) => void;
}) {
  const [local, setLocal] = useState<any[]>(selected);
  const active = selected.length > 0;
  return (
    <Popover.Root onOpenChange={(open) => { if (open) setLocal(selected); }}>
      <Popover.Trigger>
        <button
          type="button"
          aria-label="filter"
          style={{ border: 'none', background: 'none', cursor: 'pointer', display: 'inline-flex', color: active ? 'var(--brand-solid)' : 'var(--text-disabled)' }}
        >
          <IconFilter size={14} />
        </button>
      </Popover.Trigger>
      <Popover.Content size="1" style={{ minWidth: 160 }}>
        <Flex direction="column" gap="2">
          {(column.filters ?? []).map((filter, i) => (
            <Checkbox
              key={i}
              checked={local.includes(filter.value)}
              onChange={(e) =>
                setLocal((prev) => (e.target.checked ? [...prev, filter.value] : prev.filter((v) => v !== filter.value)))
              }
            >
              {filter.text}
            </Checkbox>
          ))}
          <Flex gap="2" justify="end" mt="1">
            <Popover.Close>
              <Button size="small" onClick={() => onApply([])}>Reset</Button>
            </Popover.Close>
            <Popover.Close>
              <Button size="small" type="primary" onClick={() => onApply(local)}>OK</Button>
            </Popover.Close>
          </Flex>
        </Flex>
      </Popover.Content>
    </Popover.Root>
  );
}

const SIZE: Record<string, '1' | '2' | '3'> = { small: '1', middle: '2', default: '2' };

export default function Table<T>({
  columns,
  dataSource = [],
  rowKey = 'key',
  loading,
  pagination,
  expandable,
  scroll,
  size = 'default',
  bordered,
  locale,
  onRow,
  rowClassName,
  className,
  style,
}: TableProps<T>) {
  const [sortState, setSortState] = useState<{ key: string; order: SortOrder }>(() => {
    const idx = columns.findIndex((c) => c.defaultSortOrder);
    if (idx >= 0) return { key: columnKey(columns[idx], idx), order: columns[idx].defaultSortOrder ?? null };
    return { key: '', order: null };
  });
  const cfg = useMemo<TablePaginationConfig | null>(
    () => (pagination === false ? null : pagination ?? {}),
    [pagination],
  );
  const [filterState, setFilterState] = useState<Record<string, any[]>>({});
  const [internalPage, setInternalPage] = useState(1);
  const [internalPageSize, setInternalPageSize] = useState(cfg?.pageSize ?? 10);
  const [expandedKeys, setExpandedKeys] = useState<Set<Key>>(new Set());

  const controlled = cfg?.total != null;

  const resolveKey = (record: T, index: number): Key =>
    typeof rowKey === 'function' ? rowKey(record, index) : ((record as any)[rowKey] ?? index);

  const filtered = useMemo(() => {
    let rows = dataSource;
    for (const [key, values] of Object.entries(filterState)) {
      if (!values || values.length === 0) continue;
      const idx = columns.findIndex((c, i) => columnKey(c, i) === key);
      const col = columns[idx];
      if (!col?.onFilter) continue;
      rows = rows.filter((record) => values.some((v) => col.onFilter!(v, record)));
    }
    return rows;
  }, [dataSource, filterState, columns]);

  const sorted = useMemo(() => {
    if (!sortState.order || !sortState.key) return filtered;
    const idx = columns.findIndex((c, i) => columnKey(c, i) === sortState.key);
    const col = columns[idx];
    if (!col?.sorter) return filtered;
    const copy = [...filtered];
    copy.sort((a, b) => (sortState.order === 'ascend' ? col.sorter!(a, b) : col.sorter!(b, a)));
    return copy;
  }, [filtered, sortState, columns]);

  const pageSize = cfg ? cfg.pageSize ?? internalPageSize : sorted.length;
  const current = cfg ? cfg.current ?? internalPage : 1;
  const total = controlled ? cfg!.total! : sorted.length;

  const pageRows = useMemo(() => {
    if (!cfg || controlled) return sorted;
    const start = (current - 1) * pageSize;
    return sorted.slice(start, start + pageSize);
  }, [sorted, cfg, controlled, current, pageSize]);

  const colCount = columns.length + (expandable ? 1 : 0);

  const changePage = (page: number, nextSize: number) => {
    if (cfg?.onChange) {
      cfg.onChange(page, nextSize);
    }
    setInternalPage(page);
    setInternalPageSize(nextSize);
  };

  const toggleSort = (key: string) => {
    setInternalPage(1);
    setSortState((prev) => {
      if (prev.key !== key) return { key, order: 'ascend' };
      if (prev.order === 'ascend') return { key, order: 'descend' };
      if (prev.order === 'descend') return { key, order: null };
      return { key, order: 'ascend' };
    });
  };

  const toggleExpand = (key: Key) => {
    setExpandedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const tableInner = (
    <RTable.Root size={SIZE[size]} variant={bordered ? 'surface' : 'ghost'} className={className} style={style}>
      <RTable.Header>
        <RTable.Row>
          {expandable && <RTable.ColumnHeaderCell style={{ width: 40 }} />}
          {columns.map((col, index) => {
            const key = columnKey(col, index);
            const isSorted = sortState.key === key ? sortState.order : null;
            return (
              <RTable.ColumnHeaderCell
                key={key}
                style={{ width: col.width, textAlign: col.align, whiteSpace: 'nowrap' }}
              >
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, justifyContent: col.align === 'right' ? 'flex-end' : col.align === 'center' ? 'center' : 'flex-start' }}>
                  {col.title}
                  {col.sorter && (
                    <button
                      type="button"
                      aria-label="sort"
                      onClick={() => toggleSort(key)}
                      style={{ border: 'none', background: 'none', cursor: 'pointer', display: 'inline-flex', color: isSorted ? 'var(--brand-solid)' : 'var(--text-disabled)' }}
                    >
                      {isSorted === 'ascend' ? <IconSortAscending size={14} /> : isSorted === 'descend' ? <IconSortDescending size={14} /> : <IconSelector size={14} />}
                    </button>
                  )}
                  {col.filters && (
                    <FilterDropdown
                      column={col}
                      selected={filterState[key] ?? []}
                      onApply={(values) => {
                        setInternalPage(1);
                        setFilterState((prev) => ({ ...prev, [key]: values }));
                      }}
                    />
                  )}
                </span>
              </RTable.ColumnHeaderCell>
            );
          })}
        </RTable.Row>
      </RTable.Header>
      <RTable.Body>
        {pageRows.length === 0 ? (
          <RTable.Row>
            <RTable.Cell colSpan={colCount}>
              {locale?.emptyText ?? <Empty />}
            </RTable.Cell>
          </RTable.Row>
        ) : (
          pageRows.map((record, rowIndex) => {
            const key = resolveKey(record, rowIndex);
            const rowProps = onRow?.(record, rowIndex) ?? {};
            const expanded = expandedKeys.has(key);
            const canExpand = expandable && (expandable.rowExpandable?.(record) ?? true);
            return (
              <Fragment key={key}>
                <RTable.Row className={rowClassName?.(record, rowIndex)} {...rowProps}>
                  {expandable && (
                    <RTable.Cell style={{ width: 40 }}>
                      {canExpand && (
                        <button
                          type="button"
                          aria-label={expanded ? 'collapse' : 'expand'}
                          onClick={() => toggleExpand(key)}
                          style={{ border: 'none', background: 'none', cursor: 'pointer', display: 'inline-flex', color: 'var(--text-secondary)' }}
                        >
                          {expanded ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
                        </button>
                      )}
                    </RTable.Cell>
                  )}
                  {columns.map((col, colIndex) => {
                    const value = getValue(record, col);
                    const content = col.render ? col.render(value, record, rowIndex) : (value as ReactNode);
                    return (
                      <RTable.Cell key={columnKey(col, colIndex)} style={{ textAlign: col.align, width: col.width }}>
                        {content as ReactNode}
                      </RTable.Cell>
                    );
                  })}
                </RTable.Row>
                {expandable && expanded && (
                  <RTable.Row>
                    <RTable.Cell colSpan={colCount} style={{ background: 'var(--bg-subtle)' }}>
                      {expandable.expandedRowRender(record, rowIndex)}
                    </RTable.Cell>
                  </RTable.Row>
                )}
              </Fragment>
            );
          })
        )}
      </RTable.Body>
    </RTable.Root>
  );

  const scrollWrapped = scroll?.x ? (
    <ScrollArea type="auto" scrollbars="horizontal">
      <div style={{ minWidth: scroll.x }}>{tableInner}</div>
    </ScrollArea>
  ) : (
    tableInner
  );

  return (
    <div>
      <Spin spinning={!!loading}>{scrollWrapped}</Spin>
      {pagination !== false && total > 0 && (
        <Flex align="center" justify="between" gap="3" mt="3" wrap="wrap">
          <Text size="1" color="gray">{`${total} items`}</Text>
          <Flex align="center" gap="2">
            {cfg?.showSizeChanger && (
              <select
                aria-label="Rows per page"
                className="x-table-page-size"
                value={pageSize}
                onChange={(event) => changePage(1, Number(event.target.value))}
              >
                {Array.from(new Set([...(cfg.pageSizeOptions ?? [10, 20, 50, 100]), pageSize]))
                  .sort((a, b) => a - b)
                  .map((option) => (
                    <option key={option} value={option}>{`${option} / page`}</option>
                  ))}
              </select>
            )}
            <Button size="small" disabled={current <= 1} onClick={() => changePage(current - 1, pageSize)}>Prev</Button>
            <Text size="1" color="gray">{`${current} / ${totalPages}`}</Text>
            <Button size="small" disabled={current >= totalPages} onClick={() => changePage(current + 1, pageSize)}>Next</Button>
          </Flex>
        </Flex>
      )}
    </div>
  );
}

export { Table };
