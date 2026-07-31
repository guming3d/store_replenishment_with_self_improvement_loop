import { useEffect, useState } from 'react';
import { IconAlertTriangle, IconRefresh } from '@tabler/icons-react';
import { Alert, Button, Card, Descriptions, Space, Spin, Statistic, Table, Tag, Typography } from '../components/ui';
import { fetchAdminOverview } from '../api';
import { caseStatusColor, caseStatusLabel } from '../components/AttributionStatusTag';
import { useI18n } from '../i18n';
import type { AdminLease, AdminOverview } from '../types';

const { Text, Title } = Typography;

const STATE_ORDER = [
  'QUEUED', 'RUNNING', 'NEEDS_REVIEW', 'CHANGES_REQUESTED',
  'FAILED', 'HUMAN_APPROVED', 'CANCELLED', 'SUPERSEDED',
];

function formatAge(seconds: number | null): string {
  if (seconds === null) return '—';
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

export default function AdminOverviewPage() {
  const { t, lang } = useI18n();
  const [overview, setOverview] = useState<AdminOverview>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();

  const load = async () => {
    setLoading(true);
    setError(undefined);
    try {
      setOverview(await fetchAdminOverview());
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : String(loadError));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 30000);
    return () => window.clearInterval(timer);
  }, []);

  const worker = overview?.attribution_worker;
  const leaseColumns = [
    { title: t('admin.leaseCase'), dataIndex: 'case_id', render: (value: string) => <Text code>{value.slice(0, 8)}</Text> },
    { title: t('admin.leaseWorker'), dataIndex: 'worker_id' },
    { title: t('admin.leaseState'), dataIndex: 'state' },
    {
      title: t('admin.leaseRemaining'),
      dataIndex: 'seconds_remaining',
      align: 'right' as const,
      render: (value: number, row: AdminLease) =>
        row.expired
          ? <Tag color="error">{t('admin.leaseExpired')}</Tag>
          : <Text>{formatAge(Math.max(value, 0))}</Text>,
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <div className="admin-page-head">
        <div>
          <Title level={4} style={{ margin: 0 }}>{t('admin.overviewTitle')}</Title>
          <Text type="secondary">{t('admin.overviewHint')}</Text>
        </div>
        <Button size="small" icon={<IconRefresh size={15} />} onClick={() => void load()}>
          {t('app.refresh')}
        </Button>
      </div>

      {error && <Alert showIcon type="error" message={t('admin.loadFailed')} description={error} />}

      <Spin spinning={loading && !overview}>
        {overview && (
          <Space direction="vertical" size="large" style={{ width: '100%' }}>
            {!worker?.healthy && (
              <Alert
                showIcon
                type="error"
                icon={<IconAlertTriangle size={18} />}
                message={t('admin.workerUnhealthy')}
                description={worker?.last_poll_error ?? undefined}
              />
            )}

            <Card size="small" title={t('admin.pipelineTitle')}>
              <Descriptions size="small" column={4} bordered>
                <Descriptions.Item label={t('admin.workerRunning')}>
                  <Tag color={worker?.running ? 'success' : 'default'}>
                    {worker?.running ? t('admin.yes') : t('admin.no')}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label={t('admin.workerHealthy')}>
                  <Tag color={worker?.healthy ? 'success' : 'error'}>
                    {worker?.healthy ? t('admin.yes') : t('admin.no')}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label={t('admin.agentRuntime')}>
                  <Tag color={overview.agent_runtime?.available ? 'success' : 'warning'}>
                    {overview.agent_runtime?.available ? t('admin.available') : t('admin.unavailable')}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label={t('admin.forecastPairs')}>
                  {overview.forecast_pairs}
                </Descriptions.Item>
              </Descriptions>
              {worker?.last_poll_error && (
                <Alert
                  showIcon
                  type="warning"
                  style={{ marginTop: 12 }}
                  message={t('admin.lastPollError')}
                  description={worker.last_poll_error}
                />
              )}
            </Card>

            <Card size="small" title={t('admin.backlogTitle')}>
              <Space size="large" wrap>
                <Statistic title={t('admin.queued')} value={overview.backlog.queued} />
                <Statistic title={t('admin.running')} value={overview.backlog.running} />
                <Statistic title={t('admin.pendingReview')} value={overview.pending_review} />
                <Statistic title={t('admin.oldestAge')} value={formatAge(overview.backlog.oldest_age_seconds)} />
              </Space>
            </Card>

            <Card size="small" title={t('admin.casesByState')}>
              <Space size="middle" wrap>
                {STATE_ORDER.filter((state) => overview.cases_by_state[state]).map((state) => (
                  <Tag key={state} color={caseStatusColor(state)}>
                    {`${caseStatusLabel(state, lang)} · ${overview.cases_by_state[state]}`}
                  </Tag>
                ))}
                {Object.keys(overview.cases_by_state).length === 0 && (
                  <Text type="secondary">{t('admin.noCases')}</Text>
                )}
              </Space>
            </Card>

            <Card size="small" title={t('admin.leasesTitle')}>
              <Text type="secondary">{t('admin.leasesHint')}</Text>
              <Table
                size="small"
                style={{ marginTop: 12 }}
                rowKey="case_id"
                columns={leaseColumns}
                dataSource={overview.leases}
                pagination={false}
              />
            </Card>
          </Space>
        )}
      </Spin>
    </Space>
  );
}
