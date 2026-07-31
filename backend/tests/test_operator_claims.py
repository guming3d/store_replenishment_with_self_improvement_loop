"""The store manager's stated reason, checked against what the evidence supports.

The verdict is computed, never asked for: the coordinator prompt contains the
operator's claim, so a model asked to grade that claim would be grading its own
anchor. These tests pin the two properties that make the verdict worth counting
-- that a merely *asserted* cause cannot reach SUPPORTED without the engine
being solvable to the ordered quantity, and that the verdict moves no number.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from attribution.claims import (
    CLAIM_TO_CAUSES, UNSUPPORTED_VERDICTS, judge_operator_claim,
)
from attribution.db import Database, DatabaseSettings
from attribution.deterministic import build_attribution_report, snapshot_hash
from attribution.repository import AttributionRepository
from attribution.schemas import AdjustDraftRequest, DraftOverrideEvent


def _candidate(cause_code: str, *, acceptable: bool) -> dict:
    return {
        "cause_code": cause_code,
        "acceptable": acceptable,
        "calibration_status": "EXACT" if acceptable else "UNREACHABLE",
    }


def test_claim_is_supported_only_when_the_engine_can_be_solved_to_the_order():
    supported = judge_operator_claim(
        reason_code="SEASONAL",
        applicable_causes={"SEASONAL_SHIFT"},
        candidates=[_candidate("SEASONAL_SHIFT", acceptable=True)],
    )
    assert supported["verdict"] == "SUPPORTED"
    assert supported["corroborating_causes"] == ["SEASONAL_SHIFT"]

    # The model asserted the same cause, but no value of the seasonal factor
    # reproduces the ordered quantity. Calling this SUPPORTED would let a model
    # that simply echoes the operator's reason code manufacture agreement.
    uncalibrated = judge_operator_claim(
        reason_code="SEASONAL",
        applicable_causes={"SEASONAL_SHIFT"},
        candidates=[_candidate("SEASONAL_SHIFT", acceptable=False)],
    )
    assert uncalibrated["verdict"] == "UNCALIBRATED"
    assert uncalibrated["corroborating_causes"] == []


def test_claim_verdicts_distinguish_a_wrong_reason_from_an_unmodelled_one():
    contradicted = judge_operator_claim(
        reason_code="SUBSTITUTION",
        applicable_causes=set(),
        candidates=[],
    )
    assert contradicted["verdict"] == "CONTRADICTED"

    # The registry owns no parameter for an inventory claim, so the store
    # manager cannot be scored wrong for making one.
    out_of_scope = judge_operator_claim(
        reason_code="INVENTORY_CONSTRAINT",
        applicable_causes={"SEASONAL_SHIFT"},
        candidates=[_candidate("SEASONAL_SHIFT", acceptable=True)],
    )
    assert out_of_scope["verdict"] == "OUT_OF_SCOPE"
    assert not CLAIM_TO_CAUSES["INVENTORY_CONSTRAINT"]

    unverifiable = judge_operator_claim(
        reason_code="OPERATOR", applicable_causes=set(), candidates=[])
    assert unverifiable["verdict"] == "UNVERIFIABLE"
    assert judge_operator_claim(
        reason_code=None, applicable_causes=set(), candidates=[])["verdict"] == "UNVERIFIABLE"


def test_evidence_the_operator_did_not_claim_is_reported_separately():
    """A manager acting on a real signal they cannot name is not a false claim."""
    judged = judge_operator_claim(
        reason_code="SUBSTITUTION",
        applicable_causes={"SEASONAL_SHIFT"},
        candidates=[_candidate("SEASONAL_SHIFT", acceptable=True)],
    )
    assert judged["verdict"] == "CONTRADICTED"
    assert judged["unclaimed_supported_causes"] == ["SEASONAL_SHIFT"]
    # An out-of-scope claim still surfaces what the evidence did back, otherwise
    # the only case where the registry is at fault is also the one where nothing
    # is learned.
    scoped = judge_operator_claim(
        reason_code="DEMAND_CHANGE",
        applicable_causes={"SEASONAL_SHIFT"},
        candidates=[_candidate("SEASONAL_SHIFT", acceptable=True)],
    )
    assert scoped["verdict"] == "OUT_OF_SCOPE"
    assert scoped["unclaimed_supported_causes"] == ["SEASONAL_SHIFT"]


def _snapshot() -> dict:
    return {
        "shop": "shop-1",
        "sku_info": {"goods_code": "sku-1", "goods_name": "Test", "category": "啤酒"},
        "forecast": {"mean": 3, "std": 1, "days": 60},
        "decision_date": "2026-07-23",
        "inventory_snapshot": {"on_hand": 0, "in_transit": 0, "reserved": 0, "expiring": 0},
        "params": {"fill_rate": 0.95, "coverage": 2, "case_pack": 1, "moq": 0,
                   "shelf_max": 999},
    }


def _report(reason_code: str | None, *, applicable: bool = True,
            override_qty: int = 15) -> dict:
    snapshot = _snapshot()
    case = {
        "case_id": f"case-claim-{reason_code}-{override_qty}",
        "recommended_qty": 10,
        "override_qty": override_qty,
        "snapshot": snapshot,
    }
    if reason_code is not None:
        case["reason_code"] = reason_code
    return build_attribution_report(case, {
        "summary": "Seasonal evidence assessed",
        "findings": [{
            "cause_code": "SEASONAL_SHIFT",
            "domain": "seasonality",
            "applicable": applicable,
            "evidence_refs": ["seasonality-seed"],
            "explanation": "Summer seasonal factor applies",
        }],
        "partial": False,
    })


def test_report_grades_the_claim_and_flags_an_unsupported_one():
    supported = _report("SEASONAL")
    assert supported["operator_claim"]["verdict"] == "SUPPORTED"
    assert "OPERATOR_CLAIM_UNSUPPORTED" not in supported["risk_flags"]

    contradicted = _report("SEASONAL", applicable=False)
    assert contradicted["operator_claim"]["verdict"] == "CONTRADICTED"
    assert "OPERATOR_CLAIM_UNSUPPORTED" in contradicted["risk_flags"]

    # A claim the registry cannot express is reported, not penalised.
    out_of_scope = _report("INVENTORY_CONSTRAINT")
    assert out_of_scope["operator_claim"]["verdict"] == "OUT_OF_SCOPE"
    assert "OPERATOR_CLAIM_UNSUPPORTED" not in out_of_scope["risk_flags"]
    assert "OUT_OF_SCOPE" not in UNSUPPORTED_VERDICTS


def test_the_claim_verdict_changes_no_quantity():
    """The operator's words grade the operator; they must not move the maths."""
    quantitative = (
        "allocations", "shapley", "signed_gap", "unexplained_signed_gap",
        "baseline_qty", "bare_baseline_qty", "attributed_qty", "coverage_ratio",
        "knowledge_candidates",
    )
    seasonal = _report("SEASONAL")
    substitution = _report("SUBSTITUTION")
    unstated = _report(None)
    assert seasonal["operator_claim"]["verdict"] != substitution["operator_claim"]["verdict"]
    for key in quantitative:
        assert seasonal[key] == substitution[key] == unstated[key], key


