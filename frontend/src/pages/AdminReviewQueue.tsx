import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { IconAlertTriangle, IconRefresh } from '@tabler/icons-react';
import {
  Alert, Button, Card, Checkbox, Input, Modal, Select, Space, Spin, Table, Tag, Typography, message,
} from '../components/ui';
import { bulkDismissAttributionCases, fetchAdminReviewQueue } from '../api';
import { AttributionStatusTag, caseStatusLabel } from '../components/AttributionStatusTag';
import { useI18n } from '../i18n';
import type { AdminReviewQueueItem, AttributionCaseStatus } from '../types';

const { Paragraph, Text, Title } = Typography;

const QUEUE_STATES = ['NEEDS_REVIEW', 'CHANGES_REQUESTED', 'FAILED'];

export default function AdminReviewQueuePage() {
  const { t, lang } = useI18n();
  const [items, setItems] = useState<AdminReviewQueueItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [statusFilter, setStatusFilter] = useState<string>();
  const [selected, setSelected] = useState<string[]>([]);
  const [dismissOpen, setDismissOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(undefined);
    try {
      const queue = await fetchAdminReviewQueue({ status: statusFilter, page_size: 100 });
      setItems(queue.items);
      setSelected((current) => current.filter(
        (caseId) => queue.items.some((item) => item.case_id === caseId)));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : String(loadError));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [statusFilter]);

  const selectedItems = useMemo(
    () => items.filter((item) => selected.includes(item.case_id)),
    [items, selected],
  );
  const strandedCount = selectedItems.filter((item) => item.blocks_run).length;
  const strandedRuns = useMemo(
    () => Array.from(new Set(selectedItems.filter((item) => item.blocks_run).map((item) => item.run_id))),
    [selectedItems],
  );

  const handleDismiss = async () => {
    if (!reason.trim()) return;
    setSubmitting(true);
    try {
      const result = await bulkDismissAttributionCases(
        selectedItems.map((item) => ({ case_id: item.case_id, expected_version: item.version })),
        reason.trim(),
      );
      if (result.failed_count) {
        message.warning(t('admin.dismissPartial')
          .replace('{ok}', String(result.succeeded_count))
          .replace('{failed}', String(result.failed_count)));
      } else {
        message.success(t('admin.dismissDone').replace('{ok}', String(result.succeeded_count)));
      }
      setDismissOpen(false);
      setReason('');
      setSelected([]);
      await load();
    } catch (dismissError) {
      message.error(dismissError instanceof Error ? dismissError.message : String(dismissError));
    } finally {
      setSubmitting(false);
    }
  };

  const allSelected = items.length > 0 && selected.length === items.length;
  const toggleAll = (checked: boolean) =>
    setSelected(checked ? items.map((item) => item.case_id) : []);
  const toggleOne = (caseId: string, checked: boolean) =>
    setSelected((current) => (checked
      ? [...current, caseId]
      : current.filter((existing) => existing !== caseId)));

  const columns = [
    {
      title: (
        <Checkbox
          checked={allSelected}
          onChange={(event) => toggleAll(event.target.checked)}
        />
      ),
      dataIndex: 'case_id',
      width: 48,
      render: (value: string) => (
        <Checkbox
          checked={selected.includes(value)}
          onChange={(event) => toggleOne(value, event.target.checked)}
        />
      ),
    },
    {
      title: t('admin.caseColumn'),
      key: 'case',
      dataIndex: 'case_id',
      render: (value: string, row: AdminReviewQueueItem) => (
        <Link to={`/attribution/${value}`}>
          {row.goods_name ?? row.goods_code} · {row.shop_name ?? row.shop_code}
        </Link>
      ),
    },
    { title: t('attr.colDecisionDate'), dataIndex: 'decision_date', width: 120 },
    {
      title: t('attr.colGap'),
      dataIndex: 'signed_gap',
      align: 'right' as const,
      width: 100,
      render: (value: number) => <Text>{value > 0 ? `+${value}` : value}</Text>,
    },
    {
      title: t('attr.colStatus'),
      dataIndex: 'status',
      width: 150,
      render: (value: AttributionCaseStatus) => <AttributionStatusTag status={value} lang={lang} />,
    },
    {
      title: t('admin.blocksRun'),
      dataIndex: 'blocks_run',
      width: 190,
      render: (value: boolean, row: AdminReviewQueueItem) => (value ? (
        <Tag color="warning">{t('admin.blocksRunYes')}</Tag>
      ) : (
        <Tag color="default">{row.run_locked ? t('admin.runLocked') : t('admin.blocksRunNo')}</Tag>
      )),
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <div className="admin-page-head">
        <div>
          <Title level={4} style={{ margin: 0 }}>{t('admin.queueTitle')}</Title>
          <Text type="secondary">{t('admin.queueHint')}</Text>
        </div>
        <Space size="small">
          <Select
            size="small"
            style={{ width: 190 }}
            allowClear
            placeholder={t('admin.filterAllStates')}
            value={statusFilter}
            onChange={(value) => setStatusFilter(value ?? undefined)}
            options={QUEUE_STATES.map((state) => ({
              value: state, label: caseStatusLabel(state, lang),
            }))}
          />
          <Button size="small" icon={<IconRefresh size={15} />} onClick={() => void load()}>
            {t('app.refresh')}
          </Button>
        </Space>
      </div>

      <Alert
        showIcon
        type="info"
        message={t('admin.dismissMeaningTitle')}
        description={t('admin.dismissMeaningBody')}
      />

      {error && <Alert showIcon type="error" message={t('admin.loadFailed')} description={error} />}

      <Card size="small">
        <Space style={{ marginBottom: 12 }} size="small">
          <Button
            danger
            size="small"
            disabled={!selected.length}
            onClick={() => setDismissOpen(true)}
          >
            {t('admin.dismissSelected').replace('{n}', String(selected.length))}
          </Button>
          <Text type="secondary">{t('admin.waiveHint')}</Text>
        </Space>
        <Spin spinning={loading}>
          <Table
            size="small"
            rowKey="case_id"
            columns={columns}
            dataSource={items}
            pagination={false}
          />
        </Spin>
      </Card>

      <Modal
        open={dismissOpen}
        title={t('admin.dismissTitle')}
        okText={t('admin.dismissConfirm')}
        cancelText={t('admin.cancel')}
        confirmLoading={submitting}
        okButtonProps={{ danger: true, disabled: !reason.trim() }}
        onOk={() => void handleDismiss()}
        onCancel={() => setDismissOpen(false)}
      >
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Paragraph>{t('admin.dismissBody').replace('{n}', String(selected.length))}</Paragraph>
          {strandedCount > 0 && (
            <Alert
              showIcon
              type="warning"
              icon={<IconAlertTriangle size={18} />}
              message={t('admin.dismissStrandsTitle').replace('{n}', String(strandedCount))}
              description={(
                <Space direction="vertical" size="small">
                  <Text>{t('admin.dismissStrandsBody')}</Text>
                  <Text type="secondary">
                    {t('admin.dismissStrandsRuns')}: {strandedRuns.join(', ')}
                  </Text>
                </Space>
              )}
            />
          )}
          <div>
            <Text strong>{t('admin.dismissReason')}</Text>
            <Input.TextArea
              rows={3}
              value={reason}
              placeholder={t('admin.dismissReasonPlaceholder')}
              onChange={(event) => setReason(event.target.value)}
            />
          </div>
        </Space>
      </Modal>
    </Space>
  );
}
