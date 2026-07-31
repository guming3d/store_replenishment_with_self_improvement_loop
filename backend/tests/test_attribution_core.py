from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from attribution import deterministic
from attribution.db import Database, DatabaseSettings
from attribution.deterministic import (
    CAUSE_CODES, CAUSE_RESOLVERS, SeedRepository, _format_qty, build_attribution_report,
    conserve, replay_engine, shapley_values, snapshot_hash, substitute_codes_for_target,
    substitution_target_daily_delta,
)
from attribution.errors import GateBlockedError, StateTransitionError
from attribution.repository import AttributionRepository
from attribution.schemas import AdjustDraftRequest, DraftOverrideEvent, ManualReportRequest, ReviewRequest


@pytest_asyncio.fixture
async def repository():
    database = Database(DatabaseSettings(url="sqlite+aiosqlite:///:memory:"))
    await database.init_schema()
    repo = AttributionRepository(database.session_factory)
    await repo.record_run("run-1", {"results": [{"sku": "sku-1", "chosen_qty": 10}]})
    yield repo
    await database.dispose()


def event(event_id: str, override_qty: int = 15,
          output_language: str = "zh-CN") -> DraftOverrideEvent:
    snapshot = {
        "shop": "shop-1", "sku_info": {"goods_code": "sku-1", "goods_name": "Test", "category": "啤酒"},
        "forecast": {"mean": 3, "std": 1, "days": 60}, "decision_date": "2026-07-23",
        "inventory_snapshot": {"on_hand": 0, "in_transit": 0, "reserved": 0, "expiring": 0},
        "params": {"fill_rate": 0.95, "coverage": 2, "case_pack": 1, "moq": 0, "shelf_max": 999},
    }
    return DraftOverrideEvent(
        event_id=event_id, source_run_id="run-1", source_trace_id="trace-1", shop_code="shop-1",
        goods_code="sku-1", decision_date="2026-07-23", recommended_qty=10, override_qty=override_qty,
        override_timestamp=datetime.now(timezone.utc), reason_code="OPERATOR", recommendation_snapshot=snapshot,
        snapshot_hash=snapshot_hash(snapshot), output_language=output_language,
    )


@pytest.mark.asyncio
async def test_event_idempotency_and_superseding(repository):
    request = AdjustDraftRequest(run_id="run-1", events=[event("e-1")])
    first = await repository.save_draft_edits(request)
    duplicate = await repository.save_draft_edits(request)
    assert first["case_ids"] and duplicate["case_ids"] == first["case_ids"]
    assert duplicate["duplicate_event_ids"] == ["e-1"]
    assert {"run_id", "changed", "total_qty", "results", "case_ids", "gate_status", "run_version"} <= set(first)
    readiness = await repository.submission_readiness("run-1")
    assert {"run_id", "run_version", "status", "ready", "modified_count", "approved_count", "blockers"} <= set(
        readiness.model_dump())
    listed = await repository.list_cases()
    assert {"items", "total", "page", "page_size"} <= set(listed)
    assert {"event_id", "signed_gap", "direction", "status", "report_version"} <= set(listed["items"][0])
    detail = await repository.get_case(first["case_ids"][0])
    assert {"source_trace_id", "latest_report", "attempts", "reviews", "trace_events",
            "error_code", "superseded_by_case_id"} <= set(detail)

    await repository.claim_case("stale-worker")
    stale_attempt = await repository.begin_attempt(first["case_ids"][0], "stale-worker")
    second = await repository.save_draft_edits(AdjustDraftRequest(run_id="run-1", events=[event("e-2", 17)]))
    old, current = first["case_ids"][0], second["case_ids"][0]
    ignored = await repository.finish_attempt(
        old, stale_attempt["attempt_id"], "stale-worker",
        report={"summary": "stale", "signed_gap": 5, "unexplained_signed_gap": 5},
    )
    assert ignored["ignored_stale_completion"] is True
    assert (await repository.get_case(old))["state"] == "SUPERSEDED"
    assert (await repository.get_case(current))["state"] == "QUEUED"

    await repository.save_draft_edits(
        AdjustDraftRequest(run_id="run-1", events=[event("e-reset", 10)]))
    third = await repository.save_draft_edits(
        AdjustDraftRequest(run_id="run-1", events=[event("e-3", 19)]))
    assert (await repository.get_case(third["case_ids"][0]))["case_version"] == 3


