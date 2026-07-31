from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from api import main


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login(client: AsyncClient, username: str, password: str) -> dict:
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


async def _seed_pending_case(client: AsyncClient, headers: dict[str, str]) -> tuple[str, str]:
    """Create a run with one overridden line, leaving a case awaiting attribution."""
    generated = await client.post(
        "/api/replenish/batch",
        json={"shop_code": "1011", "date": "2026-07-23"},
        headers=headers,
    )
    assert generated.status_code == 200
    selected = generated.json()[0]
    adjusted = await client.post(
        "/api/replenish/adjust",
        json={
            "run_id": selected["run_id"],
            "items": [{
                "sku": selected["sku"],
                "final_qty": selected["chosen_qty"] + 5,
                "reason_code": "OPERATOR",
                "reason_text": "Local event observed",
                "event_id": "admin-event-1",
            }],
        },
        headers=headers,
    )
    assert adjusted.status_code == 202, adjusted.text
    return selected["run_id"], adjusted.json()["case_ids"][0]


@pytest.fixture
def api_environment(monkeypatch):
    monkeypatch.setenv("ATTRIBUTION_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("ATTRIBUTION_WORKER_ENABLED", "false")
    monkeypatch.setenv("ATTRIBUTION_INIT_SCHEMA", "true")


@pytest.mark.asyncio
async def test_login_reports_the_role_of_each_account(api_environment):
    async with main.app.router.lifespan_context(main.app):
        async with AsyncClient(transport=ASGITransport(app=main.app),
                               base_url="http://test") as client:
            buyer = await _login(client, "dmall", "dmalltest")
            admin = await _login(client, "dmall-admin", "dmalladmin")

    assert buyer["role"] == "buyer"
    assert admin["role"] == "admin"


@pytest.mark.asyncio
async def test_auth_me_identifies_the_caller(api_environment):
    async with main.app.router.lifespan_context(main.app):
        async with AsyncClient(transport=ASGITransport(app=main.app),
                               base_url="http://test") as client:
            admin = await _login(client, "dmall-admin", "dmalladmin")
            me = await client.get("/api/auth/me", headers=_headers(admin["access_token"]))

    assert me.status_code == 200
    assert me.json() == {"username": "dmall-admin", "role": "admin"}


@pytest.mark.asyncio
async def test_buyer_token_cannot_reach_administrator_routes(api_environment):
    """The guard has to live in the API; a hidden UI route is not a boundary."""
    async with main.app.router.lifespan_context(main.app):
        async with AsyncClient(transport=ASGITransport(app=main.app),
                               base_url="http://test") as client:
            buyer = await _login(client, "dmall", "dmalltest")
            headers = _headers(buyer["access_token"])
            responses = {
                "overview": await client.get("/api/admin/overview", headers=headers),
                "jobs": await client.get("/api/admin/jobs", headers=headers),
                "queue": await client.get("/api/admin/review-queue", headers=headers),
                "dismiss": await client.post(
                    "/api/admin/attribution/cases/bulk-dismiss",
                    json={"cases": [{"case_id": "x", "expected_version": 1}], "reason": "no"},
                    headers=headers),
            }

    assert [response.status_code for response in responses.values()] == [403, 403, 403, 403]


@pytest.mark.asyncio
async def test_administrator_routes_still_reject_anonymous_callers(api_environment):
    async with main.app.router.lifespan_context(main.app):
        async with AsyncClient(transport=ASGITransport(app=main.app),
                               base_url="http://test") as client:
            anonymous = await client.get("/api/admin/overview")

    assert anonymous.status_code == 401


@pytest.mark.asyncio
async def test_public_health_does_not_leak_worker_internals(api_environment):
    """/api/health is exempt from authentication, so it must stay minimal."""
    async with main.app.router.lifespan_context(main.app):
        async with AsyncClient(transport=ASGITransport(app=main.app),
                               base_url="http://test") as client:
            health = await client.get("/api/health")

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "pairs": health.json()["pairs"]}
    assert "attribution_worker" not in health.json()


@pytest.mark.asyncio
async def test_overview_reports_queue_depth_to_administrators(api_environment):
    async with main.app.router.lifespan_context(main.app):
        async with AsyncClient(transport=ASGITransport(app=main.app),
                               base_url="http://test") as client:
            buyer = await _login(client, "dmall", "dmalltest")
            await _seed_pending_case(client, _headers(buyer["access_token"]))
            admin = await _login(client, "dmall-admin", "dmalladmin")
            overview = (await client.get(
                "/api/admin/overview", headers=_headers(admin["access_token"]))).json()

    assert overview["cases_by_state"]["QUEUED"] == 1
    assert overview["backlog"]["queued"] == 1
    assert overview["backlog"]["oldest_age_seconds"] is not None
    assert overview["attribution_worker"]["running"] is False


