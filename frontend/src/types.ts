export type Scenario = 'standard' | 'fresh' | 'longtail' | 'new' | 'promo' | 'holiday' | 'season' | 'stockout';

export interface Sku {
  goods_code: string;
  goods_name: string;
  category: string;
}

export interface Shop {
  shop_code: string;
  shop_name: string;
  city: string;
}

export interface Candidate {
  qty: number;
  method: string;
  risk: string;
}

export interface ResultInventory {
  on_hand: number;
  in_transit: number;
  reserved: number;
  expiring: number;
  days_to_expiry?: number | null;
  expiring_sellable?: number;
  expiring_waste?: number;
  recent_zero_days: number;
  available: number;
  phantom_suspect: boolean;
  source: 'synthetic' | 'override';
  overridden: string[];
}

export interface ReplenishmentResult {
  shop: string;
  sku: string;
  scenario: Scenario;
  candidates: Candidate[];
  chosen_qty: number;
  final_qty: number;
  safety_stock: number;
  target_stock: number;
  explanation: string;
  sku_name?: string;
  trace_id: string;
  exception: boolean;
  engine?: 'algo' | 'agent';
  run_id?: string;
  params?: ReplenishmentParams;
  fill_rate?: number;
  service_z?: number;
  // continuous-review (s,S) fields
  flow?: 'A';
  lead_time?: number;
  apply_date?: string;
  arrival_date?: string;
  shelf_date?: string;
  triggered?: boolean;
  trigger?: boolean;
  reorder_point?: number;
  order_up_to?: number;
  position?: number;
  inventory?: ResultInventory;
  attribution_case_id?: string | null;
  attribution_status?: AttributionCaseStatus | null;
}

// ---- Current inventory feed (门店当前库存) ----
export interface InventoryRow {
  goods_code: string;
  goods_name: string;
  category: string;
  on_hand: number;
  in_transit: number;
  reserved: number;
  expiring: number;
  days_to_expiry: number;
  recent_zero_days: number;
  available: number;
  daily_mean?: number;
  source: 'synthetic' | 'override';
  overridden: string[];
}

export interface StoreInventory {
  shop_code: string;
  date: string;
  rows: InventoryRow[];
}

export type InventoryFields = Partial<Pick<InventoryRow, 'on_hand' | 'in_transit' | 'reserved' | 'expiring' | 'days_to_expiry' | 'recent_zero_days'>>;

// ---- Staff adjustment of the final order quantity ----
export interface AdjustItem {
  sku: string;
  final_qty: number;
  reason_code: string;
  reason_text?: string;
  event_id?: string;
}

export type AttributionOutputLanguage = 'zh-CN' | 'en-US';

export interface AdjustResult {
  run_id: string;
  changed: number;
  total_qty: number;
  results: ReplenishmentResult[];
  job_id?: string | null;
  case_ids?: string[];
  gate_status?: RunGateStatus;
  run_version?: number;
}

// ---- Replenishment parameter configuration ----
export type ParamType = 'percent' | 'int' | 'float';

export type ReplenishmentParams = Record<string, number>;

export interface ParamSpec {
  key: string;
  type: ParamType;
  scope: 'store' | 'sku';
  default: number;
  min: number;
  max: number;
  step: number;
  label: string;
  label_en: string;
  help: string;
  help_en: string;
}

export interface ConfigSchema {
  params: ParamSpec[];
  defaults: ReplenishmentParams;
}

export type ConfigLevel = 'sku' | 'store' | 'none';

export interface ConfigStatus {
  configured: boolean;
  level: ConfigLevel;
  shop_code: string;
  goods_code: string | null;
}

export interface EffectiveConfig {
  shop_code: string;
  goods_code: string | null;
  effective: ReplenishmentParams;
  store: ReplenishmentParams | null;
  sku: ReplenishmentParams | null;
  sku_overrides: Record<string, ReplenishmentParams>;
}

export interface StoreSkuRow {
  goods_code: string;
  goods_name: string;
  category: string;
  level: ConfigLevel;
  effective: ReplenishmentParams;
  sku: ReplenishmentParams | null;
}

export interface StoreSkuConfig {
  shop_code: string;
  store: ReplenishmentParams | null;
  params: ParamSpec[];
  rows: StoreSkuRow[];
}