@pytest.mark.asyncio
async def test_attempt_call_counts_come_from_persisted_execution_events(repository):
    saved = await repository.save_draft_edits(
        AdjustDraftRequest(run_id="run-1", events=[event("telemetry-event")]))
    case_id = saved["case_ids"][0]
    worker_id = "telemetry-worker"
    await repository.claim_case(worker_id)
    attempt = await repository.begin_attempt(case_id, worker_id)
    trace_id = f"attr-{case_id}-attempt-{attempt['number']}"

    await repository.append_trace(
        case_id, trace_id, "MODEL_CALL_STARTED", {"model_call": 1})
    await repository.append_trace(
        case_id, trace_id, "MODEL_CALL_COMPLETED", {"model_call": 1})
    await repository.append_trace(
        case_id, trace_id, "TOOL_CALL_STARTED", {"tool_call": 1})
    await repository.append_trace(case_id, trace_id, "MODEL_RAW_OUTPUT", {
        "access_token": "secret",
        "reasoning": "private reasoning",
        "usage": {"input_token_count": 12},
        "response": "visible output",
    })

    detail = await repository.get_case(case_id)
    assert detail["attempts"][0]["model_calls"] == 1
    assert detail["attempts"][0]["tool_calls"] == 1
    assert detail["attempts"][0]["raw_log_available"] is True
    raw_log = await repository.get_attempt_log(case_id, 1)
    assert [event["event_type"] for event in raw_log] == [
        "MODEL_CALL_STARTED", "MODEL_CALL_COMPLETED", "TOOL_CALL_STARTED",
        "MODEL_RAW_OUTPUT",
    ]
    debug_payload = raw_log[-1]["payload"]
    assert debug_payload["access_token"] == "[REDACTED]"
    assert debug_payload["reasoning"] == "[REDACTED]"
    assert debug_payload["usage"]["input_token_count"] == 12
    assert debug_payload["response"] == "visible output"


@pytest.mark.asyncio
async def test_retry_can_follow_current_ui_language(repository):
    saved = await repository.save_draft_edits(AdjustDraftRequest(
        run_id="run-1", events=[event("language-event", output_language="en-US")]))
    case_id = saved["case_ids"][0]
    worker_id = "language-worker"
    await repository.claim_case(worker_id)
    attempt = await repository.begin_attempt(case_id, worker_id)
    await repository.finish_attempt(
        case_id, attempt["attempt_id"], worker_id,
        error_code="AGENT_UNAVAILABLE", error_detail="test failure")
    failed = await repository.get_case(case_id)
    await repository.cancel_case(case_id, failed["version"])
    cancelled = await repository.get_case(case_id)
    retried = await repository.retry_case(
        case_id, cancelled["version"], output_language="zh-CN")
    assert retried["output_language"] == "zh-CN"


@pytest.mark.asyncio
async def test_gate_requires_matching_human_approval(repository):
    result = await repository.save_draft_edits(AdjustDraftRequest(run_id="run-1", events=[event("e-1")]))
    case_id = result["case_ids"][0]

    class Adapter:
        async def submit(self, run_id, accepted_overrides):
            return {"submitted": run_id, "count": len(accepted_overrides)}

    with pytest.raises(GateBlockedError):
        await repository.submit_and_lock("run-1", 2, Adapter())

    for _ in range(3):
        await repository.claim_case("worker")
        attempt = await repository.begin_attempt(case_id, "worker")
        await repository.finish_attempt(case_id, attempt["attempt_id"], "worker",
                                        error_code="AGENT_UNAVAILABLE",
                                        error_detail="configuration absent")
        if _ < 2:
            await asyncio.sleep(2 ** _)
    queued = await repository.get_case(case_id)
    report = await repository.create_manual_report(case_id, ManualReportRequest(
        expected_case_version=queued["version"], summary="Manual, evidence-backed attribution",
        contributions=[{"cause_code": "OTHER", "domain": "manual", "signed_contribution_qty": 4,
                        "explanation": "Observed local event", "evidence_refs": []}],
    ))
    review_case = await repository.get_case(case_id)
    await repository.request_review(case_id, ReviewRequest(
        expected_case_version=review_case["version"], expected_report_version=report["version"],
        action="APPROVE", reviewer_subject="reviewer-1",
    ))
    readiness = await repository.submission_readiness("run-1")
    submission = await repository.submit_and_lock("run-1", readiness.run_version, Adapter())
    assert submission["status"] == "SUBMITTED_LOCKED"
    approved = await repository.get_case(case_id)
    with pytest.raises(StateTransitionError):
        await repository.request_review(case_id, ReviewRequest(
            expected_case_version=approved["version"],
            expected_report_version=approved["latest_report"]["version"],
            action="REQUEST_CHANGES", reviewer_subject="reviewer-1",
        ))


