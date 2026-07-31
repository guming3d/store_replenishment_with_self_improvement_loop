import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { IconRefresh } from '@tabler/icons-react';
import { Alert, Button, Card, Space, Spin, Table, Tag, Typography } from '../components/ui';
import { caseStatusColor, caseStatusLabel } from '../components/AttributionStatusTag';
import { fetchAdminJobs } from '../api';
import { useI18n } from '../i18n';
import type { AdminJob } from '../types';

const { Text, Title } = Typography;

const JOB_STATUS: Record<string, { color: string; zh: string; en: string }> = {
  QUEUED: { color: 'default', zh: '排队中', en: 'Queued' },
  RUNNING: { color: 'processing', zh: '运行中', en: 'Running' },
  COMPLETED: { color: 'success', zh: '已完成', en: 'Completed' },
};

export default function AdminJobsPage() {
  const { t, lang } = useI18n();
  const [jobs, setJobs] = useState<AdminJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();

  const load = async () => {
    setLoading(true);
    setError(undefined);
    try {
      setJobs((await fetchAdminJobs(1, 100)).items);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : String(loadError));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const columns = [
    {
      title: t('admin.jobId'),
      dataIndex: 'job_id',
      width: 120,
      render: (value: string) => <Text code>{value.slice(0, 8)}</Text>,
    },
    { title: t('admin.jobRun'), dataIndex: 'run_id', ellipsis: true },
    {
      title: t('attr.colStatus'),
      dataIndex: 'status',
      width: 130,
      render: (value: string) => {
        const meta = JOB_STATUS[value];
        return <Tag color={meta?.color ?? 'default'}>{meta ? meta[lang] : value}</Tag>;
      },
    },
    {
      title: t('admin.jobProgress'),
      dataIndex: 'completed_cases',
      align: 'right' as const,
      width: 120,
      render: (value: number, row: AdminJob) => `${value} / ${row.total_cases}`,
    },
    {
      title: t('admin.pendingReview'),
      dataIndex: 'pending_review',
      align: 'right' as const,
      width: 120,
      render: (value: number) => (value
        ? <Link to="/admin/review-queue"><Tag color="warning">{value}</Tag></Link>
        : <Text type="secondary">0</Text>),
    },
    {
      title: t('admin.jobBreakdown'),
      dataIndex: 'cases_by_state',
      render: (value: Record<string, number>) => (
        <Space size="small" wrap>
          {Object.entries(value).map(([state, count]) => (
            <Tag key={state} color={caseStatusColor(state)}>
              {`${caseStatusLabel(state, lang)} · ${count}`}
            </Tag>
          ))}
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <div className="admin-page-head">
        <div>
          <Title level={4} style={{ margin: 0 }}>{t('admin.jobsTitle')}</Title>
          <Text type="secondary">{t('admin.jobsHint')}</Text>
        </div>
        <Button size="small" icon={<IconRefresh size={15} />} onClick={() => void load()}>
          {t('app.refresh')}
        </Button>
      </div>

      {error && <Alert showIcon type="error" message={t('admin.loadFailed')} description={error} />}

      <Card size="small">
        <Spin spinning={loading}>
          <Table size="small" rowKey="job_id" columns={columns} dataSource={jobs} pagination={false} />
        </Spin>
      </Card>
    </Space>
  );
}
