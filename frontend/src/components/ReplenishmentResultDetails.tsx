import { Descriptions, Space, Tag, Typography } from '@/components/ui';
import { useEffect, useState } from 'react';
import { fetchTrace } from '../api';
import { useI18n } from '../i18n';
import type { ReplenishmentResult, TraceDetail } from '../types';

const { Paragraph, Text } = Typography;

const traceCache = new Map<string, TraceDetail | null>();

const isTraceDetail = (value: unknown): value is TraceDetail => (
  typeof value === 'object'
  && value !== null
  && typeof (value as TraceDetail).trace_id === 'string'
  && Array.isArray((value as TraceDetail).steps)
);

export default function ReplenishmentResultDetails({ record }: { record: ReplenishmentResult }) {
  const { t } = useI18n();
  const [trace, setTrace] = useState<TraceDetail | null>(() => traceCache.get(record.trace_id) ?? null);

  useEffect(() => {
    if (traceCache.has(record.trace_id)) {
      setTrace(traceCache.get(record.trace_id) ?? null);
      return;
    }

    let cancelled = false;

    void fetchTrace(record.trace_id)
      .then((data) => {
        if (cancelled) return;
        const nextTrace = isTraceDetail(data) ? data : null;
        traceCache.set(record.trace_id, nextTrace);
        setTrace(nextTrace);
      })
      .catch(() => {
        if (cancelled) return;
        traceCache.set(record.trace_id, null);
        setTrace(null);
      });

    return () => {
      cancelled = true;
    };
  }, [record.trace_id]);

  const explainabilitySummary = trace?.summary?.trim() || record.explanation;

  return (
    <Descriptions size="small" column={1} bordered>
      {record.params && (
        <Descriptions.Item label={t('sug.paramsUsed')}>
          <Space wrap>
            <Tag>{t('cfg.p.fill_rate')}: {Math.round((record.params.fill_rate ?? record.fill_rate ?? 0) * 10000) / 100}%</Tag>
            {record.params.coverage != null && <Tag>{t('cfg.p.coverage')}: {record.params.coverage}</Tag>}
            <Tag>{t('cfg.p.case_pack')}: {record.params.case_pack}</Tag>
            <Tag>{t('cfg.p.moq')}: {record.params.moq}</Tag>
            <Tag>{t('cfg.p.shelf_max')}: {record.params.shelf_max}</Tag>
            {record.lead_time != null && <Tag color="blue">{t('sug.leadTime')}: {record.lead_time}</Tag>}
          </Space>
        </Descriptions.Item>
      )}
      {record.inventory && (
        <Descriptions.Item label={t('sug.inventorySnapshot')}>
          <Space wrap>
            <Tag>{t('sug.colOnHand')}: {record.inventory.on_hand}</Tag>
            <Tag>{t('sug.colInTransit')}: {record.inventory.in_transit}</Tag>
            <Tag>{t('sug.colReserved')}: {record.inventory.reserved}</Tag>
            <Tag>{t('sug.colExpiring')}: {record.inventory.expiring}</Tag>
            {record.inventory.expiring > 0 && record.inventory.days_to_expiry != null && (
              <>
                <Tag color="gold">{t('sug.snapDaysToExpiry')}: {record.inventory.days_to_expiry}</Tag>
                <Tag color="green">{t('sug.snapExpSellable')}: {record.inventory.expiring_sellable ?? 0}</Tag>
                <Tag color="red">{t('sug.snapExpWaste')}: {record.inventory.expiring_waste ?? record.inventory.expiring}</Tag>
              </>
            )}
            <Tag>{t('sug.colAvailable')}: {record.inventory.available}</Tag>
            {record.inventory.phantom_suspect && <Tag color="volcano">{t('sug.phantom')}</Tag>}
          </Space>
        </Descriptions.Item>
      )}
      <Descriptions.Item label={t('sug.traceId')}>{record.trace_id}</Descriptions.Item>
      <Descriptions.Item label={t('menu.explainability')}>
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Paragraph style={{ marginBottom: 0 }}>{explainabilitySummary}</Paragraph>
          {trace?.steps?.length ? (
            <div className="trace-inline-list">
              {trace.steps.map((step) => (
                <div key={`${record.trace_id}-${step.step}`} className="trace-inline-step">
                  <Space wrap size={[8, 4]}>
                    <Text strong>{t('xp.step', { n: step.step })}</Text>
                    <Tag color={step.type === 'soft' ? 'orange' : 'geekblue'}>{step.skill}</Tag>
                    {typeof step.delta === 'number' && (
                      <Tag color={step.delta > 0 ? 'green' : step.delta < 0 ? 'red' : 'default'}>
                        Δ {step.delta}
                      </Tag>
                    )}
                  </Space>
                  <Paragraph type="secondary" style={{ margin: '4px 0 0' }}>
                    {step.output || '-'}
                  </Paragraph>
                  {step.formula?.length ? (
                    <div className="trace-formula">
                      <Text type="secondary" className="trace-formula-label">{t('xp.formula')}</Text>
                      {step.formula.map((line, i) => (
                        <div key={`${record.trace_id}-${step.step}-f${i}`} className="trace-formula-line">
                          {line}
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          ) : null}
        </Space>
      </Descriptions.Item>
    </Descriptions>
  );
}
