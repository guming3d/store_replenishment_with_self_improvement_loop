"""HTTP smoke tests for the outcome, accuracy and knowledge endpoints."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from api import main


async def _authenticated_client() -> tuple[AsyncClient, dict[str, str]]:
    client = AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test")
    login = await client.post(
        "/api/auth/login", json={"username": "dmall", "password": "dmalltest"})
    assert login.status_code == 200
    token = login.json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_outcome_and_knowledge_endpoints(monkeypatch, tmp_path):
    database_path = (tmp_path / "outcome-api.db").as_posix()
    monkeypatch.setenv("ATTRIBUTION_DATABASE_URL", f"sqlite+aiosqlite:///{database_path}")
    monkeypatch.setenv("ATTRIBUTION_WORKER_ENABLED", "false")
    monkeypatch.setenv("ATTRIBUTION_INIT_SCHEMA", "true")

    async with main.app.router.lifespan_context(main.app):
        client, headers = await _authenticated_client()
        try:
            empty = await client.get("/api/attribution/accuracy", headers=headers)
            assert empty.status_code == 200
            # Nothing scored yet must read as no data, never as a zero-error engine.
            assert empty.json()["scored_count"] == 0
            assert empty.json()["engine_mae"] is None

            ingested = await client.post(
                "/api/attribution/outcomes/daily-sales",
                json={"source": "POS", "records": [
                    {"shop_code": "1011", "goods_code": "653269",
                     "sales_date": "2026-07-24", "units_sold": 6, "lost_sales_units": 2},
                ]},
                headers=headers,
            )
            assert ingested.status_code == 200, ingested.text
            assert ingested.json()["ingested"] == 1

            duplicate_lines = await client.post(
                "/api/attribution/outcomes/daily-sales",
                json={"records": [
                    {"shop_code": "1011", "goods_code": "653269",
                     "sales_date": "2026-07-24", "units_sold": 6},
                    {"shop_code": "1011", "goods_code": "653269",
                     "sales_date": "2026-07-24", "units_sold": 9},
                ]},
                headers=headers,
            )
            assert duplicate_lines.status_code == 422

            listed = await client.get(
                "/api/attribution/outcomes?status=COMPLETE", headers=headers)
            assert listed.status_code == 200
            assert listed.json()["items"] == []

            bad_status = await client.get(
                "/api/attribution/outcomes?status=NOPE", headers=headers)
            assert bad_status.status_code == 422

            knowledge = await client.get(
                "/api/attribution/knowledge?status=ACTIVE", headers=headers)
            assert knowledge.status_code == 200
            assert knowledge.json()["items"] == []

            resolved = await client.get(
                "/api/attribution/knowledge/resolve?shop_code=1011&goods_code=653269",
                headers=headers,
            )
            assert resolved.status_code == 200
            assert resolved.json()["entries"] == {}

            claims = await client.get(
                "/api/attribution/claims/feedback", headers=headers)
            assert claims.status_code == 200
            # As with accuracy above, an unscored system must read as no data
            # rather than as store managers who are always right.
            assert claims.json()["judged_total"] == 0
            assert claims.json()["supported_rate"] is None
            assert claims.json()["by_reason_code"] == {}

            filtered = await client.get(
                "/api/attribution/claims/feedback"
                "?date_from=2026-07-01T00:00:00Z&shop_code=1011",
                headers=headers,
            )
            assert filtered.status_code == 200
        finally:
            await client.aclose()
