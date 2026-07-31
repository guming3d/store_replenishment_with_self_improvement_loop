import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  DownloadOutlined,
  EditOutlined,
  ReloadOutlined,
  StopOutlined,
  SyncOutlined,
} from '@/components/ui/icons';
import {
  Alert,
  Breadcrumb,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Progress,
  Row,
  Space,
  Statistic,
  Steps,
  Table,
  Tabs,
  Tag,
  Timeline,
  Typography,
  message,
} from '@/components/ui';
import type { ColumnsType } from '@/components/ui';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  cancelAttributionCase,
  downloadAttributionAttemptLog,
  fetchAttributionCase,
  retryAttributionCase,
} from '../api';
import AttributionReviewDrawer from '../components/AttributionReviewDrawer';
import { AttributionStatusTag } from '../components/AttributionStatusTag';
import { useI18n } from '../i18n';
import { CHART_AXIS, CHART_NEGATIVE, CHART_POSITIVE, CHART_SERIES } from '../theme/colors';
import type {
  AttributionAllocation,
  AttributionCaseDetail,
  AttributionCaseStatus,
  AttributionEvidence,
} from '../types';

const { Paragraph, Text, Title } = Typography;
const ACTIVE = new Set<AttributionCaseStatus>(['QUEUED', 'RUNNING']);
/** Cause hues, minus the neutral reserved for the unexplained slice. */
const CAUSE_COLORS = CHART_SERIES.filter((hue) => hue !== CHART_AXIS);

type ShareLabelProps = {
  cx?: number;
  cy?: number;
  midAngle?: number;
  innerRadius?: number;
  outerRadius?: number;
  percent?: number;
};

/** Percentages sit inside the ring; a thin slice has no room, so it is left to the legend. */
const renderShareLabel = ({ cx, cy, midAngle, innerRadius, outerRadius, percent }: ShareLabelProps) => {
  if (percent === undefined || percent < 0.08) return null;
  if (cx === undefined || cy === undefined || midAngle === undefined) return null;
  const radius = ((innerRadius ?? 0) + (outerRadius ?? 0)) / 2;
  const radians = -midAngle * (Math.PI / 180);
  return (
    <text
      x={cx + radius * Math.cos(radians)}
      y={cy + radius * Math.sin(radians)}
      fill="#ffffff"
      fontSize={12}
      fontWeight={600}
      textAnchor="middle"
      dominantBaseline="central"
    >
      {`${Math.round(percent * 100)}%`}
    </text>
  );
};

const progressStep = (status: AttributionCaseStatus) => {
  if (status === 'QUEUED') return 0;
  if (status === 'RUNNING') return 1;
  if (status === 'NEEDS_REVIEW' || status === 'FAILED' || status === 'CHANGES_REQUESTED') return 3;
  return 4;
};

