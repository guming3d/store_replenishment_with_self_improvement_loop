import { ApiOutlined, BulbOutlined, SearchOutlined } from '@/components/ui/icons';
import { Button, Card, Descriptions, Input, Space, Statistic, Tag, Timeline, Typography, message } from '@/components/ui';
import { useState } from 'react';
import { fetchTrace } from '../api';
import { useI18n } from '../i18n';
import type { TraceDetail } from '../types';

const { Paragraph, Text, Title } = Typography;

export default function Explainability() {
  const { t } = useI18n();
  const [traceId, setTraceId] = useState('TRACE-PROMO-002');
  const [trace, setTrace] = useState<TraceDetail>();
  const [loading, setLoading] = useState(false);

  const load = async () => {
    if (!traceId.trim()) { message.warning(t('xp.warnTrace')); return; }
    setLoading(true);
    try {
      const data = await fetchTrace(traceId.trim());
      setTrace(data);
    } catch (error) {
      setTrace(undefined);
      message.error(t('xp.loadFailed', { reason: error instanceof Error ? error.message : '' }));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Space direction="vertical" size="large" className="page-stack">
      <Card>
        <Space className="toolbar" align="start" wrap>
          <div><Title level={3}>{t('xp.title')}</Title><Text type="secondary">{t('xp.subtitle')}</Text></div>
          <Space.Compact className="trace-search"><Input value={traceId} onChange={(event) => setTraceId(event.target.value)} placeholder="TRACE-PROMO-002" /><Button type="primary" icon={<SearchOutlined />} loading={loading} onClick={load}>{t('xp.query')}</Button></Space.Compact>
        </Space>
      </Card>

      {trace && <>
        <div className="metric-grid three"><Card><Statistic title="Trace ID" value={trace.trace_id} /></Card><Card><Statistic title={t('xp.scenario')} value={trace.scenario} /></Card><Card><Statistic title={t('xp.finalQty')} value={trace.final_qty} suffix={t('xp.pieces')} /></Card></div>
        <Card title={t('xp.summaryCard')}><Descriptions bordered column={1} size="small"><Descriptions.Item label={t('xp.shopSku')}>{trace.shop} / {trace.sku}</Descriptions.Item><Descriptions.Item label={t('xp.summary')}><Paragraph>{trace.summary}</Paragraph></Descriptions.Item></Descriptions></Card>
        <Card title={t('xp.timeline')}>
          <Timeline mode="left" items={trace.steps.map((step) => ({
            color: step.type === 'soft' ? 'orange' : 'blue',
            dot: step.type === 'soft' ? <BulbOutlined /> : <ApiOutlined />,
            label: t('xp.step', { n: step.step }),
            children: <div className="timeline-card"><Space wrap><Text strong>{step.name}</Text><Tag color={step.type === 'soft' ? 'orange' : 'geekblue'}>{step.skill}</Tag><Tag color={(step.delta ?? 0) > 0 ? 'green' : (step.delta ?? 0) < 0 ? 'red' : 'default'}>Δ {step.delta ?? 0}</Tag></Space><Paragraph type="secondary">{t('xp.input')}{step.input || '-'}</Paragraph><Paragraph>{t('xp.output')}{step.output || '-'}</Paragraph>{step.formula?.length ? <div className="trace-formula"><Text type="secondary" className="trace-formula-label">{t('xp.formula')}</Text>{step.formula.map((line, i) => <div key={`${step.step}-f${i}`} className="trace-formula-line">{line}</div>)}</div> : null}</div>,
          }))} />
        </Card>
      </>}
    </Space>
  );
}