def test_an_uncalibratable_override_cannot_be_reported_as_a_supported_claim():
    """A quantity no seasonal factor can reach must not corroborate the claim."""
    report = _report("SEASONAL", override_qty=5000)
    candidates = report["knowledge_candidates"]
    assert candidates and not any(item["acceptable"] for item in candidates)
    assert report["operator_claim"]["verdict"] == "UNCALIBRATED"
    assert "OPERATOR_CLAIM_UNSUPPORTED" in report["risk_flags"]
    assert "NO_CALIBRATABLE_CANDIDATE" in report["risk_flags"]


@pytest_asyncio.fixture
async def repository():
    database = Database(DatabaseSettings(url="sqlite+aiosqlite:///:memory:"))
    await database.init_schema()
    repo = AttributionRepository(database.session_factory)
    await repo.record_run("run-1", {"results": [{"sku": "sku-1", "chosen_qty": 10}]})
    yield repo
    await database.dispose()


async def _store_report(repo: AttributionRepository, event_id: str, reason_code: str,
                        *, applicable: bool = True) -> str:
    snapshot = _snapshot()
    saved = await repo.save_draft_edits(AdjustDraftRequest(run_id="run-1", events=[
        DraftOverrideEvent(
            event_id=event_id, source_run_id="run-1", source_trace_id="trace-1",
            shop_code="shop-1", goods_code="sku-1", decision_date="2026-07-23",
            recommended_qty=10, override_qty=15,
            override_timestamp=datetime.now(timezone.utc), reason_code=reason_code,
            recommendation_snapshot=snapshot, snapshot_hash=snapshot_hash(snapshot),
        )]))
    case_id = saved["case_ids"][0]
    worker = f"worker-{event_id}"
    await repo.claim_case(worker)
    attempt = await repo.begin_attempt(case_id, worker)
    report = build_attribution_report(
        {"case_id": case_id, "recommended_qty": 10, "override_qty": 15,
         "reason_code": reason_code, "snapshot": snapshot},
        {"summary": "Seasonal evidence assessed",
         "findings": [{"cause_code": "SEASONAL_SHIFT", "domain": "seasonality",
                       "applicable": applicable, "evidence_refs": ["seasonality-seed"],
                       "explanation": "Summer seasonal factor applies"}],
         "partial": False})
    await repo.finish_attempt(case_id, attempt["attempt_id"], worker, report=report)
    return case_id


@pytest.mark.asyncio
async def test_verdicts_are_persisted_and_summarised_per_reason_code(repository):
    await _store_report(repository, "claim-a", "SEASONAL")
    await _store_report(repository, "claim-b", "SEASONAL", applicable=False)
    await _store_report(repository, "claim-c", "INVENTORY_CONSTRAINT")

    detail = await repository.get_case(
        (await repository.list_cases())["items"][0]["case_id"])
    assert detail["latest_report"]["operator_claim"]["verdict"] in {
        "SUPPORTED", "CONTRADICTED", "OUT_OF_SCOPE"}

    summary = await repository.claim_verdict_summary()
    assert summary["judged_total"] == 3
    seasonal = summary["by_reason_code"]["SEASONAL"]
    assert seasonal["verdicts"] == {"SUPPORTED": 1, "CONTRADICTED": 1}
    assert seasonal["supported_rate"] == 0.5
    # Out-of-scope claims are counted but kept out of the rate: the cause
    # registry is what fell short there, not the store.
    assert summary["out_of_scope_total"] == 1
    assert summary["supported_rate"] == 0.5
    assert summary["by_reason_code"]["INVENTORY_CONSTRAINT"]["supported_rate"] is None
