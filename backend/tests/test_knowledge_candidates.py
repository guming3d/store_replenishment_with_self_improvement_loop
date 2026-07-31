"""Knowledge candidates, and the reviewer verdicts recorded against them.

Two properties are load-bearing here and neither is obvious from the code:

1. A candidate is calibrated against what the store manager actually ordered, so
   it can never come out as "the engine already assumed this" -- the degeneracy
   that made the allocation-only report unreviewable.
2. A rejection is stored. Agreement teaching the system while disagreement is
   discarded is the failure mode this whole change exists to fix.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from attribution.db import Database, DatabaseSettings
from attribution.deterministic import build_attribution_report, replay_engine, snapshot_hash
from attribution.proposals import solve_for_quantity
from attribution.repository import AttributionRepository
from attribution.schemas import (
    AdjustDraftRequest, DraftOverrideEvent, KnowledgeDecisionInput, KnowledgeKind,
    ReviewRequest,
)
from attribution.errors import ValidationError

SNAPSHOT = {
    "shop": "shop-1",
    "shop_name": "古城店",
    "sku_info": {"goods_code": "sku-1", "goods_name": "雪花啤酒", "category": "啤酒"},
    "forecast": {"mean": 3, "std": 1, "days": 60},
    "decision_date": "2026-07-23",
    "inventory_snapshot": {"on_hand": 0, "in_transit": 0, "reserved": 0, "expiring": 0},
    "params": {"fill_rate": 0.95, "coverage": 2, "case_pack": 1, "moq": 0, "shelf_max": 999},
}

RUN_PAYLOAD = {
    "ts": "2026-07-23T08:00:00",
    "results": [
        {"shop": "shop-1", "sku": "sku-1", "apply_date": "2026-07-23", "chosen_qty": 10,
         "final_qty": 10, "position": 0, "flow": "A", "params": SNAPSHOT["params"]},
    ],
}


def _finding(cause_code: str, **overrides) -> dict:
    return {
        "cause_code": cause_code,
        "domain": "substitution" if cause_code == "SUBSTITUTION_TRANSFER" else "seasonality",
        "applicable": True, "expected_direction": "INCREASE",
        "condition": "每年 6 至 8 月的盛夏时段",
        "proposed_scope": "SHOP_CATEGORY", "recurring": True,
        "evidence_refs": [],
        "explanation": "7 月盛夏，啤酒进入旺销期，货架周转明显加快。",
        **overrides,
    }


def _case(override_qty: int, snapshot: dict | None = None) -> dict:
    return {
        "case_id": "case-1", "shop_code": "shop-1",
        "recommended_qty": 10, "override_qty": override_qty,
        "reason_code": "OPERATOR",
        "snapshot": snapshot or json.loads(json.dumps(SNAPSHOT)),
        "output_language": "zh-CN",
    }


def _candidates(override_qty: int, findings: list[dict] | None = None) -> dict[str, dict]:
    report = build_attribution_report(
        _case(override_qty),
        {"summary": "s", "findings": findings or [_finding("SEASONAL_SHIFT")],
         "partial": False})
    return {item["candidate_id"]: item for item in report["knowledge_candidates"]}


# --- calibration ------------------------------------------------------------

def test_solver_returns_the_value_nearest_the_prior():
    """The plateau's near edge, so the candidate is the smallest change that works."""
    # A step function: 10 below 2.0, 20 from 2.0 up to 4.0, 30 above.
    def evaluate(value: float) -> int:
        return 10 if value < 2.0 else 20 if value < 4.0 else 30

    value, achieved, status = solve_for_quantity(
        evaluate, prior=1.0, target_qty=20, lower=0.0, upper=6.0)
    assert (achieved, status) == (20, "EXACT")
    assert 2.0 <= value < 2.01, "must land on the near edge, not the middle of the plateau"


def test_solver_handles_an_inverted_response():
    """Some parameters push quantity down; the search must not assume otherwise."""
    def evaluate(value: float) -> int:
        return 30 if value < 2.0 else 10

    value, achieved, status = solve_for_quantity(
        evaluate, prior=1.0, target_qty=10, lower=0.0, upper=6.0)
    assert (achieved, status) == (10, "EXACT")
    assert value >= 2.0