@pytest.mark.asyncio
async def test_failed_case_can_be_manually_recovered(repository):
    created = await repository.save_draft_edits(AdjustDraftRequest(run_id="run-1", events=[event("e-1")]))
    case_id = created["case_ids"][0]
    for _ in range(3):
        claimed = await repository.claim_case("worker")
        assert claimed
        attempt = await repository.begin_attempt(case_id, "worker")
        await repository.finish_attempt(case_id, attempt["attempt_id"], "worker",
                                        error_code="AGENT_UNAVAILABLE",
                                        error_detail="configuration absent")
        if _ < 2:
            await asyncio.sleep(2 ** _)
    assert (await repository.get_case(case_id))["state"] == "FAILED"
    failed = await repository.get_case(case_id)
    approved = await repository.request_review(case_id, ReviewRequest(
        expected_case_version=failed["version"], action="MANUAL_AND_APPROVE",
        reviewer_subject="reviewer-1", summary="Recovered manually",
        contributions=[{"cause_code": "OTHER", "domain": "manual", "signed_contribution_qty": 5,
                        "explanation": "Audited evidence", "evidence_refs": []}],
    ))
    assert approved["state"] == "HUMAN_APPROVED"
    assert (await repository.get_case(case_id))["latest_report"]["source"] == "MANUAL"


@pytest.mark.asyncio
async def test_running_case_cancellation_fences_successful_completion(repository):
    created = await repository.save_draft_edits(
        AdjustDraftRequest(run_id="run-1", events=[event("e-cancel")]))
    case_id = created["case_ids"][0]
    await repository.claim_case("cancel-worker")
    attempt = await repository.begin_attempt(case_id, "cancel-worker")
    running = await repository.get_case(case_id)
    await repository.cancel_case(case_id, running["version"])
    completed = await repository.finish_attempt(
        case_id, attempt["attempt_id"], "cancel-worker",
        report={"summary": "too late", "signed_gap": 5, "unexplained_signed_gap": 5},
    )
    assert completed["state"] == "CANCELLED"
    assert (await repository.get_case(case_id))["latest_report"] is None


@pytest.mark.asyncio
async def test_legacy_adjustments_are_imported_as_blocked_overrides(repository, tmp_path):
    history = tmp_path / "run_history.json"
    history.write_text(json.dumps([{
        "run_id": "legacy-adjusted",
        "ts": "2026-07-23T10:00:00",
        "shop_code": "shop-1",
        "results": [{
            "shop": "shop-1", "sku": "sku-1", "apply_date": "2026-07-23",
            "chosen_qty": 10, "final_qty": 15,
        }],
    }]), encoding="utf-8")
    assert await repository.import_legacy_run_history(history) == 1
    readiness = await repository.submission_readiness("legacy-adjusted")
    assert readiness.ready is False
    assert readiness.modified_count == 1
    assert readiness.blockers[0]["status"] == "MISSING"


def test_deterministic_substitution_shapley_and_conservation():
    transfer = substitution_target_daily_delta(
        relationship_direction=1, transfer_rate=0.5, substitute_reconstructed_daily_demand=20,
        substitute_reorder_point=10, substitute_available_position=0, target_true_daily_demand=8,
        max_transfer_ratio=0.25)
    assert transfer["inventory_pressure"] == 1
    assert transfer["target_daily_delta"] == 2

    causes = ["season", "holiday"]
    values, metadata = shapley_values(causes, lambda c: sum({"season": 3, "holiday": 2}[x] for x in c),
                                      case_id="case-1")
    assert metadata["method"] == "exact"
    assert values == {"season": 3, "holiday": 2}
    assert conserve(7, values)["unexplained_signed_gap"] == 2

    many = [f"c{i}" for i in range(11)]
    one = shapley_values(many, lambda c: float(len(c)), case_id="stable")
    two = shapley_values(many, lambda c: float(len(c)), case_id="stable")
    assert one == two
    assert sum(one[0].values()) == pytest.approx(11)


def test_model_findings_are_converted_to_deterministic_report():
    draft_event = event("report-event")
    report = build_attribution_report(
        {
            "case_id": "case-report",
            "recommended_qty": draft_event.recommended_qty,
            "override_qty": draft_event.override_qty,
            "snapshot": draft_event.recommendation_snapshot,
        },
        {
            "summary": "Seasonal evidence assessed",
            "findings": [{
                "cause_code": "SEASONAL_SHIFT",
                "domain": "seasonality",
                "applicable": True,
                "evidence_refs": ["seasonality-seed"],
                "explanation": "Summer seasonal factor applies",
            }],
            "partial": False,
        },
    )
    assert report["report_version"] == "deterministic-attribution-v3"
    assert report["allocations"]
    assert sum(item["signed_contribution_qty"] for item in report["allocations"]) + (
        report["unexplained_signed_gap"]) == pytest.approx(
            report["override_qty"] - report["conservation_anchor_qty"])

    not_applicable = build_attribution_report(
        {
            "case_id": "case-report-not-applicable",
            "recommended_qty": draft_event.recommended_qty,
            "override_qty": draft_event.override_qty,
            "snapshot": draft_event.recommendation_snapshot,
        },
        {
            "summary": "Seasonal evidence does not apply",
            "findings": [{
                "cause_code": "SEASONAL_SHIFT",
                "domain": "seasonality",
                "applicable": False,
                "evidence_refs": [],
                "explanation": "Not applicable",
            }],
            "partial": False,
        },
    )
    assert not_applicable["allocations"] == []
    # Coverage is now the only quality signal on the report: a finding the model
    # rejected must explain none of the gap, while the accepted one explains some.
    assert not_applicable["coverage_ratio"] == 0
    assert report["coverage_ratio"] > 0


