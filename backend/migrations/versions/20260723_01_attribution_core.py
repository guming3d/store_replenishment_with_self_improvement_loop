"""create attribution core tables

Revision ID: 20260723_01
Revises:
Create Date: 2026-07-23
"""
from alembic import op
import sqlalchemy as sa

revision = "20260723_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("replenishment_runs",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("draft_overrides", sa.JSON(), nullable=False),
        sa.Column("accepted_overrides", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_replenishment_runs_state", "replenishment_runs", ["state"])
    op.create_table("rejection_events",
        sa.Column("event_id", sa.String(128), primary_key=True),
        sa.Column("run_id", sa.String(128), sa.ForeignKey("replenishment_runs.run_id"), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("case_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_rejection_events_run_id", "rejection_events", ["run_id"])
    op.create_index("ix_rejection_events_case_id", "rejection_events", ["case_id"])
    op.create_table("attribution_jobs",
        sa.Column("job_id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(128), sa.ForeignKey("replenishment_runs.run_id"), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_attribution_jobs_run_id", "attribution_jobs", ["run_id"])
    op.create_index("ix_attribution_jobs_state", "attribution_jobs", ["state"])
    op.create_table("attribution_cases",
        sa.Column("case_id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("attribution_jobs.job_id"), nullable=False),
        sa.Column("run_id", sa.String(128), sa.ForeignKey("replenishment_runs.run_id"), nullable=False),
        sa.Column("shop_code", sa.String(128), nullable=False), sa.Column("goods_code", sa.String(128), nullable=False),
        sa.Column("decision_date", sa.String(10), nullable=False), sa.Column("case_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(40), nullable=False), sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("recommended_qty", sa.Integer(), nullable=False), sa.Column("override_qty", sa.Integer(), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False), sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("reason_code", sa.String(128), nullable=False), sa.Column("reason_text", sa.Text()),
        sa.Column("partial", sa.Boolean(), nullable=False), sa.Column("approved_report_id", sa.String(36)),
        sa.Column("lease_owner", sa.String(128)), sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    for column in ("job_id", "run_id", "shop_code", "goods_code", "decision_date", "state", "lease_expires_at"):
        op.create_index(f"ix_attribution_cases_{column}", "attribution_cases", [column])
    op.create_index("ix_case_active_key", "attribution_cases",
                    ["run_id", "shop_code", "goods_code", "decision_date", "state"])
    op.create_table("execution_attempts",
        sa.Column("attempt_id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("attribution_cases.case_id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False), sa.Column("state", sa.String(40), nullable=False),
        sa.Column("error_code", sa.String(128)), sa.Column("error_detail", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("case_id", "number", name="uq_attempt_number"))
    op.create_index("ix_execution_attempts_case_id", "execution_attempts", ["case_id"])
    op.create_index("ix_execution_attempts_state", "execution_attempts", ["state"])
    op.create_table("attribution_reports",
        sa.Column("report_id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("attribution_cases.case_id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("partial", sa.Boolean(), nullable=False), sa.Column("source", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("case_id", "version", name="uq_report_version"))
    op.create_index("ix_attribution_reports_case_id", "attribution_reports", ["case_id"])
    op.create_table("human_reviews",
        sa.Column("review_id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("attribution_cases.case_id"), nullable=False),
        sa.Column("report_id", sa.String(36), sa.ForeignKey("attribution_reports.report_id"), nullable=False),
        sa.Column("reviewer_subject", sa.String(256), nullable=False), sa.Column("action", sa.String(40), nullable=False),
        sa.Column("notes", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_human_reviews_case_id", "human_reviews", ["case_id"])
    op.create_table("knowledge_entries",
        sa.Column("knowledge_id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("attribution_cases.case_id"), nullable=False),
        sa.Column("report_id", sa.String(36), sa.ForeignKey("attribution_reports.report_id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("invalidated", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_knowledge_entries_case_id", "knowledge_entries", ["case_id"])
    op.create_index("ix_knowledge_entries_expires_at", "knowledge_entries", ["expires_at"])
    op.create_table("attribution_trace_events",
        sa.Column("trace_event_id", sa.String(36), primary_key=True), sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("attribution_cases.case_id"), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False), sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_attribution_trace_events_trace_id", "attribution_trace_events", ["trace_id"])
    op.create_index("ix_attribution_trace_events_case_id", "attribution_trace_events", ["case_id"])
    op.create_table("worker_leases",
        sa.Column("case_id", sa.String(36), sa.ForeignKey("attribution_cases.case_id"), primary_key=True),
        sa.Column("worker_id", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_worker_leases_worker_id", "worker_leases", ["worker_id"])
    op.create_index("ix_worker_leases_expires_at", "worker_leases", ["expires_at"])
    op.create_table("run_submission_audits",
        sa.Column("audit_id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(128), sa.ForeignKey("replenishment_runs.run_id"), nullable=False),
        sa.Column("status", sa.String(40), nullable=False), sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_run_submission_audits_run_id", "run_submission_audits", ["run_id"])


def downgrade() -> None:
    for table in ("run_submission_audits", "worker_leases", "attribution_trace_events", "knowledge_entries", "human_reviews",
                  "attribution_reports", "execution_attempts", "attribution_cases", "attribution_jobs",
                  "rejection_events", "replenishment_runs"):
        op.drop_table(table)