def test_solver_reports_a_target_it_cannot_reach():
    """Silence here would let a reviewer accept a value the engine ignores."""
    value, achieved, status = solve_for_quantity(
        lambda _: 18, prior=1.0, target_qty=5, lower=0.05, upper=5.0)
    assert status == "UNREACHABLE"
    assert achieved == 18


def test_seasonal_candidate_is_calibrated_to_the_ordered_quantity():
    baseline = int(replay_engine(SNAPSHOT)["final_qty"])
    candidate = _candidates(baseline + 12)["SEASONAL_SHIFT"]

    assert candidate["kind"] == "SEASONAL_FACTOR"
    assert candidate["acceptable"] is True
    assert candidate["calibration_status"] in {"EXACT", "APPROXIMATE"}
    # The whole point: the proposal moves the engine, unlike the allocation it
    # replaces, which scored zero because the factor was already applied.
    assert candidate["proposed_value"] != candidate["prior_value"]
    assert candidate["impact_qty"] != 0
    assert int(replay_engine(
        SNAPSHOT, factor_overrides={"season": candidate["proposed_value"]},
    )["final_qty"]) == candidate["achieved_qty"]


def test_candidate_carries_the_trigger_and_a_window_it_can_fire_in():
    """A correction that only applies on the day it was seen is a patch, not knowledge."""
    candidate = _candidates(30)["SEASONAL_SHIFT"]
    assert candidate["condition"] == "每年 6 至 8 月的盛夏时段"
    assert candidate["applies_from"] == "2026-06-01"
    assert candidate["applies_to"] == "2026-08-31"
    assert candidate["scope_label"] == "SHOP_CATEGORY"
    assert candidate["scope"] == {
        "shop_code": "shop-1", "goods_code": None, "category": "啤酒"}


def test_statement_and_effect_never_contradict_each_other():
    candidate = _candidates(30)["SEASONAL_SHIFT"]
    assert str(candidate["achieved_qty"]) in candidate["effect"]
    assert str(candidate["target_qty"]) in candidate["effect"]
    assert "1.25" in candidate["statement"], "the engine's own prior must be shown"


def test_an_implausible_calibration_is_flagged_not_hidden():
    """Exact arithmetic will happily propose a 4x seasonal factor; say so."""
    baseline = int(replay_engine(SNAPSHOT)["final_qty"])
    candidate = _candidates(baseline * 2)["SEASONAL_SHIFT"]
    assert candidate["magnitude_plausible"] is False
    assert candidate["magnitude_ratio"] > 2.0
    assert "幅度偏大" in candidate["effect"]


def test_an_uncalibratable_candidate_cannot_be_accepted():
    """Below the MOQ floor no demand factor reaches the ordered quantity."""
    snapshot = json.loads(json.dumps(SNAPSHOT))
    snapshot["params"] = {**snapshot["params"], "moq": 18, "case_pack": 6}
    report = build_attribution_report(
        _case(2, snapshot),
        {"summary": "s", "findings": [_finding("SEASONAL_SHIFT")], "partial": False})
    candidate = report["knowledge_candidates"][0]
    assert candidate["calibration_status"] == "UNREACHABLE"
    assert candidate["acceptable"] is False
    assert candidate["proposed_value"] is None
    assert candidate["boundary_value"] is not None
    assert "NO_CALIBRATABLE_CANDIDATE" in report["risk_flags"]


def test_a_cause_with_no_registered_relationship_still_surfaces():
    """A missing substitution relationship is a data gap the reviewer should see."""
    candidates = _candidates(30, [_finding("SUBSTITUTION_TRANSFER")])
    candidate = candidates["SUBSTITUTION_TRANSFER"]
    assert candidate["acceptable"] is False
    assert candidate["blocked_reason"]


def test_only_applicable_findings_become_candidates():
    candidates = _candidates(30, [
        _finding("SEASONAL_SHIFT"), _finding("HOLIDAY_EFFECT", applicable=False)])
    assert set(candidates) == {"SEASONAL_SHIFT"}


# --- review verdicts --------------------------------------------------------