export default function AttributionCaseDetailPage() {
  const { caseId = '' } = useParams();
  const navigate = useNavigate();
  const { t, lang } = useI18n();
  const [detail, setDetail] = useState<AttributionCaseDetail>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [reviewOpen, setReviewOpen] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);

  const downloadRawLog = async (attemptNumber: number) => {
    try {
      const blob = await downloadAttributionAttemptLog(caseId, attemptNumber);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `attribution-${caseId}-attempt-${attemptNumber}.jsonl`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (downloadError) {
      message.error(downloadError instanceof Error ? downloadError.message : t('attr.rawLogFailed'));
    }
  };

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      setDetail(await fetchAttributionCase(caseId));
      setError(undefined);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : t('attr.detailFailed'));
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [caseId, t]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!detail || !ACTIVE.has(detail.status)) return undefined;
    const timer = window.setInterval(() => {
      if (!document.hidden) void load(true);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [detail, load]);

  const report = detail?.latest_report;
  const debugRawIo = detail?.trace_events.some((event) => (
    event.event_type === 'MODEL_RAW_INPUT'
    || event.event_type === 'MODEL_RAW_OUTPUT'
    || event.event_type === 'TOOL_RAW_INPUT'
    || event.event_type === 'TOOL_RAW_OUTPUT'
  )) ?? false;
  const allocationChart = useMemo(() => {
    if (!detail || !report) return [];
    return [
      ...report.allocations.map((item, index) => ({
        name: item.label ?? item.cause_code,
        value: item.signed_contribution_qty,
        color: CAUSE_COLORS[index % CAUSE_COLORS.length],
      })),
      { name: t('attr.unexplained'), value: report.unexplained_signed_gap, color: CHART_AXIS },
    ];
  }, [detail, report, t]);

  /**
   * A pie states shares of one whole, which only holds while every
   * contribution pushes the same way. Mixed signs keep the signed bars.
   */
  const allocationShare = useMemo(() => {
    const slices = allocationChart
      .filter((item) => item.value !== 0)
      .map((item) => ({ ...item, magnitude: Math.abs(item.value) }));
    const mixedSigns = slices.some((item) => item.value > 0) && slices.some((item) => item.value < 0);
    if (!slices.length || mixedSigns) return null;
    const total = slices.reduce((sum, item) => sum + item.magnitude, 0);
    const net = slices.reduce((sum, item) => sum + item.value, 0);
    return { slices, total, net };
  }, [allocationChart]);

  const canReview = detail && ['NEEDS_REVIEW', 'CHANGES_REQUESTED', 'FAILED'].includes(detail.status);

  const retry = async () => {
    if (!detail) return;
    setActionLoading(true);
    try {
      setDetail(await retryAttributionCase(
        detail.case_id,
        detail.version,
        lang === 'zh' ? 'zh-CN' : 'en-US',
      ));
      message.success(t('attr.retryQueued'));
    } catch (actionError) {
      message.error(t('attr.actionFailed', { reason: actionError instanceof Error ? actionError.message : '' }));
    } finally {
      setActionLoading(false);
    }
  };

  const cancel = async () => {
    if (!detail) return;
    setActionLoading(true);
    try {
      setDetail(await cancelAttributionCase(detail.case_id, detail.version));
      message.success(t('attr.cancelled'));
    } catch (actionError) {
      message.error(t('attr.actionFailed', { reason: actionError instanceof Error ? actionError.message : '' }));
    } finally {
      setActionLoading(false);
    }
  };

  if (error && !detail) {
    return (
      <Alert
        showIcon
        type="error"
        message={t('attr.detailFailed')}
        description={error}
        action={<Button onClick={() => void load()}>{t('attr.refresh')}</Button>}
      />
    );
  }

  if (!detail) return <Card loading={loading} />;

  const riskLabel = (flag: string) => {
    const key = `attr.risk.${flag}`;
    const label = t(key);
    return label === key ? flag : label;
  };

  // Shapley amounts arrive as floats, and raw values such as 4.000000000000001 read as noise.
  const qty = (value: number) => {
    const rounded = Math.round(value * 100) / 100;
    return `${rounded > 0 ? '+' : ''}${Number.isInteger(rounded) ? rounded : rounded.toFixed(2)}`;
  };

  const evidenceColumns: ColumnsType<AttributionEvidence> = [
    { title: t('attr.evidenceType'), dataIndex: 'evidence_type', width: 170 },
    { title: t('attr.evidenceTitle'), dataIndex: 'title' },
    { title: t('attr.evidenceSource'), dataIndex: 'source', width: 200 },
    { title: t('attr.evidenceVersion'), dataIndex: 'source_version', width: 130, render: (value?: string) => value ?? '-' },
    {
      title: t('attr.evidenceFresh'),
      dataIndex: 'fresh',
      width: 100,
      render: (value?: boolean) => value == null ? '-' : <Tag color={value ? 'success' : 'warning'}>{value ? t('attr.yes') : t('attr.no')}</Tag>,
    },
  ];

  const allocationColumns: ColumnsType<AttributionAllocation> = [
    {
      title: t('attr.reviewCause'),
      dataIndex: 'cause_code',
      width: 210,
      render: (_value: string, row: AttributionAllocation) => row.label ?? row.cause_code,
    },
    { title: t('attr.domain'), dataIndex: 'domain', width: 140 },
    {
      title: t('attr.reviewContribution'),
      dataIndex: 'signed_contribution_qty',
      align: 'right',
      width: 140,
      render: (value: number) => qty(value),
    },
    { title: t('attr.reviewExplanation'), dataIndex: 'explanation' },
  ];

  const explained = report
    ? report.allocations.reduce((sum, item) => sum + item.signed_contribution_qty, 0)
    : 0;

  const overview = (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      {report ? (
        <>
          <Card size="small" title={t('attr.quantityLedger')}>
            <Descriptions size="small" column={3} bordered>
              <Descriptions.Item label={t('attr.ledgerRecommended')}>
                {report.recommended_qty ?? detail.recommended_qty}
              </Descriptions.Item>
              <Descriptions.Item label={t('attr.ledgerOverride')}>
                {report.override_qty ?? detail.override_qty}
              </Descriptions.Item>
              <Descriptions.Item label={t('attr.ledgerGap')}>{qty(report.signed_gap)}</Descriptions.Item>
              <Descriptions.Item label={t('attr.ledgerBare')}>
                {report.bare_baseline_qty ?? '-'}
              </Descriptions.Item>
              <Descriptions.Item label={t('attr.ledgerExplained')}>{qty(explained)}</Descriptions.Item>
              <Descriptions.Item label={t('attr.ledgerUnexplained')}>
                {qty(report.unexplained_signed_gap)}
              </Descriptions.Item>
            </Descriptions>
          </Card>
          <Card size="small" title={t('attr.summary')}>
            <Paragraph style={{ whiteSpace: 'pre-line' }}>{report.summary}</Paragraph>
            <Space wrap>
              {report.risk_flags.map((flag) => <Tag color="warning" key={flag}>{riskLabel(flag)}</Tag>)}
              {report.conflicts.map((flag) => <Tag color="error" key={flag}>{riskLabel(flag)}</Tag>)}
            </Space>
            <Paragraph type="secondary" style={{ marginBottom: 0, marginTop: 12, fontSize: 12 }}>
              {t('attr.summaryHint')}
            </Paragraph>
          </Card>
          <Row gutter={[16, 16]}>
            {report.allocations.map((allocation) => (
              <Col xs={24} md={12} key={allocation.cause_code}>
                <Card size="small" title={allocation.label ?? allocation.cause_code}>
                  <Descriptions size="small" column={1}>
                    <Descriptions.Item label={t('attr.reviewContribution')}>{qty(allocation.signed_contribution_qty)}</Descriptions.Item>
                  </Descriptions>
                  <Paragraph type="secondary">{allocation.explanation}</Paragraph>
                </Card>
              </Col>
            ))}
          </Row>
          {report.model_summary && (
            <Card size="small" title={t('attr.modelSummary')}>
              <Paragraph type="secondary" style={{ marginBottom: 0 }}>{report.model_summary}</Paragraph>
              <Paragraph type="secondary" style={{ marginBottom: 0, marginTop: 12, fontSize: 12 }}>
                {t('attr.modelSummaryHint')}
              </Paragraph>
            </Card>
          )}
        </>
      ) : <Empty description={t('attr.reportPending')} />}
    </Space>
  );

  const evidence = (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {detail.reason_text && (
        <Alert
          showIcon
          type="warning"
          message={t('attr.operatorEvidence')}
          description={`${detail.reason_code}: ${detail.reason_text}`}
        />
      )}
      <Table
        rowKey="evidence_id"
        size="small"
        columns={evidenceColumns}
        dataSource={report?.evidence ?? []}
        pagination={false}
        locale={{ emptyText: <Empty description={t('attr.noEvidence')} /> }}
      />
    </Space>
  );

  const allocation = report ? (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Card
        size="small"
        title={t('attr.contributionMix')}
        extra={allocationShare ? (
          <Text type="secondary">
            {`${t('attr.netDelta')}: ${allocationShare.net > 0 ? '+' : ''}${allocationShare.net}`}
          </Text>
        ) : undefined}
      >
        <div style={{ width: '100%', height: 300 }}>
          <ResponsiveContainer>
            {allocationShare ? (
              <PieChart>
                <Pie
                  data={allocationShare.slices}
                  dataKey="magnitude"
                  nameKey="name"
                  cy="44%"
                  innerRadius={62}
                  outerRadius={100}
                  paddingAngle={2}
                  labelLine={false}
                  label={renderShareLabel}
                  animationDuration={700}
                >
                  {allocationShare.slices.map((slice) => <Cell key={slice.name} fill={slice.color} />)}
                </Pie>
                <Tooltip
                  formatter={(value, name) => {
                    const magnitude = Number(value);
                    const signed = allocationShare.net < 0 ? -magnitude : magnitude;
                    const share = Math.round((magnitude / allocationShare.total) * 100);
                    return [`${signed > 0 ? '+' : ''}${signed} · ${share}%`, name as string];
                  }}
                />
                <Legend
                  verticalAlign="bottom"
                  itemSorter={null}
                  formatter={(value) => {
                    const slice = allocationShare.slices.find((item) => item.name === value);
                    if (!slice) return String(value);
                    return `${slice.name} ${slice.value > 0 ? '+' : ''}${slice.value}`;
                  }}
                />
              </PieChart>
            ) : (
              <BarChart data={allocationChart} margin={{ top: 16, right: 24, left: 8, bottom: 40 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="name" angle={-20} textAnchor="end" interval={0} height={70} />
                <YAxis />
                <Tooltip />
                <ReferenceLine y={0} stroke={CHART_AXIS} />
                <Bar dataKey="value">
                  {allocationChart.map((item) => <Cell key={item.name} fill={item.value >= 0 ? CHART_POSITIVE : CHART_NEGATIVE} />)}
                </Bar>
              </BarChart>
            )}
          </ResponsiveContainer>
        </div>
        <Paragraph type="secondary" style={{ marginBottom: 0, fontSize: 12 }}>
          {allocationShare ? t('attr.contributionMixHint') : t('attr.mixedSignHint')}
        </Paragraph>
      </Card>
      <Alert
        showIcon
        type="success"
        message={`${t('attr.conservation')}: ${report.allocations.reduce((sum, item) => sum + item.signed_contribution_qty, 0)} + ${report.unexplained_signed_gap} = ${(report.override_qty ?? detail.override_qty) - (report.conservation_anchor_qty ?? report.recommended_qty ?? detail.recommended_qty)}`}
        description={`${t('attr.anchoredOn')}: ${report.conservation_anchor_qty ?? report.recommended_qty ?? detail.recommended_qty} · ${t('attr.shapleyMethod')}: ${report.shapley_method}${report.shapley_samples ? ` · ${report.shapley_samples}` : ''}`}
      />
      <Table rowKey="cause_code" size="small" columns={allocationColumns} dataSource={report.allocations} pagination={false} />
    </Space>
  ) : <Empty description={t('attr.reportPending')} />;

  const trace = (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Alert
        showIcon
        type={debugRawIo ? 'warning' : 'info'}
        message={t(debugRawIo ? 'attr.traceDebugRaw' : 'attr.traceRedacted')}
      />
      {detail.attempts.map((attempt) => (
        <Card size="small" key={attempt.attempt_id} title={`${t('attr.attempt')} ${attempt.attempt_number}`}>
          <Descriptions size="small" column={3}>
            <Descriptions.Item label={t('attr.colStatus')}>{attempt.status}</Descriptions.Item>
            <Descriptions.Item label={t('attr.modelCalls')}>{attempt.model_calls} / 15</Descriptions.Item>
            <Descriptions.Item label={t('attr.toolCalls')}>{attempt.tool_calls} / 10</Descriptions.Item>
            <Descriptions.Item label={t('attr.rawLog')}>
              <Button
                size="small"
                icon={<DownloadOutlined />}
                disabled={!attempt.raw_log_available}
                onClick={() => void downloadRawLog(attempt.attempt_number)}
              >
                {t('attr.downloadRawLog')}
              </Button>
            </Descriptions.Item>
            {attempt.error_message && <Descriptions.Item label={t('attr.error')} span={3}>{attempt.error_message}</Descriptions.Item>}
          </Descriptions>
        </Card>
      ))}
      <Timeline
        items={detail.trace_events.map((event) => ({
          children: (
            <div>
              <Text strong>{event.name}</Text>
              <br />
              <Text type="secondary">{new Date(event.created_at).toLocaleString()} · {event.event_type}</Text>
              {event.payload && <pre className="attribution-trace-payload">{JSON.stringify(event.payload, null, 2)}</pre>}
            </div>
          ),
        }))}
      />
    </Space>
  );

  const versions = (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      {detail.reports.map((item) => (
        <Card size="small" key={item.report_id} title={`${t('attr.reportVersion')} ${item.version}`}>
          <Text>{item.summary}</Text>
        </Card>
      ))}
      {detail.reviews.map((review) => (
        <Card size="small" key={review.review_id} title={`${review.action} · v${review.version}`}>
          <Descriptions size="small" column={2}>
            <Descriptions.Item label={t('attr.reviewer')}>{review.reviewer}</Descriptions.Item>
            <Descriptions.Item label={t('attr.colUpdated')}>{new Date(review.created_at).toLocaleString()}</Descriptions.Item>
            <Descriptions.Item label={t('attr.reviewComment')} span={2}>{review.comment ?? '-'}</Descriptions.Item>
          </Descriptions>
        </Card>
      ))}
    </Space>
  );

  return (
    <Space direction="vertical" size="large" className="page-stack">
      <Breadcrumb items={[
        { title: <a onClick={() => navigate('/attribution')}>{t('attr.title')}</a> },
        { title: detail.case_id },
      ]} />

      <Card>
        <Space className="toolbar" align="start" wrap>
          <div>
            <Space wrap>
              <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/attribution')} />
              <Title level={3} style={{ margin: 0 }}>{detail.goods_name ?? detail.goods_code}</Title>
              <AttributionStatusTag status={detail.status} lang={lang} />
              {detail.partial && <Tag color="warning">Partial</Tag>}
            </Space>
            <Text type="secondary">{detail.case_id} · Run {detail.run_id} · v{detail.case_version}</Text>
          </div>
          <Space wrap>
            <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>{t('attr.refresh')}</Button>
            {ACTIVE.has(detail.status) && <Button danger icon={<StopOutlined />} loading={actionLoading} onClick={() => void cancel()}>{t('attr.cancel')}</Button>}
            {['FAILED', 'CANCELLED'].includes(detail.status) && <Button icon={<SyncOutlined />} loading={actionLoading} onClick={() => void retry()}>{t('attr.retry')}</Button>}
            {canReview && <Button type="primary" icon={<EditOutlined />} onClick={() => setReviewOpen(true)}>{t('attr.review')}</Button>}
          </Space>
        </Space>
      </Card>

      {detail.status === 'HUMAN_APPROVED' && <Alert showIcon type="success" icon={<CheckCircleOutlined />} message={t('attr.gateUnlocked')} />}
      {detail.status === 'FAILED' && <Alert showIcon type="error" icon={<CloseCircleOutlined />} message={t('attr.failedManual')} description={detail.error_message} />}
      {detail.status === 'CHANGES_REQUESTED' && <Alert showIcon type="warning" message={t('attr.changesRequested')} />}

      <Card>
        <Steps
          current={progressStep(detail.status)}
          status={detail.status === 'FAILED' ? 'error' : detail.status === 'HUMAN_APPROVED' ? 'finish' : 'process'}
          items={[
            { title: t('attr.stepReceived') },
            { title: t('attr.stepDiagnosing') },
            { title: t('attr.stepConverging') },
            { title: t('attr.stepReview') },
            { title: t('attr.stepApproved') },
          ]}
        />
      </Card>

      <div className="metric-grid">
        <Card><Statistic title={t('attr.recommended')} value={detail.recommended_qty} /></Card>
        <Card><Statistic title={t('attr.override')} value={detail.override_qty} /></Card>
        <Card><Statistic title={t('attr.colGap')} value={detail.signed_gap} valueStyle={{ color: detail.signed_gap < 0 ? 'var(--danger-text)' : 'var(--success-text)' }} /></Card>
        <Card>
          <Statistic title={t('attr.colCoverage')} value={Math.round((report?.coverage_ratio ?? 0) * 100)} suffix="%" />
          <Progress percent={Math.round((report?.coverage_ratio ?? 0) * 100)} size="small" />
        </Card>
      </div>

      <Card>
        <Tabs items={[
          { key: 'overview', label: t('attr.tabOverview'), children: overview },
          { key: 'evidence', label: t('attr.tabEvidence'), children: evidence },
          { key: 'allocation', label: t('attr.tabAllocation'), children: allocation },
          { key: 'trace', label: t('attr.tabTrace'), children: trace },
          { key: 'versions', label: t('attr.tabVersions'), children: versions },
        ]} />
      </Card>

      <AttributionReviewDrawer
        open={reviewOpen}
        attributionCase={detail}
        onClose={() => setReviewOpen(false)}
        onSaved={setDetail}
      />
    </Space>
  );
}
