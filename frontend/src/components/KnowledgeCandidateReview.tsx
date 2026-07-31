import {
  Alert,
  Card,
  DatePicker,
  Input,
  InputNumber,
  Radio,
  Select,
  Space,
  Tag,
  Typography,
} from '@/components/ui';
import { useI18n } from '../i18n';
import type { KnowledgeCandidate, KnowledgeRejectReason } from '../types';
import { KNOWLEDGE_REJECT_REASONS } from '../types';

const { Text } = Typography;

export type CandidateDecision = 'SKIP' | 'ACCEPT' | 'AMEND' | 'REJECT';

export interface CandidateDraft {
  decision: CandidateDecision;
  proposed_value?: number | null;
  applies_from?: string;
  applies_to?: string;
  condition?: string;
  reject_reason?: KnowledgeRejectReason;
  note?: string;
}

export type CandidateDrafts = Record<string, CandidateDraft>;

export function initialDrafts(candidates: KnowledgeCandidate[]): CandidateDrafts {
  return Object.fromEntries(candidates.map((candidate) => [candidate.candidate_id, {
    decision: 'SKIP' as CandidateDecision,
    proposed_value: candidate.proposed_value ?? undefined,
    applies_from: candidate.applies_from ?? undefined,
    applies_to: candidate.applies_to ?? undefined,
    condition: candidate.condition ?? '',
  }]));
}

interface Props {
  candidates: KnowledgeCandidate[];
  drafts: CandidateDrafts;
  onChange: (drafts: CandidateDrafts) => void;
}

export default function KnowledgeCandidateReview({ candidates, drafts, onChange }: Props) {
  const { t } = useI18n();

  if (candidates.length === 0) {
    return <Text type="secondary">{t('kc.empty')}</Text>;
  }

  const patch = (candidateId: string, next: Partial<CandidateDraft>) => {
    onChange({ ...drafts, [candidateId]: { ...drafts[candidateId], ...next } });
  };

  const scopeText = (candidate: KnowledgeCandidate) => [
    candidate.scope.shop_code,
    candidate.scope.goods_code,
    candidate.scope.category,
  ].filter(Boolean).join(' · ') || candidate.scope_label;

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {candidates.map((candidate) => {
        const draft = drafts[candidate.candidate_id] ?? { decision: 'SKIP' as CandidateDecision };
        return (
          <Card key={candidate.candidate_id} size="small">
            <Space direction="vertical" size="small" style={{ width: '100%' }}>
              <Space wrap size="small">
                <Tag>{candidate.cause_code}</Tag>
                <Tag color={candidate.recurring ? 'blue' : 'default'}>
                  {candidate.recurring ? t('kc.recurring') : t('kc.oneOff')}
                </Tag>
                {!candidate.acceptable && <Tag color="orange">{t('kc.blocked')}</Tag>}
              </Space>

              <Text strong>{candidate.statement}</Text>
              {candidate.effect && <Text type="secondary">{candidate.effect}</Text>}
              {candidate.blocked_reason && <Text type="secondary">{candidate.blocked_reason}</Text>}

              {candidate.condition && (
                <Text type="secondary">{t('kc.condition')}：{candidate.condition}</Text>
              )}
              <Text type="secondary">
                {t('kc.scope')}：{scopeText(candidate)}
                {candidate.applies_from
                  ? ` · ${t('kc.window')}：${candidate.applies_from} ~ ${candidate.applies_to ?? ''}`
                  : ''}
              </Text>

              {!candidate.acceptable && (
                <Alert type="warning" message={t('kc.blockedHint')} />
              )}

              <Radio.Group
                value={draft.decision}
                onChange={(event) => patch(
                  candidate.candidate_id,
                  { decision: event.target.value as CandidateDecision },
                )}
              >
                <Space wrap size="middle">
                  <Radio value="SKIP">{t('kc.decisionSkip')}</Radio>
                  <Radio value="ACCEPT" disabled={!candidate.acceptable}>{t('kc.decisionAccept')}</Radio>
                  <Radio value="AMEND" disabled={!candidate.acceptable}>{t('kc.decisionAmend')}</Radio>
                  <Radio value="REJECT">{t('kc.decisionReject')}</Radio>
                </Space>
              </Radio.Group>

              {draft.decision === 'AMEND' && (
                <Space direction="vertical" size="small" style={{ width: '100%' }}>
                  <Space wrap size="small">
                    <InputNumber
                      style={{ width: 140 }}
                      value={draft.proposed_value ?? undefined}
                      placeholder={t('kc.proposedValue')}
                      onChange={(next) => patch(candidate.candidate_id, {
                        proposed_value: next === null || next === undefined ? undefined : Number(next),
                      })}
                    />
                    <DatePicker
                      value={draft.applies_from}
                      placeholder={t('kc.appliesFrom')}
                      onChange={(_, dateString) => patch(
                        candidate.candidate_id, { applies_from: dateString || undefined })}
                    />
                    <DatePicker
                      value={draft.applies_to}
                      placeholder={t('kc.appliesTo')}
                      onChange={(_, dateString) => patch(
                        candidate.candidate_id, { applies_to: dateString || undefined })}
                    />
                  </Space>
                  <Input
                    value={draft.condition ?? ''}
                    placeholder={t('kc.conditionEdit')}
                    onChange={(event) => patch(
                      candidate.candidate_id, { condition: event.target.value })}
                  />
                  <Input
                    value={draft.note ?? ''}
                    placeholder={t('kc.note')}
                    onChange={(event) => patch(candidate.candidate_id, { note: event.target.value })}
                  />
                </Space>
              )}

              {draft.decision === 'REJECT' && (
                <Space direction="vertical" size="small" style={{ width: '100%' }}>
                  <Select
                    style={{ width: 260 }}
                    value={draft.reject_reason}
                    placeholder={t('kc.rejectReason')}
                    onChange={(value) => patch(
                      candidate.candidate_id, { reject_reason: value as KnowledgeRejectReason })}
                    options={KNOWLEDGE_REJECT_REASONS.map((reason) => ({
                      value: reason, label: t(`kc.reason.${reason}`),
                    }))}
                  />
                  <Input.TextArea
                    rows={2}
                    value={draft.note ?? ''}
                    placeholder={t('kc.noteRejectHint')}
                    onChange={(event) => patch(candidate.candidate_id, { note: event.target.value })}
                  />
                </Space>
              )}
            </Space>
          </Card>
        );
      })}
    </Space>
  );
}
