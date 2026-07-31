"""record reviewer verdicts on knowledge candidates

Revision ID: 20260729_06
Revises: 20260723_05
Create Date: 2026-07-29

Adds the rejection half of the review. Accepted knowledge already had a home;
a rejected candidate had none, so every time a reviewer told the system that a
diagnostic agent had reached a wrong conclusion, the judgement was discarded.
Those rejections are the most direct evidence available about where the agents,
the seeds or the cause vocabulary are failing, so they are now first-class rows
that can be tallied per cause and per reason.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON


revision = "20260729_06"
down_revision = "20260723_05"
branch_labels = None
depends_on = None


def _json_type():
    return JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    op.create_table(
        "knowledge_rejections",
        sa.Column("rejection_id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), nullable=False),
        sa.Column("report_id", sa.String(36), nullable=True),
        sa.Column("review_id", sa.String(36), nullable=True),
        sa.Column("candidate_id", sa.String(128), nullable=False),
        sa.Column("cause_code", sa.String(128), nullable=True),
        sa.Column("kind", sa.String(64), nullable=True),
        sa.Column("domain", sa.String(128), nullable=True),
        sa.Column("scope_shop_code", sa.String(128), nullable=True),
        sa.Column("scope_goods_code", sa.String(128), nullable=True),
        sa.Column("scope_category", sa.String(128), nullable=True),
        sa.Column("prior_value", sa.Float(), nullable=True),
        sa.Column("proposed_value", sa.Float(), nullable=True),
        sa.Column("reason_code", sa.String(48), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("candidate", _json_type(), nullable=False, server_default="{}"),
        sa.Column("reviewer_subject", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["attribution_cases.case_id"]),
        sa.ForeignKeyConstraint(["report_id"], ["attribution_reports.report_id"]),
    )
    op.create_index("ix_knowledge_rejections_case_id", "knowledge_rejections", ["case_id"])
    op.create_index("ix_knowledge_rejections_review_id", "knowledge_rejections", ["review_id"])
    op.create_index("ix_knowledge_rejections_cause_code", "knowledge_rejections", ["cause_code"])
    op.create_index("ix_knowledge_rejections_kind", "knowledge_rejections", ["kind"])
    op.create_index("ix_knowledge_rejections_domain", "knowledge_rejections", ["domain"])
    op.create_index("ix_knowledge_rejections_scope_shop_code", "knowledge_rejections",
                    ["scope_shop_code"])
    op.create_index("ix_knowledge_rejections_scope_goods_code", "knowledge_rejections",
                    ["scope_goods_code"])
    op.create_index("ix_knowledge_rejection_reason", "knowledge_rejections",
                    ["reason_code", "cause_code", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_rejection_reason", table_name="knowledge_rejections")
    for column in ("scope_goods_code", "scope_shop_code", "domain", "kind", "cause_code",
                   "review_id", "case_id"):
        op.drop_index(f"ix_knowledge_rejections_{column}", table_name="knowledge_rejections")
    op.drop_table("knowledge_rejections")
