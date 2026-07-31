import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined,
  ReloadOutlined,
  SyncOutlined,
} from '@/components/ui/icons';
import { Alert, Button, Card, Empty, Input, Select, Space, Statistic, Table, Tag, Typography } from '@/components/ui';
import type { ColumnsType, TablePaginationConfig } from '@/components/ui';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { fetchAttributionCases } from '../api';
import { useStoreContext } from '../App';
import { AttributionStatusTag } from '../components/AttributionStatusTag';
import { useI18n } from '../i18n';
import type { AttributionCaseFilters, AttributionCaseStatus, AttributionCaseSummary } from '../types';

const { Text, Title } = Typography;
const ACTIVE = new Set<AttributionCaseStatus>(['QUEUED', 'RUNNING']);

const signed = (value: number) => `${value > 0 ? '+' : ''}${value}`;

export default function AttributionCases() {
  const { shops, skus, selectedShop, selectedSku } = useStoreContext();
  const { t, lang } = useI18n();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const seeded = useRef(false);
  const [filters, setFilters] = useState<AttributionCaseFilters>({
    page: 1,
    page_size: 10,
    job_id: searchParams.get('job_id') ?? undefined,
  });
  const [items, setItems] = useState<AttributionCaseSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const [updatedAt, setUpdatedAt] = useState<Date>();

  useEffect(() => {
    if (seeded.current) return;
    seeded.current = true;
    setFilters((current) => ({
      ...current,
      shop_code: current.shop_code ?? selectedShop,
      goods_code: current.goods_code ?? selectedSku,
    }));
  }, [selectedShop, selectedSku]);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const result = await fetchAttributionCases(filters);
      setItems(result.items);
      setTotal(result.total);
      setUpdatedAt(new Date());
      setError(undefined);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : t('attr.loadFailed'));
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [filters, t]);

  useEffect(() => {
    void load();
  }, [load]);

  const hasActive = items.some((item) => ACTIVE.has(item.status));
  useEffect(() => {
    if (!hasActive) return undefined;
    const timer = window.setInterval(() => {
      if (!document.hidden) void load(true);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [hasActive, load]);

  const metrics = useMemo(() => ({
    running: items.filter((item) => ACTIVE.has(item.status)).length,
    review: items.filter((item) => item.status === 'NEEDS_REVIEW' || item.status === 'CHANGES_REQUESTED').length,
    complete: items.filter((item) => item.status === 'HUMAN_APPROVED').length,
    failed: items.filter((item) => item.status === 'FAILED').length,
  }), [items]);

  const setFilter = <K extends keyof AttributionCaseFilters>(key: K, value: AttributionCaseFilters[K]) => {
    setFilters((current) => ({ ...current, [key]: value || undefined, page: 1 }));
    if (key === 'job_id') {
      const next = new URLSearchParams(searchParams);
      if (value) next.set('job_id', String(value)); else next.delete('job_id');
      setSearchParams(next, { replace: true });
    }
  };

  const columns: ColumnsType<AttributionCaseSummary> = [
    {
      title: t('attr.colUpdated'),
      dataIndex: 'updated_at',
      width: 170,
      render: (value: string) => new Date(value).toLocaleString(),
    },
    {
      title: t('attr.colStoreSku'),
      key: 'storeSku',
      render: (_, item) => (
        <Space direction="vertical" size={0}>
          <Text strong>{item.shop_name ?? item.shop_code}</Text>
          <Text type="secondary">{item.goods_name ?? item.goods_code} · {item.goods_code}</Text>
        </Space>
      ),
    },
    {
      title: t('attr.colDecisionDate'),
      dataIndex: 'decision_date',
      width: 120,
    },
    {
      title: t('attr.colGap'),
      key: 'gap',
      align: 'right',
      width: 150,
      render: (_, item) => (
        <Space direction="vertical" size={0} align="end">
          <Text>{item.recommended_qty} → <Text strong>{item.override_qty}</Text></Text>
          <Text type={item.signed_gap < 0 ? 'danger' : 'success'}>{signed(item.signed_gap)}</Text>
        </Space>
      ),
    },
    {
      title: t('attr.colStatus'),
      dataIndex: 'status',
      width: 150,
      render: (status: AttributionCaseStatus) => <AttributionStatusTag status={status} lang={lang} />,
    },
    {
      title: t('attr.colCoverage'),
      dataIndex: 'coverage_ratio',
      align: 'right',
      width: 95,
      render: (value?: number | null) => value == null ? '-' : `${Math.round(value * 100)}%`,
    },
    {
      title: t('attr.colAction'),
      key: 'action',
      fixed: 'right',
      width: 100,
      render: (_, item) => (
        <Button type="link" onClick={() => navigate(`/attribution/${item.case_id}`)}>
          {t('attr.view')}
        </Button>
      ),
    },
  ];

  const pagination: TablePaginationConfig = {
    current: filters.page ?? 1,
    pageSize: filters.page_size ?? 10,
    total,
    showSizeChanger: true,
    onChange: (page, pageSize) => setFilters((current) => ({ ...current, page, page_size: pageSize })),
  };

  return (
    <Space direction="vertical" size="large" className="page-stack">
      <Card>
        <Space className="toolbar" align="start" wrap>
          <div>
            <Title level={3}>{t('attr.title')}</Title>
            <Text type="secondary">{t('attr.subtitle')}</Text>
          </div>
          <Space>
            {hasActive && <Tag icon={<SyncOutlined spin />} color="processing">{t('attr.live')}</Tag>}
            {updatedAt && <Text type="secondary">{t('attr.updated', { time: updatedAt.toLocaleTimeString() })}</Text>}
            <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>{t('attr.refresh')}</Button>
          </Space>
        </Space>
      </Card>

      {error && <Alert showIcon type="error" message={t('attr.reconnect')} description={error} />}

      <div className="metric-grid">
        <Card className="kpi kpi-primary"><span className="kpi-icon"><SyncOutlined /></span><Statistic title={t('attr.metricRunning')} value={metrics.running} /></Card>
        <Card className="kpi kpi-warning"><span className="kpi-icon"><ExclamationCircleOutlined /></span><Statistic title={t('attr.metricReview')} value={metrics.review} /></Card>
        <Card className="kpi"><span className="kpi-icon"><CheckCircleOutlined /></span><Statistic title={t('attr.metricApproved')} value={metrics.complete} /></Card>
        <Card className="kpi kpi-danger"><span className="kpi-icon"><CloseCircleOutlined /></span><Statistic title={t('attr.metricFailed')} value={metrics.failed} /></Card>
      </div>

      <Card>
        <Space wrap className="attribution-filters">
          <Select
            allowClear
            style={{ width: 190 }}
            placeholder={t('attr.filterStatus')}
            value={filters.status}
            onChange={(value) => setFilter('status', value as AttributionCaseStatus | undefined)}
            options={[
              'QUEUED', 'RUNNING', 'NEEDS_REVIEW', 'HUMAN_APPROVED',
              'CHANGES_REQUESTED', 'FAILED', 'CANCELLED', 'SUPERSEDED',
            ].map((value) => ({ value, label: value }))}
          />
          <Select
            allowClear
            showSearch
            style={{ width: 230 }}
            placeholder={t('attr.filterStore')}
            value={filters.shop_code}
            onChange={(value) => setFilter('shop_code', value)}
            options={shops.map((shop) => ({ value: shop.shop_code, label: `${shop.shop_name} · ${shop.shop_code}` }))}
          />
          <Select
            allowClear
            showSearch
            style={{ width: 250 }}
            placeholder={t('attr.filterSku')}
            value={filters.goods_code}
            onChange={(value) => setFilter('goods_code', value)}
            options={skus.map((sku) => ({ value: sku.goods_code, label: `${sku.goods_name} · ${sku.goods_code}` }))}
          />
          <Select
            allowClear
            style={{ width: 150 }}
            placeholder={t('attr.filterDirection')}
            value={filters.direction}
            onChange={(value) => setFilter('direction', value as 'UP' | 'DOWN' | undefined)}
            options={[
              { value: 'UP', label: t('attr.directionUp') },
              { value: 'DOWN', label: t('attr.directionDown') },
            ]}
          />
          <Input
            allowClear
            style={{ width: 240 }}
            placeholder={t('attr.filterJob')}
            value={filters.job_id}
            onChange={(event) => setFilter('job_id', event.target.value)}
          />
        </Space>
      </Card>

      <Card title={t('attr.caseList')} extra={<Tag>{total}</Tag>}>
        <Table
          rowKey="case_id"
          loading={loading}
          columns={columns}
          dataSource={items}
          pagination={pagination}
          scroll={{ x: 1150 }}
          onRow={(item) => ({ onDoubleClick: () => navigate(`/attribution/${item.case_id}`) })}
          locale={{ emptyText: <Empty description={t('attr.empty')} /> }}
        />
      </Card>
    </Space>
  );
}
