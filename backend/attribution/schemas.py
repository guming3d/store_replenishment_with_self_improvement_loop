from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RunState(StrEnum):
    DRAFT = "DRAFT"
    ATTRIBUTION_RUNNING = "ATTRIBUTION_RUNNING"
    ATTRIBUTION_REVIEW_REQUIRED = "ATTRIBUTION_REVIEW_REQUIRED"
    READY_TO_SUBMIT = "READY_TO_SUBMIT"
    SUBMITTED_LOCKED = "SUBMITTED_LOCKED"


class CaseState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


class DraftOverrideEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=128)
    source_run_id: str = Field(min_length=1, max_length=128)
    source_trace_id: str = Field(min_length=1, max_length=128)
    shop_code: str = Field(min_length=1, max_length=128)
    goods_code: str = Field(min_length=1, max_length=128)
    decision_date: date
    recommended_qty: int = Field(ge=0)
    override_qty: int = Field(ge=0)
    override_timestamp: datetime
    reason_code: str = Field(min_length=1, max_length=128)
    reason_text: str | None = Field(default=None, max_length=4000)
    output_language: Literal["zh-CN", "en-US"] = "zh-CN"
    recommendation_snapshot: dict[str, Any]
    snapshot_hash: str = Field(min_length=64, max_length=64)
    case_version: int | None = Field(default=None, ge=1)


class AdjustDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1, max_length=128)
    expected_run_version: int | None = Field(default=None, ge=1)
    events: list[DraftOverrideEvent] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def matching_run_ids(self) -> "AdjustDraftRequest":
        if any(event.source_run_id != self.run_id for event in self.events):
            raise ValueError("each event source_run_id must match run_id")
        if len({event.event_id for event in self.events}) != len(self.events):
            raise ValueError("event_id values must be unique within a request")
        return self


class ContributionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cause_code: str = Field(min_length=1, max_length=128)
    domain: str = Field(min_length=1, max_length=128)
    signed_contribution_qty: float
    explanation: str = Field(min_length=1, max_length=4000)
    evidence_refs: list[str] = Field(default_factory=list)


class ManualReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_case_version: int = Field(ge=1)
    contributions: list[ContributionInput] = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=4000)


class KnowledgeKind(StrEnum):
    SEASONAL_FACTOR = "SEASONAL_FACTOR"
    HOLIDAY_FACTOR = "HOLIDAY_FACTOR"
    SUBSTITUTION_RATE = "SUBSTITUTION_RATE"
    DEMAND_LEVEL = "DEMAND_LEVEL"
    FILL_RATE = "FILL_RATE"
    SHELF_MAX = "SHELF_MAX"


class KnowledgeRejectReason(StrEnum):
    """Why a reviewer turned a candidate down.

    A closed vocabulary rather than free text: these counts are the report card
    for the diagnostic agents, and a tally is only possible if the reasons are
    comparable across reviewers.
    """
    WRONG_CAUSE = "WRONG_CAUSE"
    NOT_THE_DRIVER = "NOT_THE_DRIVER"
    WRONG_SCOPE = "WRONG_SCOPE"
    WRONG_MAGNITUDE = "WRONG_MAGNITUDE"
    ONE_OFF_EVENT = "ONE_OFF_EVENT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    ALREADY_KNOWN = "ALREADY_KNOWN"
    OTHER = "OTHER"


