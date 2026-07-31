import { DeleteOutlined, PlusOutlined } from '@/components/ui/icons';
import {
  Alert,
  Button,
  Checkbox,
  DatePicker,
  Divider,
  Drawer,
  Form,
  Input,
  InputNumber,
  Radio,
  Select,
  Space,
  Table,
  Typography,
  message,
} from '@/components/ui';
import type { ColumnsType } from '@/components/ui';
import { useEffect, useMemo, useState } from 'react';
import { apiErrorMessage, submitAttributionReview } from '../api';
import { useI18n } from '../i18n';
import KnowledgeCandidateReview, {
  initialDrafts,
  type CandidateDrafts,
} from './KnowledgeCandidateReview';
import type {
  AttributionCaseDetail,
  AttributionReviewRequest,
  KnowledgeDecisionInput,
  ReviewCauseInput,
} from '../types';

const { Text, Title } = Typography;
const causeOptions = [
  'SEASONAL_SHIFT',
  'HOLIDAY_EFFECT',
  'SUBSTITUTION_TRANSFER',
  'OTHER',
];

interface Props {
  open: boolean;
  attributionCase: AttributionCaseDetail;
  onClose: () => void;
  onSaved: (updated: AttributionCaseDetail) => void;
}

export default function AttributionReviewDrawer({ open, attributionCase, onClose, onSaved }: Props) {
  const { t } = useI18n();
  const report = attributionCase.latest_report;
  const defaultAction: AttributionReviewRequest['action'] =
    attributionCase.status === 'FAILED' ? 'MANUAL_AND_APPROVE'
      : report?.partial ? 'AMEND_AND_APPROVE'
        : 'APPROVE';
  const [action, setAction] = useState<AttributionReviewRequest['action']>(defaultAction);
  const [causes, setCauses] = useState<ReviewCauseInput[]>([]);
  const [comment, setComment] = useState('');
  const [summary, setSummary] = useState('');
  const [publishKnowledge, setPublishKnowledge] = useState(false);
  const [knowledgeScope, setKnowledgeScope] = useState<AttributionReviewRequest['knowledge_scope']>('SHOP_SKU');
  const [knowledgeExpiresAt, setKnowledgeExpiresAt] = useState<string>();
  const [drafts, setDrafts] = useState<CandidateDrafts>({});
  const [submitting, setSubmitting] = useState(false);

  const candidates = useMemo(
    () => report?.knowledge_candidates ?? [], [report]);

  useEffect(() => {
    if (!open) return;
    const nextAction = attributionCase.status === 'FAILED' ? 'MANUAL_AND_APPROVE'
      : report?.partial ? 'AMEND_AND_APPROVE'
        : 'APPROVE';
    setAction(nextAction);
    setCauses((report?.allocations ?? []).map((item) => ({
      cause_code: item.cause_code,
      domain: item.domain,
      signed_contribution_qty: item.signed_contribution_qty,
      explanation: item.explanation,
      evidence_refs: item.evidence_refs,
    })));
    setComment('');
    setSummary(report?.summary ?? '');
    setPublishKnowledge(false);
    setKnowledgeScope('SHOP_SKU');
    setKnowledgeExpiresAt(undefined);
    setDrafts(initialDrafts(report?.knowledge_candidates ?? []));
  }, [open, attributionCase.status, report]);

  const contributionTotal = useMemo(
    () => causes.reduce((total, item) => total + Number(item.signed_contribution_qty || 0), 0),
    [causes],
  );
  const residual = attributionCase.signed_gap - contributionTotal;
  const requiresCauses = action === 'AMEND_AND_APPROVE' || action === 'MANUAL_AND_APPROVE';

  const changeCause = (index: number, patch: Partial<ReviewCauseInput>) => {
    setCauses((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  };

  const columns: ColumnsType<ReviewCauseInput> = [
    {
      title: t('attr.reviewCause'),
      dataIndex: 'cause_code',
      width: 210,
      render: (value: string, _, index) => (
        <Select
          style={{ width: '100%' }}
          value={value}
          onChange={(next) => changeCause(index, { cause_code: next })}
          options={causeOptions.map((cause) => ({ value: cause, label: cause }))}
        />
      ),
    },
    {
      title: t('attr.reviewContribution'),
      dataIndex: 'signed_contribution_qty',
      width: 150,
      render: (value: number, _, index) => (
        <InputNumber
          style={{ width: '100%' }}
          precision={0}
          value={value}
          onChange={(next) => changeCause(index, { signed_contribution_qty: Number(next ?? 0) })}
        />
      ),
    },
    {
      title: t('attr.reviewExplanation'),
      dataIndex: 'explanation',
      render: (value: string, item, index) => (
        <Input
          value={value}
          status={!value.trim() ? 'error' : undefined}
          onChange={(event) => changeCause(index, { explanation: event.target.value })}
        />
      ),
    },
    {
      title: '',
      width: 48,
      render: (_, __, index) => (
        <Button
          type="text"
          danger
          icon={<DeleteOutlined />}
          aria-label={t('attr.removeCause')}
          onClick={() => setCauses((current) => current.filter((_, itemIndex) => itemIndex !== index))}
        />
      ),
    },
  ];

  const knowledgeDecisions = useMemo<KnowledgeDecisionInput[]>(() => {
    if (action === 'REQUEST_CHANGES') return [];
    return candidates.flatMap<KnowledgeDecisionInput>((candidate) => {
      const draft = drafts[candidate.candidate_id];
      if (!draft || draft.decision === 'SKIP') return [];
      if (draft.decision === 'REJECT') {
        return [{
          candidate_id: candidate.candidate_id,
          decision: 'REJECT' as const,
          cause_code: candidate.cause_code,
          kind: candidate.kind,
          domain: candidate.domain,
          scope_label: candidate.scope_label,
          scope_category: candidate.scope.category ?? undefined,
          reject_reason: draft.reject_reason,
          note: draft.note?.trim() || undefined,
        }];
      }
      const amended = draft.decision === 'AMEND';
      return [{
        candidate_id: candidate.candidate_id,
        decision: draft.decision,
        cause_code: candidate.cause_code,
        kind: candidate.kind,
        domain: candidate.domain,
        scope_label: candidate.scope_label,
        scope_category: candidate.scope.category ?? undefined,
        applies_from: (amended ? draft.applies_from : candidate.applies_from) ?? undefined,
        applies_to: (amended ? draft.applies_to : candidate.applies_to) ?? undefined,
        prior_value: candidate.prior_value ?? undefined,
        proposed_value: (amended ? draft.proposed_value : candidate.proposed_value) ?? undefined,
        condition: (amended ? draft.condition : candidate.condition)?.trim() || undefined,
        note: draft.note?.trim() || undefined,
      }];
    });
  }, [action, candidates, drafts]);

  const validate = (): string | undefined => {
    if (action === 'REQUEST_CHANGES' && !comment.trim()) return t('attr.reviewCommentRequired');
    if (requiresCauses && causes.length === 0) return t('attr.reviewCauseRequired');
    if (requiresCauses && !summary.trim()) return t('attr.reviewSummaryRequired');
    if (causes.some((cause) => !cause.cause_code || !cause.explanation.trim())) {
      return t('attr.reviewExplanationRequired');
    }
    if (new Set(causes.map((cause) => cause.cause_code)).size !== causes.length) {
      return t('attr.reviewCauseUnique');
    }
    for (const decision of knowledgeDecisions) {
      if (decision.decision === 'REJECT' && !decision.reject_reason) {
        return t('kc.rejectReasonRequired');
      }
      if (decision.decision === 'AMEND') {
        if (decision.proposed_value === undefined || decision.proposed_value === null) {
          return t('kc.amendValueRequired');
        }
        if (!decision.applies_from || !decision.applies_to) return t('kc.amendWindowRequired');
      }
    }
    if (publishKnowledge && (
      !knowledgeExpiresAt || new Date(knowledgeExpiresAt).getTime() <= Date.now()
    )) return t('attr.knowledgeFutureExpiryRequired');
    return undefined;
  };

  const submit = async () => {
    const validation = validate();
    if (validation) {
      message.error(validation);
      return;
    }
    setSubmitting(true);
    try {
      const request: AttributionReviewRequest = {
        action,
        expected_version: attributionCase.version,
        expected_report_version: report?.version,
        comment: comment.trim() || undefined,
        causes: requiresCauses ? causes.map((cause) => ({
          ...cause,
          explanation: cause.explanation.trim(),
        })) : undefined,
        summary: requiresCauses ? summary.trim() : undefined,
        knowledge_decisions: knowledgeDecisions.length ? knowledgeDecisions : undefined,
        publish_knowledge: action === 'REQUEST_CHANGES' ? false : publishKnowledge,
        knowledge_scope: publishKnowledge ? knowledgeScope : undefined,
        knowledge_expires_at: publishKnowledge ? knowledgeExpiresAt : undefined,
      };
      const updated = await submitAttributionReview(attributionCase.case_id, request);
      message.success(t('attr.reviewSaved'));
      onSaved(updated);
      onClose();
    } catch (error) {
      message.error(t('attr.reviewFailed', { reason: apiErrorMessage(error) }));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Drawer
      width={900}
      open={open}
      onClose={onClose}
      destroyOnClose
      title={t('attr.reviewTitle')}
      extra={<Button type="primary" loading={submitting} onClick={() => void submit()}>{t('attr.reviewSubmit')}</Button>}
    >
      <Space direction="vertical" size="large" style={{ width: '100%' }}>
        <Alert
          showIcon
          type="info"
          message={t('attr.reviewGateNotice')}
          description={t('attr.reviewGateDescription')}
        />

        <div>
          <Title level={5}>{t('attr.reviewOutcome')}</Title>
          <Radio.Group value={action} onChange={(event) => setAction(event.target.value as AttributionReviewRequest['action'])}>
            <Space direction="vertical">
              <Radio value="APPROVE" disabled={!report || !!report.partial || attributionCase.status === 'FAILED'}>
                {t('attr.approveOriginal')}
              </Radio>
              <Radio value={attributionCase.status === 'FAILED' ? 'MANUAL_AND_APPROVE' : 'AMEND_AND_APPROVE'}>
                {attributionCase.status === 'FAILED' ? t('attr.manualApprove') : t('attr.amendApprove')}
              </Radio>
              <Radio value="REQUEST_CHANGES">{t('attr.requestChanges')}</Radio>
            </Space>
          </Radio.Group>
        </div>

        {requiresCauses && (
          <>
            <Divider />
            <Space className="toolbar">
              <div>
                <Title level={5}>{t('attr.reviewCauses')}</Title>
                <Text type="secondary">{t('attr.reviewResidualHint')}</Text>
              </div>
              <Button
                icon={<PlusOutlined />}
                onClick={() => setCauses((current) => [...current, {
                  cause_code: 'OTHER',
                  domain: 'manual',
                  signed_contribution_qty: 0,
                  explanation: '',
                  evidence_refs: [],
                }])}
              >
                {t('attr.addCause')}
              </Button>
            </Space>
            <Table rowKey={(_, index) => String(index)} size="small" columns={columns} dataSource={causes} pagination={false} />
            <Alert
              type={Math.abs((contributionTotal + residual) - attributionCase.signed_gap) < 0.0001 ? 'success' : 'error'}
              message={`${t('attr.conservation')}: ${contributionTotal} + ${residual} = ${attributionCase.signed_gap}`}
            />
            <Form layout="vertical">
              <Form.Item label={t('attr.reviewSummary')}>
                <Input.TextArea rows={5} value={summary} onChange={(event) => setSummary(event.target.value)} />
              </Form.Item>
            </Form>
          </>
        )}

        <Form layout="vertical">
          <Form.Item
            label={t('attr.reviewComment')}
            required={action === 'REQUEST_CHANGES'}
          >
            <Input.TextArea rows={3} value={comment} onChange={(event) => setComment(event.target.value)} />
          </Form.Item>
        </Form>

        {action !== 'REQUEST_CHANGES' && (
          <>
            <Divider />
            <div>
              <Title level={5}>{t('kc.title')}</Title>
              <Text type="secondary">{t('kc.subtitle')}</Text>
            </div>
            <KnowledgeCandidateReview
              candidates={candidates}
              drafts={drafts}
              onChange={setDrafts}
            />
            {candidates.length === 0 && (
              <>
                <Checkbox checked={publishKnowledge} onChange={(event) => setPublishKnowledge(event.target.checked)}>
                  {t('attr.publishKnowledge')}
                </Checkbox>
                {publishKnowledge && (
                  <Space wrap>
                    <Select
                      value={knowledgeScope}
                      style={{ width: 220 }}
                      onChange={(value) => setKnowledgeScope(value as AttributionReviewRequest['knowledge_scope'])}
                      options={[
                        { value: 'SHOP_SKU', label: t('attr.scopeShopSku') },
                        { value: 'SKU', label: t('attr.scopeSku') },
                        { value: 'CATEGORY', label: t('attr.scopeCategory') },
                        { value: 'DOMAIN', label: t('attr.scopeDomain') },
                      ]}
                    />
                    <DatePicker
                      placeholder={t('attr.knowledgeExpiry')}
                      onChange={(value) => setKnowledgeExpiresAt(
                        value ? value.endOf('day').toISOString() : undefined)}
                    />
                  </Space>
                )}
              </>
            )}
          </>
        )}
      </Space>
    </Drawer>
  );
}
