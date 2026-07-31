from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


def _json_type():
    return JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    pass


class ReplenishmentRun(Base):
    __tablename__ = "replenishment_runs"
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    state: Mapped[str] = mapped_column(String(40), default="DRAFT", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(_json_type(), default=dict)
    draft_overrides: Mapped[dict[str, Any]] = mapped_column(_json_type(), default=dict)
    accepted_overrides: Mapped[dict[str, Any] | None] = mapped_column(_json_type(), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RejectionEvent(Base):
    __tablename__ = "rejection_events"
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("replenishment_runs.run_id"), index=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(_json_type())
    case_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AttributionJob(Base):
    __tablename__ = "attribution_jobs"
    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("replenishment_runs.run_id"), index=True)
    state: Mapped[str] = mapped_column(String(40), default="QUEUED", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AttributionCase(Base):
    __tablename__ = "attribution_cases"
    case_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("attribution_jobs.job_id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("replenishment_runs.run_id"), index=True)
    event_id: Mapped[str | None] = mapped_column(String(128), unique=True, index=True, nullable=True)
    source_trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    shop_code: Mapped[str] = mapped_column(String(128), index=True)
    goods_code: Mapped[str] = mapped_column(String(128), index=True)
    decision_date: Mapped[str] = mapped_column(String(10), index=True)
    case_version: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(40), default="QUEUED", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    recommended_qty: Mapped[int] = mapped_column(Integer)
    override_qty: Mapped[int] = mapped_column(Integer)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict[str, Any]] = mapped_column(_json_type())
    reason_code: Mapped[str] = mapped_column(String(128))
    reason_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_language: Mapped[str] = mapped_column(
        String(10), default="zh-CN", server_default="zh-CN")
    partial: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    superseded_by_case_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    approved_report_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_case_active_key", "run_id", "shop_code", "goods_code", "decision_date", "state"),)


class ExecutionAttempt(Base):
    __tablename__ = "execution_attempts"
    attempt_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("attribution_cases.case_id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(40), index=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("case_id", "number", name="uq_attempt_number"),)


class AttributionReport(Base):
    __tablename__ = "attribution_reports"
    report_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("attribution_cases.case_id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    report: Mapped[dict[str, Any]] = mapped_column(_json_type())
    partial: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Denormalised from ``report["operator_claim"]`` so the concordance summary
    #: is a grouped scan instead of a JSON walk over every report ever written.
    #: Null for manual reports, which are written without evidence and so have
    #: no verdict to record, and for reports predating the verdict.
    claim_verdict: Mapped[str | None] = mapped_column(String(24), nullable=True)
    source: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("case_id", "version", name="uq_report_version"),)


class HumanReview(Base):
    __tablename__ = "human_reviews"
    review_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("attribution_cases.case_id"), index=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("attribution_reports.report_id"))
    reviewer_subject: Mapped[str] = mapped_column(String(256))
    action: Mapped[str] = mapped_column(String(40))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DailySalesFact(Base):
    """Raw daily sales as delivered by the store's POS feed.

    Stored separately from the outcomes it scores because the feed arrives one
    day at a time while a judgement window spans several: an outcome has to be
    recomputed each time a new day lands, and that is only reproducible if the
    days themselves are kept.
    """
    __tablename__ = "daily_sales_facts"
    fact_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    shop_code: Mapped[str] = mapped_column(String(128), index=True)
    goods_code: Mapped[str] = mapped_column(String(128), index=True)
    sales_date: Mapped[str] = mapped_column(String(10), index=True)
    units_sold: Mapped[float] = mapped_column(Float, default=0.0)
    #: Demand the shelf could not serve. Without it an empty shelf reads as low
    #: demand and every downward override looks correct.
    lost_sales_units: Mapped[float] = mapped_column(Float, default=0.0)
    stockout: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(64), default="POS")
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("shop_code", "goods_code", "sales_date", name="uq_daily_sales_line"),
        Index("ix_daily_sales_line", "shop_code", "goods_code", "sales_date"),
    )


