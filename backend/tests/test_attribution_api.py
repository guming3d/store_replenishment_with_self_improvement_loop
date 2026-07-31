from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from api import main
from attribution.deterministic import build_attribution_report
from attribution.harness import HarnessAttributionOutput
from attribution.worker import AttributionWorker


async def _authenticated_client() -> tuple[AsyncClient, dict[str, str]]:
    client = AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test")
    login = await client.post(
        "/api/auth/login", json={"username": "dmall", "password": "dmalltest"})
    assert login.status_code == 200
    return client, {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _wait_for_case_status(
    client: AsyncClient,
    headers: dict[str, str],
    case_id: str,
    expected: str,
    timeout: float = 10,
) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(
            f"/api/attribution/cases/{case_id}", headers=headers)
        assert response.status_code == 200
        detail = response.json()
        if detail["status"] == expected:
            return detail
        assert detail["status"] not in {"FAILED", "CANCELLED", "SUPERSEDED"}
        await asyncio.sleep(0.05)
    raise AssertionError(f"case {case_id} did not reach {expected}")


async def _controlled_harness_executor(case: dict) -> dict:
    harness_output = HarnessAttributionOutput.model_validate({
        "findings": [{
            "cause_code": "HOLIDAY_EFFECT",
            "domain": "seasonality",
            "applicable": True,
            "evidence_refs": ["holiday-seed"],
            "explanation": "The decision date is a versioned holiday demand event.",
        }],
        "summary": "Holiday evidence applies; deterministic replay owns the quantity allocation.",
        "partial": False,
    })
    return await asyncio.to_thread(
        build_attribution_report, case, harness_output.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_mandatory_attribution_gate_end_to_end(monkeypatch):
    monkeypatch.setenv("ATTRIBUTION_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("ATTRIBUTION_WORKER_ENABLED", "false")
    monkeypatch.setenv("ATTRIBUTION_INIT_SCHEMA", "true")

    async with main.app.router.lifespan_context(main.app):
        transport = ASGITransport(app=main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            login = await client.post(
                "/api/auth/login", json={"username": "dmall", "password": "dmalltest"})
            assert login.status_code == 200
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            unsupported_flow = await client.post(
                "/api/replenish/batch",
                json={"shop_code": "1011", "date": "2026-07-23", "flow": "B"},
                headers=headers,
            )
            assert unsupported_flow.status_code == 422

            generated = await client.post(
                "/api/replenish/batch",
                json={"shop_code": "1011", "date": "2026-07-23"},
                headers=headers,
            )
            assert generated.status_code == 200
            results = generated.json()
            assert results
            selected = results[0]
            run_id = selected["run_id"]

            adjusted = await client.post(
                "/api/replenish/adjust",
                json={
                    "run_id": run_id,
                    "output_language": "en-US",
                    "items": [{
                        "sku": selected["sku"],
                        "final_qty": selected["chosen_qty"] + 5,
                        "reason_code": "OPERATOR",
                        "reason_text": "Local event observed",
                        "event_id": "api-event-1",
                    }],
                },
                headers=headers,
            )
            assert adjusted.status_code == 202
            case_id = adjusted.json()["case_ids"][0]
            localized_case = await client.get(
                f"/api/attribution/cases/{case_id}", headers=headers)
            assert localized_case.json()["output_language"] == "en-US"

            repository = main.attribution_repository
            assert repository is not None
            claimed = await repository.claim_case("api-test-worker")
            attempt = await repository.begin_attempt(case_id, "api-test-worker")
            signed_gap = claimed["override_qty"] - claimed["recommended_qty"]
            await repository.finish_attempt(
                case_id,
                attempt["attempt_id"],
                "api-test-worker",
                report={
                    "summary": "Evidence-backed test attribution",
                    "allocations": [{
                        "cause_code": "OTHER",
                        "domain": "manual",
                        "signed_contribution_qty": signed_gap,
                        "explanation": "Test evidence",
                        "evidence_refs": [],
                    }],
                    "signed_gap": signed_gap,
                    "unexplained_signed_gap": 0,
                    "coverage_ratio": 1,
                    "partial": False,
                },
            )

            detail = (await client.get(
                f"/api/attribution/cases/{case_id}", headers=headers)).json()
            reviewed = await client.post(
                f"/api/attribution/cases/{case_id}/reviews",
                json={
                    "action": "APPROVE",
                    "expected_version": detail["version"],
                    "expected_report_version": detail["latest_report"]["version"],
                    "comment": "Approved for submission",
                },
                headers=headers,
            )
            assert reviewed.status_code == 200, reviewed.text
            assert reviewed.json()["status"] == "HUMAN_APPROVED"

            readiness = await client.get(
                f"/api/runs/{run_id}/submission-readiness", headers=headers)
            assert readiness.status_code == 200
            assert readiness.json()["ready"] is True

            submitted = await client.post(
                f"/api/runs/{run_id}/submit",
                json={"expected_version": readiness.json()["run_version"]},
                headers=headers,
            )
            assert submitted.status_code == 200
            assert submitted.json()["status"] == "SUBMITTED_LOCKED"
            assert submitted.json()["submitted_by"] == "dmall"

            locked_edit = await client.post(
                "/api/replenish/adjust",
                json={
                    "run_id": run_id,
                    "items": [{
                        "sku": selected["sku"],
                        "final_qty": selected["chosen_qty"] + 6,
                        "reason_code": "OPERATOR",
                        "event_id": "api-event-2",
                    }],
                },
                headers=headers,
            )
            assert locked_edit.status_code == 409
            assert locked_edit.json()["code"] == "INVALID_STATE_TRANSITION"


@pytest.mark.asyncio
async def test_replenishment_to_causal_analysis_human_review_and_submission(
    monkeypatch, tmp_path,
):
    """Exercise the full loop without calling the external Foundry service."""
    database_path = (tmp_path / "attribution-loop.db").as_posix()
    monkeypatch.setenv(
        "ATTRIBUTION_DATABASE_URL", f"sqlite+aiosqlite:///{database_path}")
    monkeypatch.setenv("ATTRIBUTION_WORKER_ENABLED", "false")
    monkeypatch.setenv("ATTRIBUTION_INIT_SCHEMA", "true")

    async with main.app.router.lifespan_context(main.app):
        client, headers = await _authenticated_client()
        worker = None
        try:
            generated = await client.post(
                "/api/replenish/batch",
                json={"shop_code": "1011", "date": "2026-10-01"},
                headers=headers,
            )
            assert generated.status_code == 200
            results = generated.json()
            selected = next(
                (result for result in results if result["chosen_qty"] > 0), results[0])
            run_id = selected["run_id"]
            override_qty = selected["chosen_qty"] + 5

            adjusted = await client.post(
                "/api/replenish/adjust",
                json={
                    "run_id": run_id,
                    "items": [{
                        "sku": selected["sku"],
                        "final_qty": override_qty,
                        "reason_code": "LOCAL_HOLIDAY_EVENT",
                        "reason_text": "Store expects additional National Day demand.",
                        "event_id": "full-loop-event-1",
                    }],
                },
                headers=headers,
            )
            assert adjusted.status_code == 202
            adjustment = adjusted.json()
            case_id = adjustment["case_ids"][0]
            job_id = adjustment["job_id"]
            assert adjustment["gate_status"] == "ATTRIBUTION_RUNNING"

            blocked = await client.get(
                f"/api/runs/{run_id}/submission-readiness", headers=headers)
            assert blocked.status_code == 200
            assert blocked.json()["ready"] is False
            assert blocked.json()["modified_count"] == 1

            repository = main.attribution_repository
            assert repository is not None
            worker = AttributionWorker(
                repository,
                executor=_controlled_harness_executor,
                concurrency=1,
                lease_seconds=10,
                attempt_timeout=60,
            )
            await worker.start()
            detail = await _wait_for_case_status(
                client, headers, case_id, "NEEDS_REVIEW", timeout=60)
            await worker.stop()
            worker = None
            attempt = detail["attempts"][0]
            assert attempt["raw_log_available"] is True
            raw_log = await client.get(
                f"/api/attribution/cases/{case_id}/attempts/1/raw-log",
                headers=headers,
            )
            assert raw_log.status_code == 200
            assert raw_log.headers["content-type"].startswith("application/x-ndjson")
            assert '"event_type":"ATTEMPT_STARTED"' in raw_log.text

            job = await client.get(
                f"/api/attribution/jobs/{job_id}", headers=headers)
            assert job.status_code == 200
            assert job.json()["status"] == "COMPLETED"
            assert job.json()["completed_cases"] == 1

            report = detail["latest_report"]
            assert report["source"] == "AGENT"
            assert report["partial"] is False
            # The buyer-facing summary is composed after the numbers exist, so it names the
            # quantities being reconciled instead of restating which findings were set.
            assert str(report["recommended_qty"]) in report["summary"]
            assert str(report["override_qty"]) in report["summary"]
            assert "节假日影响" in report["summary"]
            # The model's own words are still retained verbatim for review and trace comparison.
            assert report["model_summary"].startswith("Holiday evidence applies")
            assert report["shapley_method"] == "exact"
            assert report["allocations"][0]["cause_code"] == "HOLIDAY_EFFECT"
            assert report["allocations"][0]["counterfactual_result"]["cause_code"] == (
                "HOLIDAY_EFFECT")
            assert report["evidence"][0]["source_version"] == "holiday-seed-v1"
            assert (
                sum(item["signed_contribution_qty"] for item in report["allocations"])
                + report["unexplained_signed_gap"]
            ) == pytest.approx(
                report["override_qty"] - report["conservation_anchor_qty"])
            assert report["signed_gap"] == 5
            assert [attempt["status"] for attempt in detail["attempts"]] == ["SUCCEEDED"]
            assert [event["event_type"] for event in detail["trace_events"]] == [
                "ATTEMPT_STARTED", "ATTEMPT_COMPLETED"]

            still_blocked = await client.get(
                f"/api/runs/{run_id}/submission-readiness", headers=headers)
            assert still_blocked.json()["ready"] is False
            assert still_blocked.json()["blockers"][0]["status"] == "NEEDS_REVIEW"

            reviewed = await client.post(
                f"/api/attribution/cases/{case_id}/reviews",
                json={
                    "action": "APPROVE",
                    "expected_version": detail["version"],
                    "expected_report_version": report["version"],
                    "comment": "Evidence and conserved allocation reviewed.",
                    "publish_knowledge": True,
                    "knowledge_scope": "SHOP_SKU",
                    "knowledge_expires_at": "2027-01-31T23:59:59+00:00",
                },
                headers=headers,
            )
            assert reviewed.status_code == 200, reviewed.text
            assert reviewed.json()["status"] == "HUMAN_APPROVED"
            assert reviewed.json()["reviews"][0]["reviewer"] == "dmall"

            knowledge = await client.get(
                "/api/attribution/knowledge", headers=headers)
            assert knowledge.status_code == 200
            assert knowledge.json()["items"][0]["case_id"] == case_id

            readiness = await client.get(
                f"/api/runs/{run_id}/submission-readiness", headers=headers)
            assert readiness.status_code == 200
            gate = readiness.json()
            assert gate["ready"] is True
            assert gate["approved_count"] == 1

            submitted = await client.post(
                f"/api/runs/{run_id}/submit",
                json={"expected_version": gate["run_version"]},
                headers=headers,
            )
            assert submitted.status_code == 200
            assert submitted.json()["status"] == "SUBMITTED_LOCKED"

            run_detail = await client.get(
                f"/api/runs/{run_id}", headers=headers)
            assert run_detail.status_code == 200
            submitted_line = next(
                item for item in run_detail.json()["results"]
                if item["sku"] == selected["sku"])
            assert submitted_line["final_qty"] == override_qty
            assert submitted_line["attribution_status"] == "HUMAN_APPROVED"
            assert run_detail.json()["status"] == "SUBMITTED_LOCKED"
        finally:
            if worker:
                await worker.stop()
            await client.aclose()


def test_recommendation_snapshot_freezes_substitute_evidence():
    """The substitution quantifier reads the substitute's numbers out of the snapshot.

    Nothing used to write them, so ``SUBSTITUTION_TRANSFER`` could never be quantified
    however confidently the model asserted it.
    """
    from datetime import date

    from attribution.deterministic import SeedRepository, substitute_codes_for_target

    seed = SeedRepository().load("substitutions")
    shop = main.SHOPS[0]["shop_code"]
    target = "653270"
    substitutes = substitute_codes_for_target(target, seed)
    assert substitutes, "the shipped seed must define a relationship to exercise"

    result = main.run(shop, main.SKU_MAP[target], main.FC[f"{shop}_{target}"],
                      date(2026, 7, 27), flow="A")
    snapshot = main._recommendation_snapshot({}, result)

    evidence = snapshot["substitution_evidence"]
    assert set(evidence) == set(substitutes)
    frozen = evidence[substitutes[0]]
    # Exactly the inputs substitution_target_daily_delta requires, or it silently no-ops.
    assert {"substitute_reconstructed_daily_demand", "substitute_reorder_point",
            "substitute_available_position", "target_true_daily_demand"} <= set(frozen)
    assert frozen["target_true_daily_demand"] == result["demand"]["true_mean"]
    assert all(isinstance(frozen[key], (int, float)) for key in
               ("substitute_reconstructed_daily_demand", "substitute_reorder_point",
                "substitute_available_position", "target_true_daily_demand"))


def test_recommendation_snapshot_omits_evidence_for_unrelated_skus():
    """A SKU with no relationship must not pay for an extra engine replay."""
    from datetime import date

    shop = main.SHOPS[0]["shop_code"]
    result = main.run(shop, main.SKU_MAP["463497"], main.FC[f"{shop}_463497"],
                      date(2026, 7, 27), flow="A")
    assert main._recommendation_snapshot({}, result)["substitution_evidence"] == {}