class KnowledgeDecisionInput(BaseModel):
    """One reviewer verdict on one knowledge candidate.

    ACCEPT takes the agent's calibrated proposition as-is, AMEND takes it with
    the reviewer's corrections, REJECT records that it was wrong and why. All
    three are stored: rejections are what tell the agent's owner where it fails.
    """
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(min_length=1, max_length=128)
    decision: Literal["ACCEPT", "AMEND", "REJECT"]
    cause_code: str | None = Field(default=None, max_length=128)
    kind: KnowledgeKind | None = None
    domain: str | None = Field(default=None, max_length=128)
    scope_label: str | None = Field(default=None, max_length=32)
    scope_shop_code: str | None = Field(default=None, max_length=128)
    scope_goods_code: str | None = Field(default=None, max_length=128)
    scope_category: str | None = Field(default=None, max_length=128)
    applies_from: date | None = None
    applies_to: date | None = None
    prior_value: float | None = None
    proposed_value: float | None = None
    #: The recognisable trigger, carried onto the entry so a future reader can
    #: tell what the rule is *for* rather than only what number it sets.
    condition: str | None = Field(default=None, max_length=500)
    reject_reason: KnowledgeRejectReason | None = None
    note: str | None = Field(default=None, max_length=2000)
    expires_at: datetime | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def decision_is_complete(self) -> "KnowledgeDecisionInput":
        if self.decision == "REJECT":
            if self.reject_reason is None:
                raise ValueError("a rejected candidate requires a reject_reason")
            return self
        if self.kind is None:
            raise ValueError("an accepted candidate requires a knowledge kind")
        if self.proposed_value is None:
            raise ValueError("an accepted candidate requires a proposed value")
        if not (self.scope_shop_code or self.scope_goods_code or self.scope_category):
            raise ValueError("knowledge must be scoped to a shop, goods or category")
        if self.applies_from and self.applies_to and self.applies_to < self.applies_from:
            raise ValueError("applies_to must not precede applies_from")
        return self


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    expected_version: int = Field(ge=1, validation_alias="expected_case_version")
    expected_report_version: int | None = Field(default=None, ge=1)
    action: Literal["APPROVE", "REQUEST_CHANGES", "AMEND_AND_APPROVE", "MANUAL_AND_APPROVE"]
    reviewer_subject: str = Field(min_length=1, max_length=256)
    notes: str | None = Field(default=None, max_length=4000)
    contributions: list[ContributionInput] | None = None
    summary: str | None = Field(default=None, max_length=4000)
    knowledge_decisions: list[KnowledgeDecisionInput] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def validate_report_binding(self) -> "ReviewRequest":
        if self.action != "MANUAL_AND_APPROVE" and self.expected_report_version is None:
            raise ValueError("expected_report_version is required for this action")
        decisions = self.knowledge_decisions or []
        if len({item.candidate_id for item in decisions}) != len(decisions):
            raise ValueError("candidate_id values must be unique within a review")
        return self


class OutcomeStatus(StrEnum):
    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    COMPLETE = "COMPLETE"


class OutcomeVerdict(StrEnum):
    PENDING = "PENDING"
    HUMAN_BETTER = "HUMAN_BETTER"
    ENGINE_BETTER = "ENGINE_BETTER"
    TIE = "TIE"


class KnowledgeStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    SHADOW = "SHADOW"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class DailySalesRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shop_code: str = Field(min_length=1, max_length=128)
    goods_code: str = Field(min_length=1, max_length=128)
    sales_date: date
    units_sold: float = Field(ge=0)
    #: Optional because not every POS feed measures unserved demand. When it is
    #: absent the outcome records the gap rather than assuming zero.
    lost_sales_units: float | None = Field(default=None, ge=0)
    stockout: bool = False


class OutcomeIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(default="POS", min_length=1, max_length=64)
    records: list[DailySalesRecord] = Field(min_length=1, max_length=5000)

    @model_validator(mode="after")
    def unique_lines(self) -> "OutcomeIngestRequest":
        keys = {(r.shop_code, r.goods_code, r.sales_date) for r in self.records}
        if len(keys) != len(self.records):
            raise ValueError("records must be unique per shop, goods and sales_date")
        return self


class OutcomeIngestResponse(BaseModel):
    ingested: int
    outcomes_recomputed: int
    outcomes_completed: int


