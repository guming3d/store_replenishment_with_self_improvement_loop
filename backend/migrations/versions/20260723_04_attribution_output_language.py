"""add attribution output language

Revision ID: 20260723_04
Revises: 20260723_03
Create Date: 2026-07-23
"""
from alembic import op
import sqlalchemy as sa


revision = "20260723_04"
down_revision = "20260723_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("attribution_cases") as batch:
        batch.add_column(sa.Column(
            "output_language",
            sa.String(10),
            nullable=False,
            server_default="zh-CN",
        ))


def downgrade() -> None:
    with op.batch_alter_table("attribution_cases") as batch:
        batch.drop_column("output_language")