export interface SkuBulkItem {
  goods_code: string;
  params: ReplenishmentParams;
}

export interface SkuBulkResult {
  shop_code: string;
  saved: { goods_code: string; params: ReplenishmentParams }[];
  errors: { goods_code: string; error: string }[];
}

export interface AgentStatus {
  available: boolean;
  sdk_installed: boolean;
  endpoint_configured: boolean;
  model_configured: boolean;
  model?: string | null;
  reason: string;
}

export interface ExceptionItem extends ReplenishmentResult {
  override_type: 'high' | 'low';
  reason: string;
  suggested_action: string;
}

export interface RunSummary {
  run_id: string;
  ts: string;
  engine: 'algo' | 'agent';
  kind: 'batch' | 'single';
  shop_code: string;
  shop_name: string;
  count: number;
  exception_count: number;
  trigger_count?: number;
  total_qty: number;
  adjusted?: boolean;
  status?: RunGateStatus;
  version?: number;
  submitted_at?: string | null;
  attribution_total?: number;
  attribution_approved?: number;
  attribution_pending?: number;
}

export interface RunDetail extends RunSummary {
  results: ReplenishmentResult[];
}

export interface TraceStep {
  step: number;
  name: string;
  skill: string;
  input?: string;
  output?: string;
  delta?: number;
  formula?: string[];
  type: 'algo' | 'soft';
}

export interface TraceDetail {
  trace_id: string;
  shop: string;
  sku: string;
  scenario: Scenario;
  steps: TraceStep[];
  final_qty: number;
  summary: string;
}

// ---- Mandatory pre-submit attribution gate ----
export type RunGateStatus =
  | 'DRAFT'
  | 'ATTRIBUTION_RUNNING'
  | 'ATTRIBUTION_REVIEW_REQUIRED'
  | 'READY_TO_SUBMIT'
  | 'SUBMITTED_LOCKED';

export type AttributionCaseStatus =
  | 'QUEUED'
  | 'RUNNING'
  | 'NEEDS_REVIEW'
  | 'HUMAN_APPROVED'
  | 'CHANGES_REQUESTED'
  | 'FAILED'
  | 'CANCELLED'
  | 'SUPERSEDED';

export interface AttributionBlocker {
  sku: string;
  case_id?: string | null;
  code: string;
  message: string;
  status?: AttributionCaseStatus | null;
}

export interface RunSubmissionReadiness {
  run_id: string;
  run_version: number;
  status: RunGateStatus;
  ready: boolean;
  modified_count: number;
  approved_count: number;
  blockers: AttributionBlocker[];
}

export interface RunSubmissionResult {
  run_id: string;
  status: 'SUBMITTED_LOCKED';
  submitted_at: string;
  submitted_by: string;
  run_version: number;
}

export interface AttributionJob {
  job_id: string;
  run_id: string;
  status: string;
  case_ids: string[];
  total_cases: number;
  completed_cases: number;
  created_at: string;
  updated_at: string;
}

export interface AttributionEvidence {
  evidence_id: string;
  evidence_type: string;
  title: string;
  source: string;
  source_version?: string;
  observed_at?: string | null;
  fresh?: boolean;
  payload?: Record<string, unknown>;
}

export interface CounterfactualResult {
  cause_code: string;
  baseline_qty: number;
  counterfactual_qty: number;
  signed_impact_qty: number;
  inputs?: Record<string, unknown>;
}

export interface AttributionAllocation {
  cause_code: string;
  domain: string;
  label?: string;
  signed_contribution_qty: number;
  absolute_contribution_weight: number;
  expected_direction?: 'INCREASE' | 'DECREASE' | 'NONE';
  explanation: string;
  evidence_refs: string[];
  counterfactual_result?: CounterfactualResult;
}

