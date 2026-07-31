import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { IconRefresh } from '@tabler/icons-react';
import { Alert, Button, Card, Space, Spin, Table, Tag, Typography } from '../components/ui';
import { fetchAttributionKnowledge, fetchDiagnosticAgents } from '../api';
import { useI18n } from '../i18n';
import type { DiagnosticAgent, KnowledgeEntry } from '../types';

const { Text, Title } = Typography;

const formatScope = (scope: Record<string, string> | null | undefined) => {
  const entries = Object.entries(scope ?? {});
  return entries.length ? entries.map(([key, value]) => `${key}=${value}`).join(' · ') : '—';
};

const formatExpiry = (value: string) => {
  const at = new Date(value);
  if (Number.isNaN(at.getTime())) return value;
  return at.toLocaleString();
};

export default function AdminKnowledgePage() {
  const { t } = useI18n();
  const [entries, setEntries] = useState<KnowledgeEntry[]>([]);
  const [agents, setAgents] = useState<DiagnosticAgent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();

  const load = async () => {
    setLoading(true);
    setError(undefined);
    try {
      const [knowledge, agentList] = await Promise.all([
        fetchAttributionKnowledge(),
        fetchDiagnosticAgents(),
      ]);
      setEntries(knowledge.items);
      setAgents(agentList.items);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : String(loadError));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const knowledgeColumns = [
    {
      title: t('admin.knowledgeId'),
      dataIndex: 'knowledge_id',
      width: 120,
      render: (value: string) => <Text code>{value.slice(0, 8)}</Text>,
    },
    {
      title: t('attr.case'),
      dataIndex: 'case_id',
      width: 160,
      render: (value: string) => <Link to={`/attribution/${value}`}>{value.slice(0, 12)}</Link>,
    },
    {
      title: t('admin.knowledgeScope'),
      dataIndex: 'scope',
      render: (value: Record<string, string>) => <Text>{formatScope(value)}</Text>,
    },
    {
      title: t('admin.knowledgeExpires'),
      dataIndex: 'expires_at',
      width: 190,
      render: (value: string) => <Text type="secondary">{formatExpiry(value)}</Text>,
    },
    {
      title: t('admin.knowledgeVersion'),
      dataIndex: 'version',
      align: 'right' as const,
      width: 90,
    },
  ];

  const agentColumns = [
    {
      title: t('admin.agentId'),
      dataIndex: 'agent_id',
      width: 200,
      render: (value: string, row: DiagnosticAgent) => (
        <Space size="small">
          <Text strong>{value}</Text>
          <Tag color={row.enabled ? 'success' : 'default'}>
            {row.enabled ? t('admin.agentEnabled') : t('admin.agentDisabled')}
          </Tag>
        </Space>
      ),
    },
    { title: t('admin.agentVersion'), dataIndex: 'version', width: 140 },
    { title: t('admin.agentDomain'), dataIndex: 'domain', width: 130 },
    {
      title: t('admin.agentScenarios'),
      dataIndex: 'applicable_scenarios',
      render: (value: string[]) => (
        <Space size="small" wrap>
          {value.map((item) => <Tag key={item}>{item}</Tag>)}
        </Space>
      ),
    },
    {
      title: t('admin.agentTools'),
      dataIndex: 'deterministic_tools',
      render: (value: string[]) => (
        <Space size="small" wrap>
          {value.map((item) => <Tag key={item} color="processing">{item}</Tag>)}
        </Space>
      ),
    },
    {
      title: t('admin.agentEvidence'),
      dataIndex: 'required_evidence',
      render: (value: string[]) => <Text type="secondary">{value.join(', ')}</Text>,
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <div className="admin-page-head">
        <div>
          <Title level={4} style={{ margin: 0 }}>{t('admin.knowledgeTitle')}</Title>
          <Text type="secondary">{t('admin.knowledgeHint')}</Text>
        </div>
        <Button size="small" icon={<IconRefresh size={15} />} onClick={() => void load()}>
          {t('app.refresh')}
        </Button>
      </div>

      {error && <Alert showIcon type="error" message={t('admin.loadFailed')} description={error} />}

      <Spin spinning={loading}>
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          <Card size="small" title={t('admin.agentsTitle')} extra={<Text type="secondary">{t('admin.agentsHint')}</Text>}>
            <Table size="small" rowKey="agent_id" columns={agentColumns} dataSource={agents} pagination={false} />
          </Card>

          <Card size="small" title={t('admin.knowledgeActive')} extra={<Text type="secondary">{t('admin.knowledgeActiveHint')}</Text>}>
            <Table size="small" rowKey="knowledge_id" columns={knowledgeColumns} dataSource={entries} pagination={false} />
          </Card>
        </Space>
      </Spin>
    </Space>
  );
}