@pytest_asyncio.fixture
async def repository():
    database = Database(DatabaseSettings(url="sqlite+aiosqlite:///:memory:"))
    await database.init_schema()
    repo = AttributionRepository(database.session_factory)
    await repo.record_run("run-1", RUN_PAYLOAD)
    yield repo
    await database.dispose()


async def _case_awaiting_review(repository, override_qty: int = 22) -> tuple[str, dict]:
    saved = await repository.save_draft_edits(AdjustDraftRequest(
        run_id="run-1", events=[DraftOverrideEvent(
            event_id="e-1", source_run_id="run-1", source_trace_id="trace-1",
            shop_code="shop-1", goods_code="sku-1", decision_date="2026-07-23",
            recommended_qty=10, override_qty=override_qty,
            override_timestamp=datetime.now(timezone.utc), reason_code="OPERATOR",
            recommendation_snapshot=SNAPSHOT, snapshot_hash=snapshot_hash(SNAPSHOT))]))
    case_id = saved["case_ids"][0]
    await repository.claim_case("worker-1")
    attempt = await repository.begin_attempt(case_id, "worker-1")
    report = build_attribution_report(
        _case(override_qty),
        {"summary": "s", "findings": [_finding("SEASONAL_SHIFT")], "partial": False})
    # The reviewer sees a conserved report; the candidate rides alongside it.
    report["allocations"] = [{
        "cause_code": "OTHER", "domain": "manual",
        "signed_contribution_qty": override_qty - 10,
        "explanation": "Local event", "evidence_refs": []}]
    report["unexplained_signed_gap"] = 0
    report["signed_gap"] = override_qty - 10
    # These hand-written allocations are stated straight against the recommendation,
    # not against the counterfactual's neutral-assumption anchor.
    report["conservation_anchor_qty"] = 10
    await repository.finish_attempt(case_id, attempt["attempt_id"], "worker-1", report=report)
    return case_id, await repository.get_case(case_id)


def _decision(candidate: dict, decision: str, **overrides) -> KnowledgeDecisionInput:
    payload = {
        "candidate_id": candidate["candidate_id"], "decision": decision,
        "cause_code": candidate["cause_code"],
        "kind": KnowledgeKind(candidate["kind"]),
        "scope_label": candidate["scope_label"],
        "scope_shop_code": candidate["scope"]["shop_code"],
        "scope_goods_code": candidate["scope"]["goods_code"],
        "scope_category": candidate["scope"]["category"],
        "prior_value": candidate["prior_value"],
        "proposed_value": candidate["proposed_value"],
        "condition": candidate["condition"],
    }
    payload.update(overrides)
    return KnowledgeDecisionInput(**payload)


async def _review(repository, case_id: str, detail: dict, decisions: list) -> None:
    await repository.request_review(case_id, ReviewRequest(
        expected_case_version=detail["version"],
        expected_report_version=detail["latest_report"]["version"],
        action="APPROVE", reviewer_subject="reviewer-1",
        knowledge_decisions=decisions))


@pytest.mark.asyncio
async def test_accepting_a_candidate_stores_its_value_scope_and_trigger(repository):
    case_id, detail = await _case_awaiting_review(repository)
    candidate = detail["latest_report"]["knowledge_candidates"][0]

    await _review(repository, case_id, detail, [_decision(candidate, "ACCEPT")])

    entries = await repository.list_knowledge()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["kind"] == "SEASONAL_FACTOR"
    assert entry["proposed_value"] == candidate["proposed_value"]
    assert entry["prior_value"] == candidate["prior_value"]
    assert entry["applies_from"] == candidate["applies_from"]
    assert entry["scope_category"] == "啤酒"
    # Without the trigger the entry is a number nobody can later interpret.
    assert entry["evidence"]["condition"] == "每年 6 至 8 月的盛夏时段"
    # Approval says credible, not correct: weight is earned from outcomes.
    assert entry["status"] == "CANDIDATE"
    assert entry["effective_weight"] == 0.0


