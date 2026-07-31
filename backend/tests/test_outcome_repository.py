"""Repository-level tests for the outcome ledger and its knowledge feedback."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from attribution.db import Database, DatabaseSettings
from attribution.deterministic import snapshot_hash
from attribution.repository import AttributionRepository
from attribution.schemas import (
    AdjustDraftRequest, DailySalesRecord, DraftOverrideEvent, KnowledgeKind,
    KnowledgePublishRequest, OutcomeIngestRequest, ReviewRequest,
)

SNAPSHOT = {
    "shop": "shop-1",
    "sku_info": {"goods_code": "sku-1", "goods_name": "Test", "category": "啤酒"},
    "forecast": {"mean": 3, "std": 1, "days": 60},
    "decision_date": "2026-07-23",
    "inventory_snapshot": {"on_hand": 0, "in_transit": 0, "reserved": 0, "expiring": 0},
    "params": {"fill_rate": 0.95, "coverage": 2, "case_pack": 1, "moq": 0, "shelf_max": 999},
}

#: One accepted line and one the store manager overrode, so both paths are covered.
RUN_PAYLOAD = {
    "ts": "2026-07-23T08:00:00",
    "results": [
        {"shop": "shop-1", "sku": "sku-1", "apply_date": "2026-07-23", "chosen_qty": 10,
         "final_qty": 10, "position": 0, "flow": "A", "params": SNAPSHOT["params"]},
        {"shop": "shop-1", "sku": "sku-2", "apply_date": "2026-07-23", "chosen_qty": 8,
         "final_qty": 8, "position": 0, "flow": "A", "params": SNAPSHOT["params"]},
    ],
}


class _Adapter:
    async def submit(self, run_id: str, accepted_overrides: dict[str, int]) -> dict:
        return {"status": "OK"}


@pytest_asyncio.fixture
async def repository():
    database = Database(DatabaseSettings(url="sqlite+aiosqlite:///:memory:"))
    await database.init_schema()
    repo = AttributionRepository(database.session_factory)
    await repo.record_run("run-1", RUN_PAYLOAD)
    yield repo
    await database.dispose()


def _event(event_id: str = "e-1", override_qty: int = 20) -> DraftOverrideEvent:
    return DraftOverrideEvent(
        event_id=event_id, source_run_id="run-1", source_trace_id="trace-1",
        shop_code="shop-1", goods_code="sku-1", decision_date="2026-07-23",
        recommended_qty=10, override_qty=override_qty,
        override_timestamp=datetime.now(timezone.utc), reason_code="OPERATOR",
        recommendation_snapshot=SNAPSHOT, snapshot_hash=snapshot_hash(SNAPSHOT),
    )


async def _approve_and_submit(repository, override_qty: int = 20) -> str:
    saved = await repository.save_draft_edits(
        AdjustDraftRequest(run_id="run-1", events=[_event(override_qty=override_qty)]))
    case_id = saved["case_ids"][0]
    signed_gap = override_qty - 10
    await repository.claim_case("outcome-test-worker")
    attempt = await repository.begin_attempt(case_id, "outcome-test-worker")
    await repository.finish_attempt(
        case_id, attempt["attempt_id"], "outcome-test-worker",
        report={
            "summary": "Manual attribution for the outcome test.",
            "allocations": [{"cause_code": "OTHER", "domain": "manual",
                             "signed_contribution_qty": signed_gap,
                             "explanation": "Local event", "evidence_refs": []}],
            "signed_gap": signed_gap, "unexplained_signed_gap": 0,
            "coverage_ratio": 1, "partial": False,
        })
    detail = await repository.get_case(case_id)
    await repository.request_review(case_id, ReviewRequest(
        expected_case_version=detail["version"],
        expected_report_version=detail["latest_report"]["version"], action="APPROVE",
        reviewer_subject="reviewer-1", notes="approved"))
    readiness = await repository.submission_readiness("run-1")
    await repository.submit_and_lock("run-1", readiness.run_version, _Adapter())
    return case_id


async def _daily_sales(repository, goods_code: str, per_day: float, days: int = 4,
                       lost: float = 0.0) -> dict:
    return await repository.ingest_daily_sales(OutcomeIngestRequest(records=[
        DailySalesRecord(shop_code="shop-1", goods_code=goods_code,
                         sales_date=f"2026-07-{24 + offset}", units_sold=per_day,
                         lost_sales_units=lost)
        for offset in range(days)
    ]))


@pytest.mark.asyncio
async def test_submission_tracks_accepted_lines_not_only_overrides(repository):
    """Sampling only disagreements would teach the loop nothing about agreement."""
    await _approve_and_submit(repository)
    listed = await repository.list_outcomes()
    by_sku = {row["goods_code"]: row for row in listed["items"]}
    assert by_sku["sku-1"]["source"] == "OVERRIDE"
    assert by_sku["sku-2"]["source"] == "ACCEPTED"
    assert by_sku["sku-2"]["recommended_qty"] == by_sku["sku-2"]["ordered_qty"] == 8


@pytest.mark.asyncio
async def test_window_is_derived_from_the_frozen_snapshot(repository):
    await _approve_and_submit(repository)
    outcome = (await repository.list_outcomes(goods_code="sku-1"))["items"][0]
    # Flow A lead time 2 + coverage 2 = a 4-day window opening the next day.
    assert outcome["horizon_days"] == 4
    assert outcome["window_start"] == "2026-07-24"
    assert outcome["window_end"] == "2026-07-27"
    assert outcome["status"] == "PENDING"


@pytest.mark.asyncio
async def test_outcome_stays_partial_until_the_window_closes(repository):
    await _approve_and_submit(repository)
    await _daily_sales(repository, "sku-1", per_day=5, days=2)
    outcome = (await repository.list_outcomes(goods_code="sku-1"))["items"][0]
    assert outcome["status"] == "PARTIAL"
    assert outcome["verdict"] == "PENDING"
    assert outcome["ideal_qty"] is None


@pytest.mark.asyncio
async def test_closed_window_scores_both_quantities(repository):
    await _approve_and_submit(repository, override_qty=20)
    result = await _daily_sales(repository, "sku-1", per_day=5, days=4)
    assert result["outcomes_completed"] == 1
    outcome = (await repository.list_outcomes(goods_code="sku-1"))["items"][0]
    assert outcome["status"] == "COMPLETE"
    assert outcome["actual_demand"] == 20
    assert outcome["ideal_qty"] == 20
    assert outcome["verdict"] == "HUMAN_BETTER"


@pytest.mark.asyncio
async def test_repeated_feed_does_not_double_count_demand(repository):
    await _approve_and_submit(repository)
    await _daily_sales(repository, "sku-1", per_day=5, days=4)
    await _daily_sales(repository, "sku-1", per_day=5, days=4)
    outcome = (await repository.list_outcomes(goods_code="sku-1"))["items"][0]
    assert outcome["actual_demand"] == 20


@pytest.mark.asyncio
async def test_accuracy_summary_reports_the_gap_between_engine_and_human(repository):
    await _approve_and_submit(repository, override_qty=20)
    await _daily_sales(repository, "sku-1", per_day=5, days=4)
    summary = await repository.outcome_accuracy_summary()
    assert summary["scored_count"] == 1
    assert summary["engine_mae"] == 10.0
    assert summary["human_mae"] == 0.0
    assert summary["accuracy_gain_units"] == 10.0
    assert summary["human_win_rate"] == 1.0
    # The accepted line has no sales yet, so its window is still counted as open.
    assert summary["pending_count"] == 1


@pytest.mark.asyncio
async def test_published_knowledge_starts_inert(repository):
    """Approval creates a candidate; only measured outcomes give it engine weight."""
    case_id = await _approve_and_submit(repository)
    detail = await repository.get_case(case_id)
    entry = await repository.publish_knowledge(
        case_id, detail["latest_report"]["report_id"],
        KnowledgePublishRequest(kind=KnowledgeKind.DEMAND_LEVEL, scope_shop_code="shop-1",
                                scope_goods_code="sku-1", prior_value=0.0,
                                proposed_value=2.5))
    assert entry["status"] == "CANDIDATE"
    assert entry["effective_weight"] == 0.0
    assert entry["scope_shop_code"] == "shop-1"
    assert entry["scope_goods_code"] == "sku-1"
    assert await repository.active_knowledge_for("shop-1", "sku-1") == {}


@pytest.mark.asyncio
async def test_knowledge_value_falls_back_to_the_approved_quantity_gap(repository):
    case_id = await _approve_and_submit(repository, override_qty=20)
    detail = await repository.get_case(case_id)
    entry = await repository.publish_knowledge(
        case_id, detail["latest_report"]["report_id"],
        KnowledgePublishRequest(kind=KnowledgeKind.DEMAND_LEVEL, scope_shop_code="shop-1",
                                scope_goods_code="sku-1"))
    # +10 units spread over the 4-day horizon the order covered.
    assert entry["prior_value"] == 0.0
    assert entry["proposed_value"] == 2.5


@pytest.mark.asyncio
async def test_completed_outcomes_move_the_knowledge_posterior(repository):
    case_id = await _approve_and_submit(repository, override_qty=20)
    detail = await repository.get_case(case_id)
    await repository.publish_knowledge(
        case_id, detail["latest_report"]["report_id"],
        KnowledgePublishRequest(kind=KnowledgeKind.DEMAND_LEVEL, scope_shop_code="shop-1",
                                scope_goods_code="sku-1", prior_value=0.0,
                                proposed_value=2.5))
    await _daily_sales(repository, "sku-1", per_day=5, days=4)
    entry = (await repository.list_knowledge(shop_code="shop-1"))[0]
    assert entry["posterior"]["hit_count"] == 1
    assert entry["posterior"]["sample_size"] == 1


@pytest.mark.asyncio
async def test_knowledge_scoped_elsewhere_is_untouched(repository):
    case_id = await _approve_and_submit(repository, override_qty=20)
    detail = await repository.get_case(case_id)
    await repository.publish_knowledge(
        case_id, detail["latest_report"]["report_id"],
        KnowledgePublishRequest(kind=KnowledgeKind.DEMAND_LEVEL, scope_shop_code="shop-9",
                                scope_goods_code="sku-9", prior_value=0.0,
                                proposed_value=2.5))
    await _daily_sales(repository, "sku-1", per_day=5, days=4)
    entry = (await repository.list_knowledge(shop_code="shop-9"))[0]
    assert entry["posterior"]["sample_size"] == 0


@pytest.mark.asyncio
async def test_lost_sales_are_counted_as_demand(repository):
    await _approve_and_submit(repository, override_qty=20)
    await _daily_sales(repository, "sku-1", per_day=2, days=4, lost=3)
    outcome = (await repository.list_outcomes(goods_code="sku-1"))["items"][0]
    assert outcome["actual_demand"] == 20
    assert outcome["verdict"] == "HUMAN_BETTER"