@pytest.mark.asyncio
async def test_review_queue_warns_that_dismissal_strands_the_run(api_environment):
    """Cancelling clears the badge but never satisfies readiness, so the UI must be told."""
    async with main.app.router.lifespan_context(main.app):
        async with AsyncClient(transport=ASGITransport(app=main.app),
                               base_url="http://test") as client:
            buyer = await _login(client, "dmall", "dmalltest")
            run_id, case_id = await _seed_pending_case(client, _headers(buyer["access_token"]))
            repository = main.attribution_repository
            claimed = await repository.claim_case("admin-test-worker")
            attempt = await repository.begin_attempt(case_id, "admin-test-worker")
            signed_gap = claimed["override_qty"] - claimed["recommended_qty"]
            await repository.finish_attempt(
                case_id, attempt["attempt_id"], "admin-test-worker",
                report={
                    "summary": "Evidence-backed test attribution",
                    "allocations": [{
                        "cause_code": "OTHER", "domain": "manual",
                        "signed_contribution_qty": signed_gap,
                        "explanation": "Test evidence", "evidence_refs": [],
                    }],
                    "signed_gap": signed_gap, "unexplained_signed_gap": 0,
                    "coverage_ratio": 1, "partial": False,
                })

            admin = await _login(client, "dmall-admin", "dmalladmin")
            queue = (await client.get(
                "/api/admin/review-queue", headers=_headers(admin["access_token"]))).json()

    assert queue["total"] == 1
    entry = queue["items"][0]
    assert entry["case_id"] == case_id
    assert entry["status"] == "NEEDS_REVIEW"
    assert entry["blocks_run"] is True
    assert entry["run_state"] == "ATTRIBUTION_REVIEW_REQUIRED"


@pytest.mark.asyncio
async def test_bulk_dismiss_reports_per_case_outcomes(api_environment):
    """A single stale version must not discard the rest of the batch."""
    async with main.app.router.lifespan_context(main.app):
        async with AsyncClient(transport=ASGITransport(app=main.app),
                               base_url="http://test") as client:
            buyer = await _login(client, "dmall", "dmalltest")
            buyer_headers = _headers(buyer["access_token"])
            _run_id, case_id = await _seed_pending_case(client, buyer_headers)
            detail = (await client.get(
                f"/api/attribution/cases/{case_id}", headers=buyer_headers)).json()

            admin_headers = _headers((await _login(
                client, "dmall-admin", "dmalladmin"))["access_token"])
            result = (await client.post(
                "/api/admin/attribution/cases/bulk-dismiss",
                json={
                    "reason": "duplicate override captured during testing",
                    "cases": [
                        {"case_id": case_id, "expected_version": detail["version"]},
                        {"case_id": case_id, "expected_version": 99},
                    ],
                },
                headers=admin_headers)).json()

            after = (await client.get(
                f"/api/attribution/cases/{case_id}", headers=buyer_headers)).json()
            trace_types = [event["event_type"] for event in after["trace_events"]]

    assert result["succeeded_count"] == 1
    assert result["failed_count"] == 1
    assert result["failed"][0]["case_id"] == case_id
    assert result["failed"][0]["code"] == "CONFLICT"
    assert after["status"] == "CANCELLED"
    assert "ADMIN_DISMISSED" in trace_types


@pytest.mark.asyncio
async def test_dismissing_a_case_leaves_the_run_unsubmittable(api_environment):
    """Documents the consequence the console has to surface before an administrator acts."""
    async with main.app.router.lifespan_context(main.app):
        async with AsyncClient(transport=ASGITransport(app=main.app),
                               base_url="http://test") as client:
            buyer_headers = _headers((await _login(
                client, "dmall", "dmalltest"))["access_token"])
            run_id, case_id = await _seed_pending_case(client, buyer_headers)
            detail = (await client.get(
                f"/api/attribution/cases/{case_id}", headers=buyer_headers)).json()

            admin_headers = _headers((await _login(
                client, "dmall-admin", "dmalladmin"))["access_token"])
            await client.post(
                "/api/admin/attribution/cases/bulk-dismiss",
                json={"reason": "not worth attributing",
                      "cases": [{"case_id": case_id, "expected_version": detail["version"]}]},
                headers=admin_headers)

            readiness = (await client.get(
                f"/api/runs/{run_id}/submission-readiness", headers=buyer_headers)).json()
            badge = (await client.get(
                "/api/attribution/review-count", headers=buyer_headers)).json()

    assert badge["needs_review"] == 0
    assert readiness["ready"] is False
    assert readiness["blockers"][0]["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_admin_jobs_rolls_up_case_states(api_environment):
    async with main.app.router.lifespan_context(main.app):
        async with AsyncClient(transport=ASGITransport(app=main.app),
                               base_url="http://test") as client:
            buyer_headers = _headers((await _login(
                client, "dmall", "dmalltest"))["access_token"])
            run_id, _case_id = await _seed_pending_case(client, buyer_headers)

            admin_headers = _headers((await _login(
                client, "dmall-admin", "dmalladmin"))["access_token"])
            jobs = (await client.get("/api/admin/jobs", headers=admin_headers)).json()

    assert jobs["total"] == 1
    job = jobs["items"][0]
    assert job["run_id"] == run_id
    assert job["total_cases"] == 1
    assert job["cases_by_state"] == {"QUEUED": 1}
    assert job["status"] == "QUEUED"