export interface KnowledgeCandidate {
  candidate_id: string;
  cause_code: string;
  kind: string;
  domain: string;
  scope_label: 'SHOP_SKU' | 'SHOP_CATEGORY' | 'SKU' | 'CATEGORY' | string;
  scope: { shop_code?: string | null; goods_code?: string | null; category?: string | null };
  prior_value?: number | null;
  proposed_value?: number | null;
  boundary_value?: number | null;
  acceptable: boolean;
  applies_from?: string | null;
  applies_to?: string | null;
  baseline_qty?: number | null;
  target_qty?: number | null;
  achieved_qty?: number | null;
  impact_qty?: number | null;
  calibration_status: 'EXACT' | 'APPROXIMATE' | 'UNREACHABLE' | 'ALREADY_CORRECT' | 'BLOCKED' | string;
  magnitude_ratio?: number | null;
  magnitude_plausible: boolean;
  recurring: boolean;
  condition: string;
  explanation: string;
  evidence_refs: string[];
  substitute_goods_code?: string | null;
  blocked_reason?: string | null;
  statement: string;
  effect: string;
  proposal_version?: string;
}

export interface AttributionReport {
  report_id: string;
  version: number;
  summary: string;
  model_summary?: string;
  primary_cause?: string | null;
  signed_gap: number;
  unexplained_signed_gap: number;
  coverage_ratio: number;
  recommended_qty?: number | null;
  override_qty?: number | null;
  baseline_qty?: number | null;
  /** What the engine would have ordered with the named causes switched off. */
  bare_baseline_qty?: number | null;
  /** The quantity the allocations are measured from and conserve against. */
  conservation_anchor_qty?: number | null;
  explained_signed_qty?: number | null;
  replay_drift_qty?: number | null;
  partial: boolean;
  risk_flags: string[];
  unknown_cause_codes?: string[];
  unquantifiable_cause_codes?: string[];
  conflicts: string[];
  allocations: AttributionAllocation[];
  knowledge_candidates?: KnowledgeCandidate[];
  evidence: AttributionEvidence[];
  shapley_method: 'exact' | 'sampled' | string;
  shapley_samples?: number | null;
  shapley_error_estimate?: number | null;
  created_at: string;
}

export interface AttributionAttempt {
  attempt_id: string;
  attempt_number: number;
  status: string;
  started_at: string;
  finished_at?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  model_calls: number;
  tool_calls: number;
  raw_log_available: boolean;
  duration_ms?: number | null;
}

export interface AttributionReview {
  review_id: string;
  version: number;
  action: 'APPROVE' | 'REQUEST_CHANGES' | 'AMEND_AND_APPROVE' | 'MANUAL_AND_APPROVE';
  reviewer: string;
  comment?: string | null;
  report_version: number;
  publish_knowledge: boolean;
  created_at: string;
}

export interface AttributionTraceEvent {
  trace_event_id: string;
  event_type: string;
  name: string;
  created_at: string;
  payload?: Record<string, unknown>;
}

export interface AttributionCaseSummary {
  case_id: string;
  job_id: string;
  run_id: string;
  event_id: string;
  version: number;
  case_version: number;
  shop_code: string;
  shop_name?: string;
  goods_code: string;
  goods_name?: string;
  decision_date: string;
  recommended_qty: number;
  override_qty: number;
  output_language: AttributionOutputLanguage;
  signed_gap: number;
  direction: 'UP' | 'DOWN';
  status: AttributionCaseStatus;
  partial: boolean;
  coverage_ratio?: number | null;
  report_version?: number | null;
  created_at: string;
  updated_at: string;
}

export interface AttributionCaseDetail extends AttributionCaseSummary {
  source_trace_id: string;
  reason_code: string;
  reason_text?: string | null;
  snapshot_hash: string;
  latest_report?: AttributionReport | null;
  reports: AttributionReport[];
  attempts: AttributionAttempt[];
  reviews: AttributionReview[];
  trace_events: AttributionTraceEvent[];
  error_code?: string | null;
  error_message?: string | null;
  superseded_by_case_id?: string | null;
}

export interface AttributionCaseList {
  items: AttributionCaseSummary[];
  total: number;
  page: number;
  page_size: number;
}

export interface AttributionCaseFilters {
  status?: AttributionCaseStatus;
  shop_code?: string;
  goods_code?: string;
  direction?: 'UP' | 'DOWN';
  date_from?: string;
  date_to?: string;
  job_id?: string;
  page?: number;
  page_size?: number;
}