class DecisionOutcome(Base):
    """What the store actually needed, versus what each side ordered.

    One row per decided line, created when the run is submitted rather than when
    sales arrive, so that lines the store manager *accepted* are scored too. Only
    sampling overrides would teach the loop exclusively from disagreement.
    """
    __tablename__ = "decision_outcomes"
    outcome_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    case_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    shop_code: Mapped[str] = mapped_column(String(128), index=True)
    goods_code: Mapped[str] = mapped_column(String(128), index=True)
    decision_date: Mapped[str] = mapped_column(String(10), index=True)
    #: OVERRIDE when the store manager changed the quantity, ACCEPTED when the
    #: recommendation was taken as-is.
    source: Mapped[str] = mapped_column(String(24), default="OVERRIDE", index=True)
    recommended_qty: Mapped[int] = mapped_column(Integer)
    ordered_qty: Mapped[int] = mapped_column(Integer)
    opening_position: Mapped[float] = mapped_column(Float, default=0.0)
    case_pack: Mapped[int] = mapped_column(Integer, default=1)
    horizon_days: Mapped[int] = mapped_column(Integer)
    window_start: Mapped[str] = mapped_column(String(10), index=True)
    window_end: Mapped[str] = mapped_column(String(10), index=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    observed_days: Mapped[int] = mapped_column(Integer, default=0)
    units_sold: Mapped[float | None] = mapped_column(Float, nullable=True)
    lost_sales_units: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_demand: Mapped[float | None] = mapped_column(Float, nullable=True)
    ideal_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    engine_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    human_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    stockout_units: Mapped[float | None] = mapped_column(Float, nullable=True)
    overstock_units: Mapped[float | None] = mapped_column(Float, nullable=True)
    verdict: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(_json_type(), default=dict)
    snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("shop_code", "goods_code", "decision_date",
                         name="uq_decision_outcome_line"),
        Index("ix_decision_outcome_window", "window_start", "window_end", "status"),
    )


class KnowledgeEntry(Base):
    """A learned, store- and SKU-specific parameter proposal.

    This used to be a pointer to a report plus an expiry date, which stored no
    knowledge and had no consumer. It now carries the proposed value, the scope
    it applies to, the evidence behind it and a posterior that decides how much
    of it the engine is allowed to believe.
    """
    __tablename__ = "knowledge_entries"
    knowledge_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    #: Provenance of the first supporting case. Nullable because mined knowledge
    #: is derived from many cases and belongs to none of them.
    case_id: Mapped[str | None] = mapped_column(
        ForeignKey("attribution_cases.case_id"), index=True, nullable=True)
    report_id: Mapped[str | None] = mapped_column(
        ForeignKey("attribution_reports.report_id"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    #: Legacy free-form scope, retained so pre-existing rows keep their payload.
    scope: Mapped[dict[str, Any]] = mapped_column(_json_type(), default=dict)
    scope_shop_code: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    scope_goods_code: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    scope_category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    applies_from: Mapped[str | None] = mapped_column(String(10), nullable=True)
    applies_to: Mapped[str | None] = mapped_column(String(10), nullable=True)
    kind: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    prior_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    proposed_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        String(24), default="CANDIDATE", server_default="CANDIDATE", index=True)
    #: 0..1 blend weight. Zero means the engine ignores this entry entirely.
    effective_weight: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    evidence: Mapped[dict[str, Any]] = mapped_column(_json_type(), default=dict)
    posterior: Mapped[dict[str, Any]] = mapped_column(_json_type(), default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True)
    invalidated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        Index("ix_knowledge_scope", "scope_shop_code", "scope_goods_code", "kind", "status"),
    )


class KnowledgeRejection(Base):
    """A knowledge candidate a reviewer turned down, and why.

    Accepted knowledge is only half the signal. When a reviewer rejects a
    candidate they are stating that the agent reached a wrong conclusion on a
    case whose ground truth they know -- which is the most direct evidence there
    is about where the diagnostic agents, the seeds or the cause vocabulary are
    failing. The old review kept only an action enum and a free-text note that
    was null in practice, so every one of those judgements was discarded.

    Deliberately not a knowledge entry with a REJECTED status: a rejection has
    no value, no scope to resolve and no posterior to evolve, and mixing it into
    the table the engine reads would put rows there that must never be applied.
    """
    __tablename__ = "knowledge_rejections"
    rejection_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("attribution_cases.case_id"), index=True)
    report_id: Mapped[str | None] = mapped_column(
        ForeignKey("attribution_reports.report_id"), nullable=True)
    review_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    candidate_id: Mapped[str] = mapped_column(String(128))
    cause_code: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    kind: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    domain: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    scope_shop_code: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    scope_goods_code: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    scope_category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prior_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    proposed_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason_code: Mapped[str] = mapped_column(String(48), index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The candidate exactly as the agent produced it, so a later prompt change
    #: can be replayed against the cases it got wrong.
    candidate: Mapped[dict[str, Any]] = mapped_column(_json_type(), default=dict)
    reviewer_subject: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("ix_knowledge_rejection_reason", "reason_code", "cause_code", "created_at"),
    )


class TraceEvent(Base):
    __tablename__ = "attribution_trace_events"
    trace_event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(128), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("attribution_cases.case_id"), index=True)
    event_type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(_json_type())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkerLease(Base):
    __tablename__ = "worker_leases"
    case_id: Mapped[str] = mapped_column(ForeignKey("attribution_cases.case_id"), primary_key=True)
    worker_id: Mapped[str] = mapped_column(String(128), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SubmissionAudit(Base):
    __tablename__ = "run_submission_audits"
    audit_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("replenishment_runs.run_id"), index=True)
    status: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(_json_type())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