class DecisionOutcomeResponse(BaseModel):
    outcome_id: str
    run_id: str | None = None
    case_id: str | None = None
    shop_code: str
    goods_code: str
    decision_date: str
    source: Literal["OVERRIDE", "ACCEPTED"]
    recommended_qty: int
    ordered_qty: int
    horizon_days: int
    window_start: str
    window_end: str
    status: OutcomeStatus
    observed_days: int
    actual_demand: float | None = None
    ideal_qty: float | None = None
    engine_error: float | None = None
    human_error: float | None = None
    stockout_units: float | None = None
    overstock_units: float | None = None
    verdict: OutcomeVerdict
    updated_at: datetime | None = None


class AccuracySummaryResponse(BaseModel):
    """Scoreboard the loop is trying to move. Counts only closed windows."""
    scored_count: int
    pending_count: int
    engine_mae: float | None = None
    human_mae: float | None = None
    engine_mape: float | None = None
    human_mape: float | None = None
    human_win_rate: float | None = None
    engine_win_rate: float | None = None
    tie_rate: float | None = None
    accuracy_gain_units: float | None = None
    stockout_units: float = 0.0
    overstock_units: float = 0.0


class KnowledgePublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: KnowledgeKind
    #: At least one of shop or goods, so an approved case cannot silently become
    #: a global rule.
    scope_shop_code: str | None = Field(default=None, max_length=128)
    scope_goods_code: str | None = Field(default=None, max_length=128)
    scope_category: str | None = Field(default=None, max_length=128)
    applies_from: date | None = None
    applies_to: date | None = None
    prior_value: float | None = None
    proposed_value: float | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def scoped_and_ordered(self) -> "KnowledgePublishRequest":
        if not (self.scope_shop_code or self.scope_goods_code or self.scope_category):
            raise ValueError("knowledge must be scoped to a shop, goods or category")
        if self.applies_from and self.applies_to and self.applies_to < self.applies_from:
            raise ValueError("applies_to must not precede applies_from")
        return self


class KnowledgeEntryResponse(BaseModel):
    knowledge_id: str
    case_id: str | None = None
    report_id: str | None = None
    kind: str | None = None
    scope_shop_code: str | None = None
    scope_goods_code: str | None = None
    scope_category: str | None = None
    applies_from: str | None = None
    applies_to: str | None = None
    prior_value: float | None = None
    proposed_value: float | None = None
    status: KnowledgeStatus = KnowledgeStatus.CANDIDATE
    effective_weight: float = 0.0
    posterior: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    invalidated: bool = False
    expires_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class KnowledgeRejectionResponse(BaseModel):
    rejection_id: str
    case_id: str
    report_id: str | None = None
    candidate_id: str
    cause_code: str | None = None
    kind: str | None = None
    domain: str | None = None
    scope_shop_code: str | None = None
    scope_goods_code: str | None = None
    scope_category: str | None = None
    prior_value: float | None = None
    proposed_value: float | None = None
    reason_code: str
    note: str | None = None
    candidate: dict[str, Any] = Field(default_factory=dict)
    reviewer_subject: str
    created_at: datetime | None = None


class AttributionCaseResponse(BaseModel):
    case_id: str
    job_id: str
    run_id: str
    state: CaseState
    version: int
    event_id: str | None = None
    case_version: int | None = None
    shop_code: str | None = None
    shop_name: str | None = None
    goods_code: str | None = None
    goods_name: str | None = None
    decision_date: str | None = None
    recommended_qty: int
    override_qty: int
    output_language: Literal["zh-CN", "en-US"] = "zh-CN"
    signed_gap: int | None = None
    direction: Literal["UP", "DOWN"] | None = None
    status: CaseState | None = None
    snapshot_hash: str
    partial: bool = False
    coverage_ratio: float | None = None
    report_version: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SubmissionReadiness(BaseModel):
    run_id: str
    run_version: int
    status: RunState
    ready: bool
    modified_count: int
    approved_count: int
    blockers: list[dict[str, Any]]
