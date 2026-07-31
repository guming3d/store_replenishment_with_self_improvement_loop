import {
  IconClock,
  IconLoader2,
  IconAlertCircle,
  IconCircleCheck,
  IconEdit,
  IconCircleX,
  IconBan,
  IconArrowsExchange,
} from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { Tag } from '@/components/ui';
import type { AttributionCaseStatus, RunGateStatus } from '../types';

const iconSize = 14;

const caseMeta: Record<AttributionCaseStatus, { color: string; icon: ReactNode; zh: string; en: string }> = {
  QUEUED: { color: 'default', icon: <IconClock size={iconSize} />, zh: '排队中', en: 'Queued' },
  RUNNING: { color: 'processing', icon: <IconLoader2 size={iconSize} />, zh: '归因中', en: 'Running' },
  NEEDS_REVIEW: { color: 'warning', icon: <IconAlertCircle size={iconSize} />, zh: '待审核', en: 'Needs review' },
  HUMAN_APPROVED: { color: 'success', icon: <IconCircleCheck size={iconSize} />, zh: '已批准', en: 'Approved' },
  CHANGES_REQUESTED: { color: 'orange', icon: <IconEdit size={iconSize} />, zh: '要求修订', en: 'Changes requested' },
  FAILED: { color: 'error', icon: <IconCircleX size={iconSize} />, zh: '失败', en: 'Failed' },
  CANCELLED: { color: 'default', icon: <IconBan size={iconSize} />, zh: '已取消', en: 'Cancelled' },
  SUPERSEDED: { color: 'default', icon: <IconArrowsExchange size={iconSize} />, zh: '已被取代', en: 'Superseded' },
};

const runMeta: Record<RunGateStatus, { color: string; zh: string; en: string }> = {
  DRAFT: { color: 'default', zh: '草稿', en: 'Draft' },
  ATTRIBUTION_RUNNING: { color: 'processing', zh: '归因中', en: 'Attribution running' },
  ATTRIBUTION_REVIEW_REQUIRED: { color: 'warning', zh: '归因待审核', en: 'Attribution review required' },
  READY_TO_SUBMIT: { color: 'success', zh: '可提交', en: 'Ready to submit' },
  SUBMITTED_LOCKED: { color: 'blue', zh: '已提交并锁定', en: 'Submitted and locked' },
};

export function caseStatusLabel(status: string, lang: 'zh' | 'en' = 'zh') {
  const meta = caseMeta[status as AttributionCaseStatus];
  if (!meta) return status;
  return lang === 'zh' ? meta.zh : meta.en;
}

export function caseStatusColor(status: string) {
  return caseMeta[status as AttributionCaseStatus]?.color ?? 'default';
}

export function AttributionStatusTag({ status, lang = 'zh' }: { status: AttributionCaseStatus; lang?: 'zh' | 'en' }) {
  const meta = caseMeta[status];
  return <Tag color={meta.color} icon={meta.icon}>{lang === 'zh' ? meta.zh : meta.en}</Tag>;
}

export function RunGateStatusTag({ status, lang = 'zh' }: { status: RunGateStatus; lang?: 'zh' | 'en' }) {
  const meta = runMeta[status];
  return <Tag color={meta.color}>{lang === 'zh' ? meta.zh : meta.en}</Tag>;
}
