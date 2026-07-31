import { DeleteOutlined, ExportOutlined, EyeOutlined, ReloadOutlined, RobotOutlined, ThunderboltOutlined } from '@/components/ui/icons';
import { Button, Card, Descriptions, Drawer, Empty, Modal, Space, Table, Tag, Typography, message } from '@/components/ui';
import type { ColumnsType } from '@/components/ui';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { clearRuns, fetchRunDetail, fetchRuns } from '../api';
import { useStoreContext } from '../App';
import { AttributionStatusTag, RunGateStatusTag } from '../components/AttributionStatusTag';
import ReplenishmentResultDetails from '../components/ReplenishmentResultDetails';
import { useI18n } from '../i18n';
import type { ReplenishmentResult, RunDetail, RunSummary, Scenario } from '../types';

const { Text, Title } = Typography;

const scenarioColors: Record<Scenario, string> = {
  standard: 'blue', fresh: 'green', longtail: 'purple', new: 'cyan', promo: 'orange', holiday: 'magenta', season: 'lime', stockout: 'red',
};

const fmtTs = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
};

export default function RunHistory() {
  const { setBatchResults, setSelectedShop } = useStoreContext();
  const { t, lang, scenarioLabel } = useI18n();
  const navigate = useNavigate();
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      setRuns(await fetchRuns());
    } catch (error) {
      setRuns([]);
      message.error(t('hist.loadFailed', { reason: error instanceof Error ? error.message : '' }));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const openDetail = async (runId: string) => {
    setDrawerOpen(true);
    setDetailLoading(true);
    setDetail(null);
    try {
      setDetail(await fetchRunDetail(runId));
    } catch (error) {
      message.error(t('hist.detailFailed', { reason: error instanceof Error ? error.message : '' }));
      setDrawerOpen(false);
    } finally {
      setDetailLoading(false);
    }
  };

  const loadIntoSuggestions = () => {
    if (!detail) return;
    setBatchResults(detail.results);
    if (detail.shop_code) setSelectedShop(detail.shop_code);
    message.success(t('hist.loadedToSuggestions', { count: detail.results.length }));
    setDrawerOpen(false);
    navigate('/suggestions');
  };

  const confirmClear = () => {
    Modal.confirm({
      title: t('hist.clearConfirmTitle'),
      content: t('hist.clearConfirmBody'),
      okText: t('hist.clear'),
      okButtonProps: { danger: true },
      cancelText: t('hist.cancel'),
      onOk: async () => {
        const ok = await clearRuns();
        if (ok) {
          message.success(t('hist.cleared'));
          void load();
        } else {
          message.error(t('hist.clearFailed'));
        }
      },
    });
  };

  const engineTag = (engine: 'algo' | 'agent') =>
    engine === 'agent'
      ? <Tag icon={<RobotOutlined />} color="geekblue">{t('sug.agentTag')}</Tag>
      : <Tag icon={<ThunderboltOutlined />} color="blue">{t('sug.engineTag')}</Tag>;

  const columns: ColumnsType<RunSummary> = [
    { title: t('hist.colTime'), dataIndex: 'ts', render: (ts: string) => <Text>{fmtTs(ts)}</Text>, defaultSortOrder: 'descend', sorter: (a, b) => a.ts.localeCompare(b.ts) },
    { title: t('hist.colEngine'), dataIndex: 'engine', render: engineTag, filters: [{ text: t('sug.engineTag'), value: 'algo' }, { text: t('sug.agentTag'), value: 'agent' }], onFilter: (v, r) => r.engine === v },
    { title: t('hist.colKind'), dataIndex: 'kind', render: (kind: string) => <Tag>{kind === 'single' ? t('hist.kindSingle') : t('hist.kindBatch')}</Tag> },
    {
      title: t('hist.colStatus'),
      dataIndex: 'status',
      render: (status: RunSummary['status']) => status ? <RunGateStatusTag status={status} lang={lang} /> : '-',
    },
    { title: t('hist.colShop'), dataIndex: 'shop_name', render: (name: string, r) => <Space direction="vertical" size={0}><Text strong>{name}</Text><Text type="secondary">{r.shop_code}</Text></Space> },
    { title: t('hist.colCount'), dataIndex: 'count', align: 'right' },
    { title: t('hist.colExceptions'), dataIndex: 'exception_count', align: 'right', render: (n: number) => <Text style={{ color: n ? 'var(--danger-text)' : 'var(--success-text)' }}>{n}</Text> },
    { title: t('hist.colTotalQty'), dataIndex: 'total_qty', align: 'right', render: (v: number) => <Text strong>{v}</Text> },
    { title: t('hist.colOps'), key: 'ops', render: (_, r) => <Button size="small" icon={<EyeOutlined />} onClick={() => void openDetail(r.run_id)}>{t('hist.view')}</Button> },
  ];

  const resultColumns: ColumnsType<ReplenishmentResult> = useMemo(() => [
    { title: 'SKU', dataIndex: 'sku', render: (sku: string, r: ReplenishmentResult) => <Space direction="vertical" size={0}><Text strong>{sku}</Text><Text type="secondary">{r.sku_name ?? t('sug.unknownSku')}</Text></Space> },
    { title: t('sug.colScenario'), dataIndex: 'scenario', render: (s: Scenario) => <Tag color={scenarioColors[s]}>{scenarioLabel(s)}</Tag> },
    { title: t('sug.colSafety'), dataIndex: 'safety_stock', align: 'right' },
    { title: t('sug.colTarget'), dataIndex: 'target_stock', align: 'right' },
    { title: t('sug.colChosen'), dataIndex: 'chosen_qty', align: 'right', render: (v: number) => <Text strong>{v}</Text> },
    { title: t('sug.colException'), dataIndex: 'exception', render: (e: boolean) => <Tag color={e ? 'red' : 'green'}>{e ? t('sug.needReview') : t('sug.normal')}</Tag> },
    {
      title: t('hist.attribution'),
      key: 'attribution',
      render: (_, item) => item.attribution_case_id ? (
        <Button
          type="link"
          size="small"
          onClick={() => {
            setDrawerOpen(false);
            navigate(`/attribution/${item.attribution_case_id}`);
          }}
        >
          {item.attribution_status && <AttributionStatusTag status={item.attribution_status} lang={lang} />} {t('hist.viewCase')}
        </Button>
      ) : '-',
    },
  ], [t, lang, navigate, scenarioLabel]);

  return (
    <Space direction="vertical" size="large" className="page-stack">
      <Card>
        <Space className="toolbar" align="start" wrap>
          <div>
            <Title level={3}>{t('hist.title')}</Title>
            <Text type="secondary">{t('hist.subtitle')}</Text>
          </div>
          <Space wrap>
            <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>{t('hist.refresh')}</Button>
            <Button danger icon={<DeleteOutlined />} disabled={!runs.length} onClick={confirmClear}>{t('hist.clear')}</Button>
          </Space>
        </Space>
      </Card>

      <Card title={t('hist.cardRuns')} extra={<Tag>{t('hist.runCount', { count: runs.length })}</Tag>}>
        <Table
          rowKey="run_id"
          loading={loading}
          columns={columns}
          dataSource={runs}
          locale={{ emptyText: <Empty description={t('hist.empty')} /> }}
          pagination={{ pageSize: 8 }}
        />
      </Card>

      <Drawer
        width={880}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={detail ? `${detail.shop_name} · ${fmtTs(detail.ts)}` : t('hist.detailTitle')}
        extra={detail ? <Button type="primary" icon={<ExportOutlined />} onClick={loadIntoSuggestions}>{t('hist.loadToSuggestions')}</Button> : null}
      >
        {detail && (
          <Descriptions size="small" column={2} bordered style={{ marginBottom: 16 }}>
            <Descriptions.Item label={t('hist.colEngine')}>{engineTag(detail.engine)}</Descriptions.Item>
            <Descriptions.Item label={t('hist.colKind')}>{detail.kind === 'single' ? t('hist.kindSingle') : t('hist.kindBatch')}</Descriptions.Item>
            <Descriptions.Item label={t('hist.colCount')}>{detail.count}</Descriptions.Item>
            <Descriptions.Item label={t('hist.colExceptions')}>{detail.exception_count}</Descriptions.Item>
            <Descriptions.Item label={t('hist.colTotalQty')} span={2}>{detail.total_qty}</Descriptions.Item>
          </Descriptions>
        )}
        <Table
          rowKey="trace_id"
          size="small"
          loading={detailLoading}
          columns={resultColumns}
          dataSource={detail?.results ?? []}
          locale={{ emptyText: <Empty description={t('hist.empty')} /> }}
          expandable={{
            expandedRowRender: (record) => <ReplenishmentResultDetails record={record} />,
          }}
          pagination={{ pageSize: 10 }}
        />
      </Drawer>
    </Space>
  );
}
