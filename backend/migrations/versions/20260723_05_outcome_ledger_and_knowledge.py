"""add outcome ledger and restructure knowledge entries

Revision ID: 20260723_05
Revises: 20260723_04
Create Date: 2026-07-24

Adds the ground-truth half of the attribution loop: daily sales facts, the
decision outcomes they score, and a knowledge entry that carries a value and a
confidence instead of only a pointer to a report.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON


revision = "20260723_05"
down_revision = "20260723_04"
branch_labels = None
depends_on = None


def _json_type():
    return JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    op.create_table(
        "daily_sales_facts",
        sa.Column("fact_id", sa.String(36), primary_key=True),
        sa.Column("shop_code", sa.String(128), nullable=False),
        sa.Column("goods_code", sa.String(128), nullable=False),
        sa.Column("sales_date", sa.String(10), nullable=False),
        sa.Column("units_sold", sa.Float(), nullable=False, server_default="0"),
        sa.Column("lost_sales_units", sa.Float(), nullable=False, server_default="0"),
        sa.Column("stockout", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(64), nullable=False, server_default="POS"),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("shop_code", "goods_code", "sales_date", name="uq_daily_sales_line"),
    )
    op.create_index("ix_daily_sales_facts_shop_code", "daily_sales_facts", ["shop_code"])
    op.create_index("ix_daily_sales_facts_goods_code", "daily_sales_facts", ["goods_code"])
    op.create_index("ix_daily_sales_facts_sales_date", "daily_sales_facts", ["sales_date"])
    op.create_index("ix_daily_sales_line", "daily_sales_facts",
                    ["shop_code", "goods_code", "sales_date"])

    op.create_table(
        "decision_outcomes",
        sa.Column("outcome_id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(128), nullable=True),
        sa.Column("case_id", sa.String(36), nullable=True),
        sa.Column("shop_code", sa.String(128), nullable=False),
        sa.Column("goods_code", sa.String(128), nullable=False),
        sa.Column("decision_date", sa.String(10), nullable=False),
        sa.Column("source", sa.String(24), nullable=False, server_default="OVERRIDE"),
        sa.Column("recommended_qty", sa.Integer(), nullable=False),
        sa.Column("ordered_qty", sa.Integer(), nullable=False),
        sa.Column("opening_position", sa.Float(), nullable=False, server_default="0"),
        sa.Column("case_pack", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("window_start", sa.String(10), nullable=False),
        sa.Column("window_end", sa.String(10), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("observed_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("units_sold", sa.Float(), nullable=True),
        sa.Column("lost_sales_units", sa.Float(), nullable=True),
        sa.Column("actual_demand", sa.Float(), nullable=True),
        sa.Column("ideal_qty", sa.Float(), nullable=True),
        sa.Column("engine_error", sa.Float(), nullable=True),
        sa.Column("human_error", sa.Float(), nullable=True),
        sa.Column("stockout_units", sa.Float(), nullable=True),
        sa.Column("overstock_units", sa.Float(), nullable=True),
        sa.Column("verdict", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("detail", _json_type(), nullable=False, server_default="{}"),
        sa.Column("snapshot_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("shop_code", "goods_code", "decision_date",
                            name="uq_decision_outcome_line"),
    )
    for column in ("run_id", "case_id", "shop_code", "goods_code",
                   "decision_date", "source", "status", "verdict",
                   "window_start", "window_end"):
        op.create_index(f"ix_decision_outcomes_{column}", "decision_outcomes", [column])
    op.create_index("ix_decision_outcome_window", "decision_outcomes",
                    ["window_start", "window_end", "status"])

    # batch mode so SQLite can alter columns; existing rows keep their scope JSON
    # and are left as CANDIDATE with zero weight, since none of them carry the
    # evidence the new promotion path requires.
    with op.batch_alter_table("knowledge_entries") as batch:
        batch.alter_column("case_id", existing_type=sa.String(36), nullable=True)
        batch.alter_column("report_id", existing_type=sa.String(36), nullable=True)
        batch.alter_column("expires_at", existing_type=sa.DateTime(timezone=True), nullable=True)
        batch.add_column(sa.Column("scope_shop_code", sa.String(128), nullable=True))
        batch.add_column(sa.Column("scope_goods_code", sa.String(128), nullable=True))
        batch.add_column(sa.Column("scope_category", sa.String(128), nullable=True))
        batch.add_column(sa.Column("applies_from", sa.String(10), nullable=True))
        batch.add_column(sa.Column("applies_to", sa.String(10), nullable=True))
        batch.add_column(sa.Column("kind", sa.String(64), nullable=True))
        batch.add_column(sa.Column("prior_value", sa.Float(), nullable=True))
        batch.add_column(sa.Column("proposed_value", sa.Float(), nullable=True))
        batch.add_column(sa.Column("status", sa.String(24), nullable=False,
                                   server_default="CANDIDATE"))
        batch.add_column(sa.Column("effective_weight", sa.Float(), nullable=False,
                                   server_default="0"))
        batch.add_column(sa.Column("evidence", _json_type(), nullable=False,
                                   server_default="{}"))
        batch.add_column(sa.Column("posterior", _json_type(), nullable=False,
                                   server_default="{}"))
        batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_knowledge_entries_scope_shop_code", "knowledge_entries",
                    ["scope_shop_code"])
    op.create_index("ix_knowledge_entries_scope_goods_code", "knowledge_entries",
                    ["scope_goods_code"])
    op.create_index("ix_knowledge_entries_kind", "knowledge_entries", ["kind"])
    op.create_index("ix_knowledge_entries_status", "knowledge_entries", ["status"])
    op.create_index("ix_knowledge_scope", "knowledge_entries",
                    ["scope_shop_code", "scope_goods_code", "kind", "status"])


def downgrade() -> None:
    for name in ("ix_knowledge_scope", "ix_knowledge_entries_status",
                 "ix_knowledge_entries_kind", "ix_knowledge_entries_scope_goods_code",
                 "ix_knowledge_entries_scope_shop_code"):
        op.drop_index(name, table_name="knowledge_entries")
    with op.batch_alter_table("knowledge_entries") as batch:
        for column in ("updated_at", "posterior", "evidence", "effective_weight", "status",
                       "proposed_value", "prior_value", "kind", "applies_to", "applies_from",
                       "scope_category", "scope_goods_code", "scope_shop_code"):
            batch.drop_column(column)
    op.drop_table("decision_outcomes")
    op.drop_table("daily_sales_facts")
