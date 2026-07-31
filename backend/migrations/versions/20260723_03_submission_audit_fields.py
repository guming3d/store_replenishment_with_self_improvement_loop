"""add submission audit fields

Revision ID: 20260723_03
Revises: 20260723_02
Create Date: 2026-07-23
"""
from alembic import op
import sqlalchemy as sa


revision = "20260723_03"
down_revision = "20260723_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("replenishment_runs") as batch:
        batch.add_column(sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("submitted_by", sa.String(256), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("replenishment_runs") as batch:
        batch.drop_column("submitted_by")
        batch.drop_column("submitted_at")