def _case(draft_event, *, recommended_qty: int | None = None,
          override_qty: int | None = None) -> dict:
    recommended = draft_event.recommended_qty if recommended_qty is None else recommended_qty
    return {
        "case_id": "case-anchor",
        "recommended_qty": recommended,
        "override_qty": draft_event.override_qty if override_qty is None else override_qty,
        "snapshot": draft_event.recommendation_snapshot,
    }


def _replayed_case(draft_event, *, gap: int = 5) -> dict:
    """A case whose stored advice matches its snapshot, as production writes it."""
    baseline = int(replay_engine(draft_event.recommendation_snapshot)["final_qty"])
    return _case(draft_event, recommended_qty=baseline, override_qty=baseline + gap)


def _seed_dir(tmp_path, *, seasonality: dict, holidays: dict,
              substitutions: dict | None = None) -> SeedRepository:
    (tmp_path / "seasonality.json").write_text(
        json.dumps({"version": "test-seasonality-v1", "sku": {}, "category": {}} | seasonality),
        encoding="utf-8")
    (tmp_path / "holidays.json").write_text(
        json.dumps({"version": "test-holiday-v1", "dates": {}} | holidays), encoding="utf-8")
    (tmp_path / "substitutions.json").write_text(
        json.dumps({"version": "test-substitution-v1", "relationships": []}
                   | (substitutions or {})), encoding="utf-8")
    return SeedRepository(tmp_path)


def _finding(cause_code: str, domain: str) -> dict:
    return {"cause_code": cause_code, "domain": domain, "applicable": True,
            "evidence_refs": [], "explanation": "evidence applies"}


def test_summary_narrates_the_conserved_numbers(tmp_path):
    """The buyer-facing text must be built from the allocation, not from model prose."""
    draft_event = event("narrative-event")
    seeds = _seed_dir(tmp_path, seasonality={"sku": {"sku-1": {"7": 1.6}}},
                      holidays={"dates": {"07-23": 1.5}})
    case = _replayed_case(draft_event)
    report = build_attribution_report(
        case,
        {"summary": "基于诊断工具结果，本案季节性证据适用。", "partial": False,
         "findings": [_finding("SEASONAL_SHIFT", "seasonality"),
                      _finding("HOLIDAY_EFFECT", "seasonality")]},
        seeds=seeds,
    )
    summary = report["summary"]
    assert str(case["recommended_qty"]) in summary and str(case["override_qty"]) in summary
    assert "Test" in summary and "sku-1" in summary and "2026-07-23" in summary
    assert "季节性变化" in summary and "节假日影响" in summary
    # Every cause line carries the quantity the table shows for that same cause.
    for item in report["allocations"]:
        assert f"{item['label']}（{_format_qty(item['signed_contribution_qty'])} 件）" in summary
    # The model's prose is preserved but demoted out of the headline.
    assert report["model_summary"] == "基于诊断工具结果，本案季节性证据适用。"
    assert "诊断工具" not in summary
    assert report["narrative_version"] == "narrative-v3"


def test_summary_separates_the_engines_assumptions_from_the_operator_gap(tmp_path):
    """The narrative must not claim evidence explained an override it cannot reach."""
    draft_event = event("narrative-degenerate")
    seeds = _seed_dir(tmp_path, seasonality={"category": {"啤酒": {"7": 1.25}}}, holidays={})
    report = build_attribution_report(
        _replayed_case(draft_event),
        {"summary": "季节性证据适用。", "partial": False,
         "findings": [_finding("SEASONAL_SHIFT", "seasonality")]},
        seeds=seeds,
    )
    # The seasonal factor is a real component of the advice, so it is quantified,
    # but it argues for the quantity the engine already proposed rather than for
    # the store manager's larger order -- and the summary has to say exactly that.
    assert "在系统建议的" in report["summary"]
    assert f"系统只会建议 {report['bare_baseline_qty']} 件" in report["summary"]
    assert "解释不了店长的调整" in report["summary"]