export interface ReviewCauseInput {
  cause_code: string;
  domain?: string;
  signed_contribution_qty: number;
  explanation: string;
  evidence_refs?: string[];
}

export type KnowledgeRejectReason =
  | 'WRONG_CAUSE'
  | 'NOT_THE_DRIVER'
  | 'WRONG_SCOPE'
  | 'WRONG_MAGNITUDE'
  | 'ONE_OFF_EVENT'
  | 'INSUFFICIENT_EVIDENCE'
  | 'ALREADY_KNOWN'
  | 'OTHER';

export const KNOWLEDGE_REJECT_REASONS: KnowledgeRejectReason[] = [
  'WRONG_CAUSE',
  'NOT_THE_DRIVER',
  'WRONG_SCOPE',
  'WRONG_MAGNITUDE',
  'ONE_OFF_EVENT',
  'INSUFFICIENT_EVIDENCE',
  'ALREADY_KNOWN',
  'OTHER',
];

export interface KnowledgeDecisionInput {
  candidate_id: string;
  decision: 'ACCEPT' | 'AMEND' | 'REJECT';
  cause_code?: string;
  kind?: string;
  domain?: string;
  scope_label?: string;
  scope_category?: string | null;
  applies_from?: string | null;
  applies_to?: string | null;
  prior_value?: number | null;
  proposed_value?: number | null;
  condition?: string;
  reject_reason?: KnowledgeRejectReason;
  note?: string;
  expires_at?: string;
}

export interface AttributionReviewRequest {
  action: AttributionReview['action'];
  expected_version: number;
  expected_report_version?: number;
  comment?: string;
  causes?: ReviewCauseInput[];
  summary?: string;
  knowledge_decisions?: KnowledgeDecisionInput[];
  publish_knowledge?: boolean;
  knowledge_scope?: 'SHOP_SKU' | 'SKU' | 'CATEGORY' | 'DOMAIN';
  knowledge_expires_at?: string;
}

export interface AttributionReviewCount {
  needs_review: number;
}

export type UserRole = 'buyer' | 'admin';

export interface CurrentUser {
  username: string;
  role: UserRole;
}

export interface WorkerStatus {
  running: boolean;
  healthy: boolean;
  last_poll_error: string | null;
}

export interface AdminLease {
  case_id: string;
  worker_id: string;
  expires_at: string;
  expired: boolean;
  seconds_remaining: number;
  shop_code: string | null;
  goods_code: string | null;
  state: string | null;
}

export interface AdminOverview {
  generated_at: string;
  cases_by_state: Record<string, number>;
  runs_by_state: Record<string, number>;
  pending_review: number;
  backlog: {
    queued: number;
    running: number;
    oldest_started_at: string | null;
    oldest_age_seconds: number | null;
  };
  leases: AdminLease[];
  attribution_worker: WorkerStatus;
  forecast_pairs: number;
  agent_runtime: AgentStatus;
}

export interface AdminJob {
  job_id: string;
  run_id: string;
  status: string;
  total_cases: number;
  completed_cases: number;
  pending_review: number;
  cases_by_state: Record<string, number>;
  created_at: string;
  updated_at: string;
}

export interface AdminJobList {
  items: AdminJob[];
  total: number;
  page: number;
  page_size: number;
}

export interface AdminReviewQueueItem extends AttributionCaseSummary {
  reason_code: string;
  error_code: string | null;
  /** Dismissing this case clears the badge but leaves its run unsubmittable. */
  blocks_run: boolean;
  run_state: string | null;
  run_locked: boolean;
}

export interface AdminReviewQueue {
  items: AdminReviewQueueItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface BulkDismissResult {
  succeeded: AttributionCaseSummary[];
  failed: { case_id: string; code: string; message: string }[];
  succeeded_count: number;
  failed_count: number;
}

export interface KnowledgeEntry {
  knowledge_id: string;
  case_id: string;
  scope: Record<string, string>;
  expires_at: string;
  version: number;
}

export interface DiagnosticAgent {
  agent_id: string;
  version: string;
  domain: string;
  applicable_scenarios: string[];
  required_evidence: string[];
  deterministic_tools: string[];
  finding_schema: string;
  enabled: boolean;
  default_model_deployment_env: string;
}
