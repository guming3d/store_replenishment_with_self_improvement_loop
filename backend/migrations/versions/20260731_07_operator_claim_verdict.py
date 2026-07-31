"""grade the operator's stated reason against the evidence

Revision ID: 20260731_07
Revises: 20260729_06
Create Date: 2026-07-31

The reason code a store manager picks when overriding a quantity was stored on
every Case and read by nothing: the coordinator is shown it but forbidden to
treat it as proof, and no deterministic step consumed it. Reports now carry a
verdict comparing that claim with the causes the evidence actually supports.

The verdict lives in the report JSON; this column denormalises it so the
concordance summary can group in SQL rather than walking every report. It is
nullable on purpose -- manual reports are written without evidence and reports
written before this revision have no verdict, and neither may be silently
counted as agreement.
"""
from alembic import op
import sqlalchemy as sa


revision = "20260731_07"
down_revision = "20260729_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attribution_reports",
        sa.Column("claim_verdict", sa.String(24), nullable=True),
    )
    op.create_index(
        "ix_reports_claim_verdict", "attribution_reports",
        ["claim_verdict", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_reports_claim_verdict", table_name="attribution_reports")
    op.drop_column("attribution_reports", "claim_verdict")