@pytest.mark.parametrize(
    ("unexplained", "expected", "forbidden"),
    [
        (-6.0, "只剩 -6 件差异需要人工确认", "解释不了店长的调整"),
        (-70.0, "解释不了店长的调整", "只剩"),
    ],
)
def test_summary_scales_its_verdict_to_the_size_of_the_residual(
        unexplained, expected, forbidden):
    """A small remainder is not the same finding as an unexplained override.

    The narrative used to treat any non-zero residual as a flat failure, so a
    six-unit remainder sitting under a 150-unit breakdown was announced as "this
    evidence does not explain the adjustment" -- a verdict the numbers printed
    directly above it contradict.
    """
    from attribution.deterministic import _compose_summary

    allocation_rows = [{
        "cause_code": "HOLIDAY_EFFECT", "label": "节假日影响",
        "signed_contribution_qty": 132.0, "absolute_contribution_weight": 0.85,
        "explanation": "元旦当天。",
    }, {
        "cause_code": "SUBSTITUTION_TRANSFER", "label": "替代品需求转移",
        "signed_contribution_qty": 18.0, "absolute_contribution_weight": 0.12,
        "explanation": "替代品库存吃紧。",
    }]
    summary = _compose_summary(
        language="zh-CN",
        snapshot={"shop_name": "天通苑店", "decision_date": "2027-01-01",
                  "sku_info": {"goods_code": "sku-1", "goods_name": "Test"}},
        recommended_qty=426, override_qty=438, baseline_qty=426, bare_baseline_qty=294,
        signed_gap=144.0, unexplained=unexplained, allocation_rows=allocation_rows,
        candidate_labels=["季节性变化", "节假日影响", "替代品需求转移"],
        evidence_is_informative=True, has_conflict=False, direction_conflicts=[],
        unquantifiable_labels=[], unknown_cause_codes=[],
    )
    assert expected in summary
    assert forbidden not in summary


def test_summary_names_what_was_checked_when_no_cause_applies(tmp_path):
    """"No evidence applied" is useless unless the reader learns what was looked at.

    A reviewer who is told only that "the available evidence does not explain this"
    cannot tell whether the system examined ten things or nothing at all, so the
    text names every cause the system is able to quantify and then asks for the
    one thing it cannot know: why the store manager ordered differently.
    """
    draft_event = event("narrative-no-cause")
    seeds = _seed_dir(tmp_path, seasonality={}, holidays={})
    report = build_attribution_report(
        _replayed_case(draft_event),
        {"summary": "未发现适用原因。", "partial": False, "findings": []},
        seeds=seeds,
    )
    summary = report["summary"]
    for label in ("季节性变化", "节假日影响", "替代品需求转移"):
        assert label in summary
    assert "请审核时补充说明" in summary
    # Case-file jargon reads as a verdict on the store manager rather than a
    # statement about the system's own reach.
    assert "本案" not in summary and "现有证据" not in summary


def test_summary_says_plainly_when_applicable_causes_moved_nothing(tmp_path):
    """Causes that replay to the untouched recommendation must be named, not lumped
    together as "the available evidence", which reads as though something was found."""
    draft_event = event("narrative-no-effect")
    seeds = _seed_dir(tmp_path, seasonality={"category": {"啤酒": {"7": 1.01}}}, holidays={})
    report = build_attribution_report(
        _replayed_case(draft_event),
        {"summary": "季节性证据适用。", "partial": False,
         "findings": [_finding("SEASONAL_SHIFT", "seasonality")]},
        seeds=seeds,
    )
    summary = report["summary"]
    assert "季节性变化" in summary
    assert f"建议量仍是 {report['bare_baseline_qty']} 件" in summary
    assert "请审核时补充说明" in summary


def test_summary_is_written_in_the_case_output_language(tmp_path):
    draft_event = event("narrative-en")
    seeds = _seed_dir(tmp_path, seasonality={"sku": {"sku-1": {"7": 1.6}}}, holidays={})
    case = _replayed_case(draft_event) | {"output_language": "en-US"}
    report = build_attribution_report(
        case,
        {"summary": "Seasonal evidence applies.", "partial": False,
         "findings": [_finding("SEASONAL_SHIFT", "seasonality")]},
        seeds=seeds,
    )
    assert report["summary"].startswith("Test · sku-1 (store shop-1, 2026-07-23)")
    assert "the system suggested" in report["summary"]
    assert "Seasonal Shift" in report["summary"]


