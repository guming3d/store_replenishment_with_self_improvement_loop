"""add case contract fields

Revision ID: 20260723_02
Revises: 20260723_01
Create Date: 2026-07-23
"""
from alembic import op
import sqlalchemy as sa

revision = "20260723_02"
down_revision = "20260723_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("attribution_cases") as batch:
        batch.add_column(sa.Column("event_id", sa.String(128), nullable=True))
        batch.add_column(sa.Column("source_trace_id", sa.String(128), nullable=True))
        batch.add_column(sa.Column("error_code", sa.String(128), nullable=True))
        batch.add_column(sa.Column("error_message", sa.Text(), nullable=True))
        batch.add_column(sa.Column("superseded_by_case_id", sa.String(36), nullable=True))
        batch.create_index("ix_attribution_cases_event_id", ["event_id"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("attribution_cases") as batch:
        batch.drop_index("ix_attribution_cases_event_id")
        batch.drop_column("superseded_by_case_id")
        batch.drop_column("error_message")
        batch.drop_column("error_code")
        batch.drop_column("source_trace_id")
        batch.drop_column("event_id")
