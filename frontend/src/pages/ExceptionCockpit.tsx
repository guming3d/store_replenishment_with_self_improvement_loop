import { ArrowDownOutlined, ArrowUpOutlined, ReloadOutlined } from '@/components/ui/icons';
import { Button, Card, Space, Table, Tag, Typography, message } from '@/components/ui';
import type { ColumnsType } from '@/components/ui';
import { useEffect, useState } from 'react';
import { fetchExceptions } from '../api';
import { useI18n } from '../i18n';
import type { ExceptionItem, Scenario } from '../types';

const { Text, Title } = Typography;

export default function ExceptionCockpit() {
  const { t, scenarioLabel } = useI18n();
  const [items, setItems] = useState<ExceptionItem[]>([]);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const data = await fetchExceptions();
      setItems(data);
    } catch (error) {
      setItems([]);
      message.error(t('ex.loadFailed', { reason: error instanceof Error ? error.message : '' }));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const mockAdjust = (record: ExceptionItem, direction: 'up' | 'down') => {
    setItems((current) => current.map((item) => item.trace_id === record.trace_id ? { ...item, chosen_qty: Math.max(0, item.chosen_qty + (direction === 'up' ? 1 : -1)) } : item));
    message.success(t(direction === 'up' ? 'ex.adjustedUp' : 'ex.adjustedDown', { sku: record.sku }));
  };

  const columns: ColumnsType<ExceptionItem> = [
    { title: t('ex.colShop'), dataIndex: 'shop' },
    { title: 'SKU', dataIndex: 'sku' },
    { title: t('ex.colScenario'), dataIndex: 'scenario', render: (scenario: Scenario) => <Tag>{scenarioLabel(scenario)}</Tag> },
    { title: t('ex.colChosen'), dataIndex: 'chosen_qty', align: 'right' },
    { title: t('ex.colTarget'), dataIndex: 'target_stock', align: 'right' },
    { title: t('ex.colType'), dataIndex: 'override_type', render: (type: ExceptionItem['override_type']) => <Tag color={type === 'high' ? 'red' : 'orange'}>{type === 'high' ? t('ex.high') : t('ex.low')}</Tag> },
    { title: t('ex.colReason'), dataIndex: 'reason' },
    { title: t('ex.colAction'), dataIndex: 'suggested_action' },
    { title: t('ex.colOps'), fixed: 'right', render: (_, record) => <Space><Button size="small" icon={<ArrowUpOutlined />} onClick={() => mockAdjust(record, 'up')}>{t('ex.up')}</Button><Button size="small" icon={<ArrowDownOutlined />} onClick={() => mockAdjust(record, 'down')}>{t('ex.down')}</Button></Space> },
  ];

  return (
    <Space direction="vertical" size="large" className="page-stack">
      <Card><Space className="toolbar" wrap><div><Title level={3}>{t('ex.title')}</Title><Text type="secondary">{t('ex.subtitle')}</Text></div><Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>{t('ex.refresh')}</Button></Space></Card>
      <Card title={t('ex.list')}><Table rowKey="trace_id" loading={loading} columns={columns} dataSource={items} scroll={{ x: 1100 }} pagination={{ pageSize: 8 }} /></Card>
    </Space>
  );
}