@pytest.mark.asyncio
async def test_amending_a_candidate_stores_the_reviewers_value(repository):
    case_id, detail = await _case_awaiting_review(repository)
    candidate = detail["latest_report"]["knowledge_candidates"][0]

    await _review(repository, case_id, detail, [_decision(
        candidate, "AMEND", proposed_value=1.5, note="幅度按门店经验下调")])

    entry = (await repository.list_knowledge())[0]
    assert entry["proposed_value"] == 1.5
    assert entry["evidence"]["decision"] == "AMEND"
    assert entry["evidence"]["note"] == "幅度按门店经验下调"


@pytest.mark.asyncio
async def test_rejecting_a_candidate_is_recorded_rather_than_discarded(repository):
    """The signal that tells an agent's owner where it is wrong."""
    case_id, detail = await _case_awaiting_review(repository)
    candidate = detail["latest_report"]["knowledge_candidates"][0]

    await _review(repository, case_id, detail, [_decision(
        candidate, "REJECT", reject_reason="ONE_OFF_EVENT",
        note="这次是门口修路导致的临时客流，不是季节性")])

    assert await repository.list_knowledge() == []
    rejections = await repository.list_knowledge_rejections()
    assert len(rejections) == 1
    rejection = rejections[0]
    assert rejection["reason_code"] == "ONE_OFF_EVENT"
    assert rejection["cause_code"] == "SEASONAL_SHIFT"
    assert rejection["note"].startswith("这次是门口修路")
    # The candidate is kept verbatim so a changed prompt can be replayed against it.
    assert rejection["candidate"]["proposed_value"] == candidate["proposed_value"]
    assert rejection["reviewer_subject"] == "reviewer-1"


@pytest.mark.asyncio
async def test_feedback_summary_tallies_acceptance_against_rejection(repository):
    case_id, detail = await _case_awaiting_review(repository)
    candidate = detail["latest_report"]["knowledge_candidates"][0]
    await _review(repository, case_id, detail, [_decision(
        candidate, "REJECT", reject_reason="WRONG_MAGNITUDE")])

    summary = await repository.knowledge_feedback_summary()
    assert summary["rejected_total"] == 1
    assert summary["accepted_total"] == 0
    assert summary["acceptance_rate"] == 0.0
    assert summary["rejected_by_cause"]["SEASONAL_SHIFT"]["reasons"] == {"WRONG_MAGNITUDE": 1}


@pytest.mark.asyncio
async def test_a_rejection_requires_a_reason(repository):
    case_id, detail = await _case_awaiting_review(repository)
    candidate = detail["latest_report"]["knowledge_candidates"][0]
    with pytest.raises(ValueError, match="reject_reason"):
        _decision(candidate, "REJECT")


@pytest.mark.asyncio
async def test_a_decision_must_name_a_candidate_the_report_produced(repository):
    case_id, detail = await _case_awaiting_review(repository)
    candidate = detail["latest_report"]["knowledge_candidates"][0]
    stray = _decision(candidate, "ACCEPT", candidate_id="INVENTED_CAUSE")
    with pytest.raises(ValidationError, match="does not match any candidate"):
        await _review(repository, case_id, detail, [stray])


@pytest.mark.asyncio
async def test_changes_requested_cannot_record_knowledge_decisions(repository):
    case_id, detail = await _case_awaiting_review(repository)
    candidate = detail["latest_report"]["knowledge_candidates"][0]
    with pytest.raises(ValidationError, match="cannot record knowledge decisions"):
        await repository.request_review(case_id, ReviewRequest(
            expected_case_version=detail["version"],
            expected_report_version=detail["latest_report"]["version"],
            action="REQUEST_CHANGES", reviewer_subject="reviewer-1", notes="please redo",
            knowledge_decisions=[_decision(candidate, "ACCEPT")]))


@pytest.mark.asyncio
async def test_two_decisions_cannot_target_the_same_candidate(repository):
    case_id, detail = await _case_awaiting_review(repository)
    candidate = detail["latest_report"]["knowledge_candidates"][0]
    with pytest.raises(ValueError, match="unique"):
        ReviewRequest(
            expected_case_version=1, expected_report_version=1, action="APPROVE",
            reviewer_subject="reviewer-1",
            knowledge_decisions=[_decision(candidate, "ACCEPT"),
                                 _decision(candidate, "REJECT",
                                           reject_reason="WRONG_SCOPE")])