def test_model_direction_contradicting_the_replay_is_flagged(tmp_path):
    """A confident sentence must not silently disagree with its own row."""
    draft_event = event("direction-event")
    # The seed lifts July demand, so replay must attribute an increase.
    seeds = _seed_dir(tmp_path, seasonality={"sku": {"sku-1": {"7": 1.6}}}, holidays={})
    finding = _finding("SEASONAL_SHIFT", "seasonality") | {"expected_direction": "DECREASE"}
    report = build_attribution_report(
        _replayed_case(draft_event),
        {"summary": "s", "partial": False, "findings": [finding]},
        seeds=seeds,
    )
    assert report["allocations"][0]["signed_contribution_qty"] > 0
    assert report["allocations"][0]["expected_direction"] == "DECREASE"
    assert "DIRECTION_CONTRADICTS_EVIDENCE" in report["risk_flags"]
    assert "方向与重算结果相反" in report["summary"]


def test_allocation_is_anchored_on_a_world_without_the_named_causes(tmp_path):
    """Shapley must measure each cause from a world where it is switched off.

    Anchoring on the engine's own reproduction made every factor cause worth
    exactly zero: the coalition replay re-asserted the factor the engine had
    already applied, so the difference was zero by construction.
    """
    draft_event = event("anchor-event")
    seeds = _seed_dir(tmp_path, seasonality={"sku": {"sku-1": {"7": 1.6}}},
                      holidays={"dates": {"07-23": 1.5}})
    report = build_attribution_report(
        _replayed_case(draft_event),
        {"summary": "Seasonal and holiday evidence assessed", "partial": False,
         "findings": [_finding("SEASONAL_SHIFT", "seasonality"),
                      _finding("HOLIDAY_EFFECT", "seasonality")]},
        seeds=seeds,
    )
    # The engine's own advice is still reported, so replay drift stays visible.
    assert report["baseline_qty"] == report["recommended_qty"]
    assert report["replay_drift_qty"] == 0
    assert "SNAPSHOT_REPLAY_DRIFT" not in report["risk_flags"]
    # Switching the named causes off has to reach a genuinely different order.
    assert report["conservation_anchor_qty"] == report["bare_baseline_qty"]
    assert report["bare_baseline_qty"] < report["baseline_qty"]

    allocated = sum(item["signed_contribution_qty"] for item in report["allocations"])
    assert allocated + report["unexplained_signed_gap"] == pytest.approx(
        report["override_qty"] - report["bare_baseline_qty"])
    # Efficiency now lands on the quantity the evidence actually accounts for.
    assert allocated == pytest.approx(report["explained_signed_qty"])
    assert report["attributed_qty"] - report["bare_baseline_qty"] == pytest.approx(
        report["explained_signed_qty"])
    assert report["shapley"]["error_estimate"] == pytest.approx(0)

    # Both causes now carry weight instead of scoring a structural zero.
    assert report["explained_signed_qty"] != 0
    assert all(item["signed_contribution_qty"] > 0 for item in report["allocations"])
    assert "EVIDENCE_MATCHES_BASELINE" not in report["risk_flags"]
    for item in report["allocations"]:
        assert item["counterfactual_result"]["baseline_qty"] == report["bare_baseline_qty"]
    weights = sum(item["absolute_contribution_weight"] for item in report["allocations"])
    assert weights <= 1.0 + 1e-9


def test_evidence_agreeing_with_the_engine_is_quantified_but_explains_no_override(tmp_path):
    """A seed that restates the engine's factor is part of the advice, not of the gap."""
    draft_event = event("degenerate-event")
    # skills.soft.factors already applies 1.25 to 啤酒 in July; the seed agrees.
    seeds = _seed_dir(tmp_path, seasonality={"category": {"啤酒": {"7": 1.25}}}, holidays={})
    report = build_attribution_report(
        _replayed_case(draft_event),
        {"summary": "Seasonal evidence assessed", "partial": False,
         "findings": [_finding("SEASONAL_SHIFT", "seasonality")]},
        seeds=seeds,
    )
    # It moved the recommendation, so it is measurable and carries an allocation.
    assert report["explained_signed_qty"] > 0
    assert report["allocations"][0]["signed_contribution_qty"] > 0
    counterfactual = report["allocations"][0]["counterfactual_result"]
    assert counterfactual["counterfactual_qty"] != counterfactual["baseline_qty"]
    assert "EVIDENCE_MATCHES_BASELINE" not in report["risk_flags"]
    # But it argues for the quantity the engine already proposed, so none of the
    # store manager's disagreement is accounted for and the residual holds it all.
    assert report["unexplained_signed_gap"] == pytest.approx(report["signed_gap"])
    assert "UNEXPLAINED_RESIDUAL" in report["risk_flags"]


def test_coalition_replays_are_memoised(tmp_path, monkeypatch):
    draft_event = event("memo-event")
    seeds = _seed_dir(tmp_path, seasonality={"sku": {"sku-1": {"7": 1.6}}},
                      holidays={"dates": {"07-23": 1.5}})
    calls = 0
    original = deterministic.replay_engine

    def counting_replay(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(deterministic, "replay_engine", counting_replay)
    report = build_attribution_report(
        _replayed_case(draft_event),
        {"summary": "Seasonal and holiday evidence assessed", "partial": False,
         "findings": [_finding("SEASONAL_SHIFT", "seasonality"),
                      _finding("HOLIDAY_EFFECT", "seasonality")]},
        seeds=seeds,
    )
    assert len(report["allocations"]) == 2
    # The engine's own reproduction, the bare baseline that anchors the empty
    # coalition, and the 2^n - 1 non-empty coalitions. Shapley alone asks for
    # n*2^n = 8 evaluations, and the report needs several more after it.
    assert calls == 5


def test_snapshot_that_no_longer_reproduces_the_advice_is_flagged(tmp_path):
    """Stored advice and replayed advice can diverge; the gap must not be hidden."""
    draft_event = event("drift-event")
    seeds = _seed_dir(tmp_path, seasonality={"sku": {"sku-1": {"7": 1.6}}}, holidays={})
    # event() pins recommended_qty=10 while the snapshot now replays to something else.
    report = build_attribution_report(
        _case(draft_event),
        {"summary": "Seasonal evidence assessed", "partial": False,
         "findings": [_finding("SEASONAL_SHIFT", "seasonality")]},
        seeds=seeds,
    )
    assert report["baseline_qty"] != report["recommended_qty"]
    assert report["replay_drift_qty"] == report["baseline_qty"] - report["recommended_qty"]
    assert "SNAPSHOT_REPLAY_DRIFT" in report["risk_flags"]
    # Conservation is still exact: drift is absorbed by the residual, never by a cause.
    allocated = sum(item["signed_contribution_qty"] for item in report["allocations"])
    assert allocated + report["unexplained_signed_gap"] == pytest.approx(
        report["override_qty"] - report["conservation_anchor_qty"])


# --- the model/quantifier contract -------------------------------------------------

def test_model_cause_vocabulary_cannot_drift_from_the_quantifiers():
    """Guards the defect that made every production report explain nothing.

    ``cause_code`` used to be a free-form string, so the model invented a new spelling
    almost every run -- "SEASONAL", "SEASONAL_HOLIDAY", "SUBSTITUTION_EVIDENCE_APPLICABLE"
    -- and ``build_attribution_report`` dropped each one silently because it keys on exact
    strings. Both halves now derive from one literal, and this pins them together.
    """
    from pydantic import ValidationError

    from attribution.harness import HarnessFinding

    declared = HarnessFinding.model_json_schema()["properties"]["cause_code"]["enum"]
    assert set(declared) == set(CAUSE_CODES) == set(CAUSE_RESOLVERS)

    for code in CAUSE_CODES:
        finding = HarnessFinding.model_validate(
            {"cause_code": code, "domain": "seasonality", "applicable": True,
             "explanation": "a retail condition that a buyer would recognise"})
        assert finding.cause_code == code

    for invented in ("SEASONAL", "SEASONAL_HOLIDAY", "SUBSTITUTION",
                     "SUBSTITUTION_EVIDENCE_APPLICABLE", "seasonal_shift"):
        with pytest.raises(ValidationError):
            HarnessFinding.model_validate(
                {"cause_code": invented, "domain": "seasonality", "applicable": True,
                 "explanation": "a retail condition that a buyer would recognise"})


def test_unrecognised_cause_codes_are_reported_rather_than_dropped(tmp_path):
    """A replayed or hand-written payload can still carry a stale code; say so."""
    draft_event = event("unknown-cause-event")
    seeds = _seed_dir(tmp_path, seasonality={"sku": {"sku-1": {"7": 1.6}}}, holidays={})
    report = build_attribution_report(
        _replayed_case(draft_event),
        {"summary": "s", "partial": False,
         "findings": [_finding("SEASONAL", "seasonality")]},
        seeds=seeds,
    )
    assert report["allocations"] == []
    assert report["unknown_cause_codes"] == ["SEASONAL"]
    assert "UNKNOWN_CAUSE_CODE" in report["risk_flags"]
    assert "无法识别的原因类型" in report["summary"]


def test_applicable_cause_without_seed_evidence_is_reported(tmp_path):
    """'No cause applied' and 'no data to score the cause' need opposite follow-up."""
    draft_event = event("no-evidence-event")
    # The model asserts a holiday, but the seed holds no factor for this date.
    seeds = _seed_dir(tmp_path, seasonality={"sku": {"sku-1": {"7": 1.6}}}, holidays={})
    report = build_attribution_report(
        _replayed_case(draft_event),
        {"summary": "s", "partial": False,
         "findings": [_finding("SEASONAL_SHIFT", "seasonality"),
                      _finding("HOLIDAY_EFFECT", "seasonality")]},
        seeds=seeds,
    )
    assert [row["cause_code"] for row in report["allocations"]] == ["SEASONAL_SHIFT"]
    assert report["unquantifiable_cause_codes"] == ["HOLIDAY_EFFECT"]
    assert "EVIDENCE_UNAVAILABLE_FOR_CAUSE" in report["risk_flags"]
    assert "没有对应的可量化证据数据" in report["summary"]


# --- substitution ------------------------------------------------------------------

_RELATIONSHIP = {"relationships": [{
    "target_goods_code": "sku-1", "substitute_goods_code": "sku-2",
    "relationship_direction": 1, "transfer_rate": 0.5, "max_transfer_ratio": 0.5,
}]}


def _with_substitute(draft_event, *, position: float) -> dict:
    """A snapshot carrying the frozen substitute evidence the API now writes."""
    snapshot = json.loads(json.dumps(draft_event.recommendation_snapshot))
    snapshot["substitution_evidence"] = {"sku-2": {
        "substitute_goods_code": "sku-2",
        "substitute_reconstructed_daily_demand": 4.0,
        "substitute_reorder_point": 10.0,
        "substitute_available_position": position,
        "target_true_daily_demand": 3.0,
    }}
    return snapshot


def test_substitution_seed_and_snapshot_together_explain_an_override(tmp_path):
    """Both halves are required: a relationship AND the substitute's frozen position."""
    draft_event = event("substitution-event")
    seeds = _seed_dir(tmp_path, seasonality={}, holidays={}, substitutions=_RELATIONSHIP)
    snapshot = _with_substitute(draft_event, position=0)
    baseline = int(replay_engine(snapshot)["final_qty"])
    report = build_attribution_report(
        {"case_id": "case-substitution", "recommended_qty": baseline,
         "override_qty": baseline + 12, "snapshot": snapshot},
        {"summary": "s", "partial": False,
         "findings": [_finding("SUBSTITUTION_TRANSFER", "substitution")]},
        seeds=seeds,
    )
    assert [row["cause_code"] for row in report["allocations"]] == ["SUBSTITUTION_TRANSFER"]
    assert report["allocations"][0]["signed_contribution_qty"] > 0
    assert report["coverage_ratio"] > 0
    assert "EVIDENCE_MATCHES_BASELINE" not in report["risk_flags"]
    assert "EVIDENCE_UNAVAILABLE_FOR_CAUSE" not in report["risk_flags"]
    # The frozen substitute is named in the evidence so a reviewer can check it.
    payload = report["evidence"][0]["payload"]
    assert payload["substitute_goods_code"] == "sku-2"
    assert payload["inventory_pressure"] == pytest.approx(1.0)
    allocated = sum(row["signed_contribution_qty"] for row in report["allocations"])
    assert allocated + report["unexplained_signed_gap"] == pytest.approx(report["signed_gap"])


def test_a_well_stocked_substitute_transfers_demand_away(tmp_path):
    """Pressure is signed: an over-stocked neighbour pulls demand off this SKU."""
    draft_event = event("substitution-slack")
    seeds = _seed_dir(tmp_path, seasonality={}, holidays={}, substitutions=_RELATIONSHIP)
    stressed = deterministic._substitution_delta(
        _with_substitute(draft_event, position=0), seeds.load("substitutions"))
    slack = deterministic._substitution_delta(
        _with_substitute(draft_event, position=20), seeds.load("substitutions"))
    assert stressed is not None and slack is not None
    assert stressed[0] > 0 > slack[0]
    # The cap is expressed against the target's own demand, never the substitute's.
    assert stressed[0] == pytest.approx(stressed[1]["max_abs_delta"])


def test_substitution_without_snapshot_evidence_cannot_be_quantified(tmp_path):
    """A relationship alone is not enough; this is the half that was missing in the API."""
    draft_event = event("substitution-no-snapshot")
    seeds = _seed_dir(tmp_path, seasonality={}, holidays={}, substitutions=_RELATIONSHIP)
    report = build_attribution_report(
        _replayed_case(draft_event),
        {"summary": "s", "partial": False,
         "findings": [_finding("SUBSTITUTION_TRANSFER", "substitution")]},
        seeds=seeds,
    )
    assert report["allocations"] == []
    assert report["unquantifiable_cause_codes"] == ["SUBSTITUTION_TRANSFER"]
    assert "EVIDENCE_UNAVAILABLE_FOR_CAUSE" in report["risk_flags"]


def test_substitute_lookup_reads_the_shipped_seed():
    """Snapshot construction and replay must agree on which substitutes matter."""
    seed = SeedRepository().load("substitutions")
    assert substitute_codes_for_target("653270", seed) == ["653269"]
    assert substitute_codes_for_target("653269", seed) == ["653270"]
    assert substitute_codes_for_target("no-such-sku", seed) == []
