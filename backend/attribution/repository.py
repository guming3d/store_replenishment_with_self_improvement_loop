"""Transactional repository and gate service for attribution."""
from __future__ import annotations

import asyncio
import copy
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .deterministic import canonical_json, conserve, snapshot_hash
from .errors import (
    AttributionError, ConflictError, GateBlockedError, NotFoundError, SnapshotUnavailableError,
    StateTransitionError, ValidationError,
)
from .models import (
    AttributionCase, AttributionJob, AttributionReport, DailySalesFact, DecisionOutcome,
    ExecutionAttempt, HumanReview, KnowledgeEntry, KnowledgeRejection, RejectionEvent,
    ReplenishmentRun, SubmissionAudit, TraceEvent, WorkerLease,
)
from .schemas import (
    AdjustDraftRequest, AttributionCaseResponse, CaseState, KnowledgeDecisionInput,
    KnowledgePublishRequest, ManualReportRequest, OutcomeIngestRequest, ReviewRequest,
    RunState, SubmissionReadiness,
)
from . import knowledge as knowledge_math
from . import outcomes as outcome_math


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id() -> str:
    return str(uuid.uuid4())


def _payload_hash(value: object) -> str:
    import hashlib
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _line_key(shop: str, sku: str, decision_date: str) -> str:
    return f"{shop}\x1f{sku}\x1f{decision_date}"


def _as_aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes; comparisons against _now() need a timezone."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _legacy_overrides(record: dict[str, Any]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    fallback_date = str(record.get("ts") or "")[:10]
    for result in record.get("results", []):
        recommended = int(result.get("chosen_qty", result.get("final_qty", 0)) or 0)
        override = int(result.get("final_qty", recommended) or 0)
        if override == recommended:
            continue
        shop_code = str(result.get("shop") or record.get("shop_code") or "")
        goods_code = str(result.get("sku") or result.get("goods_code") or "")
        decision_date = str(result.get("apply_date") or fallback_date)
        if not shop_code or not goods_code or len(decision_date) < 10:
            continue
        decision_date = decision_date[:10]
        key = _line_key(shop_code, goods_code, decision_date)
        overrides[key] = {
            "shop_code": shop_code, "goods_code": goods_code,
            "decision_date": decision_date, "recommended_qty": recommended,
            "override_qty": override,
            "snapshot_hash": _payload_hash({
                "legacy_run_id": record.get("run_id"), "line_key": key,
                "migration_status": "SNAPSHOT_UNAVAILABLE",
            }),
        }
    return overrides


class DownstreamSubmissionAdapter(Protocol):
    async def submit(self, run_id: str, accepted_overrides: dict[str, int]) -> dict[str, Any]: ...


class AttributionRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def record_run(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Create an immutable-source run once; duplicate equivalent imports are safe."""
        now = _now()
        async with self.sessions() as session, session.begin():
            existing = await session.get(ReplenishmentRun, run_id)
            if existing:
                if _payload_hash(existing.payload) != _payload_hash(payload):
                    raise ConflictError("run_id already has different payload", details={"run_id": run_id})
                return self._run_dict(existing)
            run = ReplenishmentRun(
                run_id=run_id, state=RunState.DRAFT, version=1, payload=payload,
                draft_overrides={}, created_at=now, updated_at=now,
            )
            session.add(run)
            return self._run_dict(run)

    async def import_legacy_run_history(self, history_path: str | Path) -> int:
        """Idempotently import legacy ``run_history.json`` records into the DB."""
        records = json.loads(Path(history_path).read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValidationError("legacy run history must be a list")
        imported = 0
        for record in records:
            run_id = str(record.get("run_id") or "")
            if not run_id:
                continue
            async with self.sessions() as session, session.begin():
                existing = await session.get(ReplenishmentRun, run_id)
                legacy_overrides = _legacy_overrides(record)
                if existing:
                    if (legacy_overrides and not existing.draft_overrides and
                            existing.state != RunState.SUBMITTED_LOCKED):
                        existing.draft_overrides = legacy_overrides
                        existing.state = RunState.ATTRIBUTION_REVIEW_REQUIRED
                        existing.version += 1
                        existing.updated_at = _now()
                    continue
                timestamp = record.get("ts")
                try:
                    created = datetime.fromisoformat(timestamp) if timestamp else _now()
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                except ValueError:
                    created = _now()
                session.add(ReplenishmentRun(
                    run_id=run_id,
                    state=(RunState.ATTRIBUTION_REVIEW_REQUIRED
                           if legacy_overrides else RunState.DRAFT),
                    version=1, payload=record, draft_overrides=legacy_overrides,
                    created_at=created, updated_at=created,
                ))
                imported += 1
        return imported

    async def get_run(self, run_id: str, *, include_payload: bool = False) -> dict[str, Any]:
        async with self.sessions() as session:
            run = await session.get(ReplenishmentRun, run_id)
            if not run:
                raise NotFoundError("run not found", details={"run_id": run_id})
            result = self._run_dict(run)
            if include_payload:
                result["payload"] = run.payload
                result["draft_overrides"] = run.draft_overrides
                result["accepted_overrides"] = run.accepted_overrides
            return result

    async def list_runs(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            runs = (await session.scalars(select(ReplenishmentRun).order_by(
                ReplenishmentRun.updated_at.desc()).limit(min(limit, 100)).offset(offset))).all()
            return [self._run_dict(run) for run in runs]

    async def get_run_view(self, run_id: str) -> dict[str, Any]:
        """Return the legacy run payload overlaid with current draft and gate state."""
        async with self.sessions() as session:
            run = await session.get(ReplenishmentRun, run_id)
            if not run:
                raise NotFoundError("run not found", details={"run_id": run_id})
            return await self._run_view(session, run)

    async def list_run_views(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        async with self.sessions() as session:
            runs = (await session.scalars(select(ReplenishmentRun).order_by(
                ReplenishmentRun.created_at.desc()).limit(min(limit, 100)).offset(offset))).all()
            return [await self._run_view(session, run) for run in runs]

    async def find_replenishment_trace(self, trace_id: str) -> dict[str, Any]:
        async with self.sessions() as session:
            runs = (await session.scalars(select(ReplenishmentRun).order_by(
                ReplenishmentRun.created_at.desc()).limit(100))).all()
            for run in runs:
                results = run.payload.get("results", []) if isinstance(run.payload, dict) else []
                result = next((item for item in results if item.get("trace_id") == trace_id), None)
                if result:
                    return copy.deepcopy(result)
        raise NotFoundError("trace not found", details={"trace_id": trace_id})

    async def clear_unreferenced_drafts(self) -> int:
        """Delete only editable runs that have no attribution/audit references."""
        async with self.sessions() as session, session.begin():
            case_run_ids = select(AttributionCase.run_id)
            job_run_ids = select(AttributionJob.run_id)
            event_run_ids = select(RejectionEvent.run_id)
            audited_run_ids = select(SubmissionAudit.run_id)
            result = await session.execute(delete(ReplenishmentRun).where(
                ReplenishmentRun.state.in_([RunState.DRAFT, RunState.READY_TO_SUBMIT]),
                ReplenishmentRun.run_id.not_in(case_run_ids),
                ReplenishmentRun.run_id.not_in(job_run_ids),
                ReplenishmentRun.run_id.not_in(event_run_ids),
                ReplenishmentRun.run_id.not_in(audited_run_ids),
            ))
            return int(result.rowcount or 0)

    async def save_draft_edits(self, request: AdjustDraftRequest) -> dict[str, Any]:
        """Atomically persist events, supersede stale cases, and enqueue isolated cases."""
        now = _now()
        async with self.sessions() as session, session.begin():
            run = await session.scalar(
                select(ReplenishmentRun).where(ReplenishmentRun.run_id == request.run_id).with_for_update()
            )
            if not run:
                raise NotFoundError("run not found", details={"run_id": request.run_id})
            if run.state == RunState.SUBMITTED_LOCKED:
                raise StateTransitionError("submitted run is read-only")
            if request.expected_run_version is not None and run.version != request.expected_run_version:
                raise ConflictError("run version is stale", details={"current_version": run.version})

            duplicate_event_ids: list[str] = []
            duplicate_case_ids: list[str] = []
            duplicate_job_ids: list[str] = []
            created_cases: list[AttributionCase] = []
            new_events = []
            for event in request.events:
                payload = event.model_dump(mode="json")
                event_hash = _payload_hash(payload)
                prior = await session.get(RejectionEvent, event.event_id)
                if prior:
                    if prior.payload_hash != event_hash:
                        raise ConflictError("event_id was reused with a different payload",
                                            details={"event_id": event.event_id})
                    duplicate_event_ids.append(event.event_id)
                    if prior.case_id:
                        prior_case = await session.get(AttributionCase, prior.case_id)
                        if prior_case:
                            duplicate_case_ids.append(prior_case.case_id)
                            duplicate_job_ids.append(prior_case.job_id)
                    continue
                self._validate_event_against_run(run, event)
                new_events.append((event, payload, event_hash))

            if not new_events:
                readiness = await self._readiness_in_session(session, run)
                return {"run_id": run.run_id, "changed": False,
                        "total_qty": sum(int(item["override_qty"]) for item in (run.draft_overrides or {}).values()),
                        "results": [], "job_id": duplicate_job_ids[0] if len(set(duplicate_job_ids)) == 1 else None,
                        "case_ids": duplicate_case_ids, "duplicate_event_ids": duplicate_event_ids,
                        "gate_status": readiness.status.value, "run_version": run.version,
                        "run_state": run.state, "readiness": readiness.model_dump(mode="json")}

            job = AttributionJob(job_id=_id(), run_id=run.run_id, state="QUEUED", version=1,
                                 created_at=now, updated_at=now)
            session.add(job)
            case_ids: list[str] = []
            overrides = dict(run.draft_overrides or {})
            for event, payload, event_hash in new_events:
                key = _line_key(event.shop_code, event.goods_code, event.decision_date.isoformat())
                # Each new edit invalidates the former binding, including previous approval.
                prior_cases = (await session.scalars(select(AttributionCase).where(
                    AttributionCase.run_id == run.run_id,
                    AttributionCase.shop_code == event.shop_code,
                    AttributionCase.goods_code == event.goods_code,
                    AttributionCase.decision_date == event.decision_date.isoformat(),
                ).with_for_update())).all()
                active_cases = [
                    case for case in prior_cases if case.state != CaseState.SUPERSEDED]
                next_case_version = max(
                    (case.case_version for case in prior_cases), default=0) + 1
                new_case_id = _id() if event.override_qty != event.recommended_qty else None
                for old in active_cases:
                    old.state = CaseState.SUPERSEDED
                    old.superseded_by_case_id = new_case_id
                    old.cancel_requested = True
                    old.lease_owner = None
                    old.lease_expires_at = None
                    old.version += 1
                    old.updated_at = now
                    running_attempts = (await session.scalars(select(ExecutionAttempt).where(
                        ExecutionAttempt.case_id == old.case_id,
                        ExecutionAttempt.state == "RUNNING",
                    ))).all()
                    for attempt in running_attempts:
                        attempt.state = "STALE"
                        attempt.finished_at = now
                        attempt.error_code = "CASE_SUPERSEDED"
                        attempt.error_detail = "case was superseded by a newer quantity binding"
                    lease = await session.get(WorkerLease, old.case_id)
                    if lease:
                        await session.delete(lease)
                    knowledge_entries = (await session.scalars(select(KnowledgeEntry).where(
                        KnowledgeEntry.case_id == old.case_id,
                        KnowledgeEntry.invalidated.is_(False),
                    ))).all()
                    for entry in knowledge_entries:
                        entry.invalidated = True
                        entry.version += 1

                event_record = RejectionEvent(event_id=event.event_id, run_id=run.run_id,
                                              payload_hash=event_hash, payload=payload, created_at=now)
                session.add(event_record)
                overrides[key] = {
                    "shop_code": event.shop_code, "goods_code": event.goods_code,
                    "decision_date": event.decision_date.isoformat(),
                    "recommended_qty": event.recommended_qty, "override_qty": event.override_qty,
                    "snapshot_hash": event.snapshot_hash,
                }
                if event.override_qty == event.recommended_qty:
                    event_record.case_id = None
                    continue
                if event.case_version is not None and event.case_version != next_case_version:
                    raise ConflictError("case_version is not the next version",
                                        details={"expected": next_case_version})
                case = AttributionCase(
                    case_id=new_case_id, job_id=job.job_id, run_id=run.run_id,
                    event_id=event.event_id, source_trace_id=event.source_trace_id,
                    shop_code=event.shop_code, goods_code=event.goods_code,
                    decision_date=event.decision_date.isoformat(), case_version=next_case_version,
                    state=CaseState.QUEUED, version=1, recommended_qty=event.recommended_qty,
                    override_qty=event.override_qty, snapshot_hash=event.snapshot_hash,
                    snapshot=event.recommendation_snapshot, reason_code=event.reason_code,
                    reason_text=event.reason_text, output_language=event.output_language,
                    partial=False, created_at=now, updated_at=now,
                )
                session.add(case)
                created_cases.append(case)
                event_record.case_id = case.case_id
                case_ids.append(case.case_id)

            run.draft_overrides = overrides
            run.version += 1
            run.updated_at = now
            run.state = RunState.ATTRIBUTION_RUNNING if case_ids else RunState.DRAFT
            readiness = await self._readiness_in_session(session, run)
            return {
                "run_id": run.run_id, "changed": bool(new_events),
                "total_qty": sum(int(item["override_qty"]) for item in overrides.values()),
                "results": [self._case_dict(case) for case in created_cases],
                "job_id": job.job_id, "case_ids": duplicate_case_ids + case_ids,
                "duplicate_event_ids": duplicate_event_ids,
                "gate_status": readiness.status.value, "run_version": run.version,
                "run_state": run.state, "readiness": readiness.model_dump(mode="json"),
            }

    async def submission_readiness(self, run_id: str) -> SubmissionReadiness:
        async with self.sessions() as session, session.begin():
            run = await session.scalar(select(ReplenishmentRun).where(
                ReplenishmentRun.run_id == run_id).with_for_update())
            if not run:
                raise NotFoundError("run not found", details={"run_id": run_id})
            return await self._readiness_in_session(session, run)

    async def submit_and_lock(
        self, run_id: str, expected_run_version: int, adapter: DownstreamSubmissionAdapter,
        *, submitted_by: str = "system",
    ) -> dict[str, Any]:
        async with self.sessions() as session, session.begin():
            run = await session.scalar(select(ReplenishmentRun).where(
                ReplenishmentRun.run_id == run_id).with_for_update())
            if not run:
                raise NotFoundError("run not found")
            if run.version != expected_run_version:
                raise ConflictError("run version is stale", details={"current_version": run.version})
            if run.state == RunState.SUBMITTED_LOCKED:
                raise StateTransitionError("run is already locked")
            readiness = await self._readiness_in_session(session, run)
            if not readiness.ready:
                raise GateBlockedError("not every modified line has a matching human approval",
                                       details={"blockers": readiness.blockers})
            accepted = {key: value["override_qty"] for key, value in (run.draft_overrides or {}).items()}
            downstream_result = await adapter.submit(run.run_id, accepted)
            now = _now()
            outcomes_tracked = await self._record_run_outcomes(session, run, now)
            run.accepted_overrides = accepted
            run.state = RunState.SUBMITTED_LOCKED
            run.version += 1
            run.updated_at = now
            run.submitted_at = now
            run.submitted_by = submitted_by
            session.add(SubmissionAudit(audit_id=_id(), run_id=run.run_id, status="SUBMITTED",
                                        payload={"accepted_overrides": accepted,
                                                 "downstream_result": downstream_result,
                                                 "submitted_by": submitted_by}, created_at=now))
            return {"run_id": run_id, "status": run.state, "state": run.state,
                    "accepted_overrides": accepted,
                    "outcomes_tracked": outcomes_tracked,
                    "downstream_result": downstream_result, "run_version": run.version,
                    "submitted_at": now, "submitted_by": submitted_by}

    # ---- Ground truth: what the store actually needed ----

    async def _record_run_outcomes(
        self, session: AsyncSession, run: ReplenishmentRun, now: datetime,
    ) -> int:
        """Open a judgement window for every decided line at submission time.

        Both overridden and accepted lines are tracked. Sampling only the
        overrides would mean the loop learns exclusively from disagreement and
        never discovers that a recommendation the store manager accepted was
        itself wrong.
        """
        overrides = run.draft_overrides or {}
        results = (run.payload or {}).get("results") or []
        fallback_date = str((run.payload or {}).get("ts") or "")[:10]
        tracked = 0
        for result in results:
            if not isinstance(result, dict):
                continue
            shop_code = str(result.get("shop") or result.get("shop_code") or "")
            goods_code = str(result.get("sku") or result.get("goods_code") or "")
            decision_date = str(result.get("apply_date") or fallback_date)[:10]
            if not shop_code or not goods_code or len(decision_date) != 10:
                continue
            key = _line_key(shop_code, goods_code, decision_date)
            override = overrides.get(key)
            recommended = int(result.get("chosen_qty", result.get("final_qty", 0)) or 0)
            if override:
                recommended = int(override.get("recommended_qty", recommended) or 0)
                ordered = int(override.get("override_qty", recommended) or 0)
            else:
                ordered = recommended
            source = "OVERRIDE" if override and ordered != recommended else "ACCEPTED"
            params = result.get("params") if isinstance(result.get("params"), dict) else {}
            existing = await session.scalar(select(DecisionOutcome).where(
                DecisionOutcome.shop_code == shop_code,
                DecisionOutcome.goods_code == goods_code,
                DecisionOutcome.decision_date == decision_date).with_for_update())
            horizon = outcome_math.horizon_days_from_snapshot(result)
            window_start, window_end = outcome_math.demand_window(decision_date, horizon)
            if existing:
                # A resubmitted line supersedes the earlier binding; keep the row
                # so its identity is stable, but re-open the window against the
                # quantity that was actually sent downstream.
                existing.recommended_qty = recommended
                existing.ordered_qty = ordered
                existing.source = source
                existing.run_id = run.run_id
                existing.updated_at = now
                tracked += 1
                continue
            session.add(DecisionOutcome(
                outcome_id=_id(), run_id=run.run_id, case_id=None,
                shop_code=shop_code, goods_code=goods_code, decision_date=decision_date,
                source=source, recommended_qty=recommended, ordered_qty=ordered,
                opening_position=float(result.get("position") or 0),
                case_pack=int(params.get("case_pack") or 1), horizon_days=horizon,
                window_start=window_start, window_end=window_end,
                status="PENDING", observed_days=0, verdict="PENDING", detail={},
                snapshot_hash=(override or {}).get("snapshot_hash"),
                created_at=now, updated_at=now,
            ))
            tracked += 1
        return tracked

    async def ingest_daily_sales(self, request: OutcomeIngestRequest) -> dict[str, Any]:
        """Upsert daily sales, then rescore every outcome whose window they touch.

        Idempotent per (shop, goods, date) so a feed that replays a day cannot
        double-count demand.
        """
        now = _now()
        touched: set[tuple[str, str]] = set()
        async with self.sessions() as session, session.begin():
            for record in request.records:
                sales_date = record.sales_date.isoformat()
                existing = await session.scalar(select(DailySalesFact).where(
                    DailySalesFact.shop_code == record.shop_code,
                    DailySalesFact.goods_code == record.goods_code,
                    DailySalesFact.sales_date == sales_date).with_for_update())
                if existing:
                    existing.units_sold = float(record.units_sold)
                    existing.lost_sales_units = float(record.lost_sales_units or 0)
                    existing.stockout = bool(record.stockout)
                    existing.source = request.source
                    existing.ingested_at = now
                else:
                    session.add(DailySalesFact(
                        fact_id=_id(), shop_code=record.shop_code,
                        goods_code=record.goods_code, sales_date=sales_date,
                        units_sold=float(record.units_sold),
                        lost_sales_units=float(record.lost_sales_units or 0),
                        stockout=bool(record.stockout), source=request.source,
                        ingested_at=now))
                touched.add((record.shop_code, record.goods_code))

        recomputed = completed = 0
        for shop_code, goods_code in sorted(touched):
            stats = await self._rescore_line(shop_code, goods_code)
            recomputed += stats["recomputed"]
            completed += stats["completed"]
        return {"ingested": len(request.records), "outcomes_recomputed": recomputed,
                "outcomes_completed": completed}

    async def _rescore_line(self, shop_code: str, goods_code: str) -> dict[str, int]:
        """Re-run the outcome maths for one SKU in one store against all its facts."""
        now = _now()
        recomputed = completed = 0
        async with self.sessions() as session, session.begin():
            facts = (await session.scalars(select(DailySalesFact).where(
                DailySalesFact.shop_code == shop_code,
                DailySalesFact.goods_code == goods_code))).all()
            daily_units = {fact.sales_date: float(fact.units_sold) for fact in facts}
            lost_sales = {fact.sales_date: float(fact.lost_sales_units or 0) for fact in facts}
            pending = (await session.scalars(select(DecisionOutcome).where(
                DecisionOutcome.shop_code == shop_code,
                DecisionOutcome.goods_code == goods_code,
                DecisionOutcome.status != "COMPLETE").with_for_update())).all()
            for outcome in pending:
                demand = outcome_math.realised_demand(
                    daily_units, outcome.window_start, outcome.window_end,
                    lost_sales=lost_sales)
                scored = outcome_math.score_outcome(
                    outcome_math.OutcomeInputs(
                        recommended_qty=outcome.recommended_qty,
                        ordered_qty=outcome.ordered_qty,
                        opening_position=float(outcome.opening_position or 0),
                        horizon_days=outcome.horizon_days,
                        case_pack=outcome.case_pack,
                    ), demand)
                if scored["status"] == "PENDING" and outcome.status == "PENDING":
                    continue
                outcome.status = scored["status"]
                outcome.observed_days = scored["observed_days"]
                outcome.units_sold = scored["units_sold"]
                outcome.lost_sales_units = scored["lost_sales_units"]
                outcome.actual_demand = scored["actual_demand"]
                outcome.ideal_qty = scored["ideal_qty"]
                outcome.engine_error = scored["engine_error"]
                outcome.human_error = scored["human_error"]
                outcome.stockout_units = scored["stockout_units"]
                outcome.overstock_units = scored["overstock_units"]
                outcome.verdict = scored["verdict"]
                outcome.detail = scored
                outcome.updated_at = now
                recomputed += 1
                if scored["status"] == "COMPLETE":
                    completed += 1
                    await self._settle_knowledge_for_outcome(session, outcome)
        return {"recomputed": recomputed, "completed": completed}

    async def _settle_knowledge_for_outcome(
        self, session: AsyncSession, outcome: DecisionOutcome,
    ) -> None:
        """Move the posterior of every knowledge entry covering this decision.

        A completed outcome is the only event that changes confidence, so
        promotion and retirement both happen here rather than on a reviewer's
        judgement.
        """
        entries = (await session.scalars(select(KnowledgeEntry).where(
            KnowledgeEntry.invalidated.is_(False),
            KnowledgeEntry.status != "RETIRED").with_for_update())).all()
        for entry in entries:
            scope = {
                "shop_code": entry.scope_shop_code, "goods_code": entry.scope_goods_code,
                "category": entry.scope_category,
                "applies_from": entry.applies_from, "applies_to": entry.applies_to,
            }
            if not knowledge_math.scope_matches(
                    scope, shop_code=outcome.shop_code, goods_code=outcome.goods_code,
                    on_date=outcome.decision_date):
                continue
            # The entry argued the engine's baseline was wrong for this line, so
            # it is credited whenever the human quantity proved closer and
            # debited when the engine's did. Ties leave the posterior untouched.
            if outcome.verdict == "TIE":
                continue
            updated = knowledge_math.apply_outcome(
                {"posterior": entry.posterior or {}, "status": entry.status,
                 "invalidated": entry.invalidated},
                improved=outcome.verdict == "HUMAN_BETTER")
            entry.posterior = updated["posterior"]
            entry.effective_weight = updated["effective_weight"]
            entry.status = updated["status"]
            entry.version += 1
            entry.updated_at = _now()

    async def list_outcomes(
        self, *, shop_code: str | None = None, goods_code: str | None = None,
        status: str | None = None, verdict: str | None = None,
        date_from: str | None = None, date_to: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> dict[str, Any]:
        page_size = min(max(limit, 1), 200)
        async with self.sessions() as session:
            filters = []
            if shop_code:
                filters.append(DecisionOutcome.shop_code == shop_code)
            if goods_code:
                filters.append(DecisionOutcome.goods_code == goods_code)
            if status:
                filters.append(DecisionOutcome.status == status)
            if verdict:
                filters.append(DecisionOutcome.verdict == verdict)
            if date_from:
                filters.append(DecisionOutcome.decision_date >= date_from)
            if date_to:
                filters.append(DecisionOutcome.decision_date <= date_to)
            query = select(DecisionOutcome).order_by(
                DecisionOutcome.decision_date.desc()).limit(page_size).offset(offset)
            total_query = select(func.count()).select_from(DecisionOutcome)
            for condition in filters:
                query = query.where(condition)
                total_query = total_query.where(condition)
            rows = (await session.scalars(query)).all()
            return {
                "items": [self._outcome_dict(row) for row in rows],
                "total": int(await session.scalar(total_query) or 0),
                "page": offset // page_size + 1, "page_size": page_size,
            }

    async def outcome_accuracy_summary(
        self, *, shop_code: str | None = None, goods_code: str | None = None,
        date_from: str | None = None, date_to: str | None = None,
    ) -> dict[str, Any]:
        async with self.sessions() as session:
            query = select(DecisionOutcome)
            if shop_code:
                query = query.where(DecisionOutcome.shop_code == shop_code)
            if goods_code:
                query = query.where(DecisionOutcome.goods_code == goods_code)
            if date_from:
                query = query.where(DecisionOutcome.decision_date >= date_from)
            if date_to:
                query = query.where(DecisionOutcome.decision_date <= date_to)
            rows = (await session.scalars(query)).all()
        summary = outcome_math.accuracy_summary([self._outcome_dict(row) for row in rows])
        return {
            "scored_count": summary["sample_size"],
            "pending_count": summary["pending_count"],
            "engine_mae": summary["engine_mae"], "human_mae": summary["human_mae"],
            "engine_mape": summary["engine_mape"], "human_mape": summary["human_mape"],
            "human_win_rate": summary["human_win_rate"],
            "engine_win_rate": summary["engine_win_rate"],
            "tie_rate": summary["tie_rate"],
            "accuracy_gain_units": summary["accuracy_gain_units"],
            "stockout_units": summary["stockout_units"],
            "overstock_units": summary["overstock_units"],
        }

    @staticmethod
    def _outcome_dict(row: DecisionOutcome) -> dict[str, Any]:
        return {
            "outcome_id": row.outcome_id, "run_id": row.run_id, "case_id": row.case_id,
            "shop_code": row.shop_code, "goods_code": row.goods_code,
            "decision_date": row.decision_date, "source": row.source,
            "recommended_qty": row.recommended_qty, "ordered_qty": row.ordered_qty,
            "horizon_days": row.horizon_days, "window_start": row.window_start,
            "window_end": row.window_end, "status": row.status,
            "observed_days": row.observed_days, "actual_demand": row.actual_demand,
            "ideal_qty": row.ideal_qty, "engine_error": row.engine_error,
            "human_error": row.human_error, "stockout_units": row.stockout_units,
            "overstock_units": row.overstock_units, "verdict": row.verdict,
            "updated_at": row.updated_at,
        }

    async def list_cases(
        self, *, state: CaseState | None = None, shop_code: str | None = None,
        goods_code: str | None = None, direction: str | None = None,
        job_id: str | None = None, date_from: str | None = None, date_to: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> dict:
        async with self.sessions() as session:
            query = select(AttributionCase).order_by(AttributionCase.created_at.desc()).limit(min(limit, 100)).offset(offset)
            if state: query = query.where(AttributionCase.state == state)
            if shop_code: query = query.where(AttributionCase.shop_code == shop_code)
            if goods_code: query = query.where(AttributionCase.goods_code == goods_code)
            if direction == "UP": query = query.where(AttributionCase.override_qty > AttributionCase.recommended_qty)
            if direction == "DOWN": query = query.where(AttributionCase.override_qty < AttributionCase.recommended_qty)
            if job_id: query = query.where(AttributionCase.job_id == job_id)
            if date_from: query = query.where(AttributionCase.decision_date >= date_from)
            if date_to: query = query.where(AttributionCase.decision_date <= date_to)
            cases = (await session.scalars(query)).all()
            total_query = select(func.count()).select_from(AttributionCase)
            if state: total_query = total_query.where(AttributionCase.state == state)
            if shop_code: total_query = total_query.where(AttributionCase.shop_code == shop_code)
            if goods_code: total_query = total_query.where(AttributionCase.goods_code == goods_code)
            if direction == "UP": total_query = total_query.where(AttributionCase.override_qty > AttributionCase.recommended_qty)
            if direction == "DOWN": total_query = total_query.where(AttributionCase.override_qty < AttributionCase.recommended_qty)
            if job_id: total_query = total_query.where(AttributionCase.job_id == job_id)
            if date_from: total_query = total_query.where(AttributionCase.decision_date >= date_from)
            if date_to: total_query = total_query.where(AttributionCase.decision_date <= date_to)
            return {
                "items": [await self._case_summary(session, case) for case in cases],
                "total": int(await session.scalar(total_query) or 0),
                "page": offset // max(limit, 1) + 1,
                "page_size": min(limit, 100),
            }

    async def get_case(self, case_id: str) -> dict:
        async with self.sessions() as session:
            case = await session.get(AttributionCase, case_id)
            if not case:
                raise NotFoundError("case not found", details={"case_id": case_id})
            reports = (await session.scalars(select(AttributionReport).where(
                AttributionReport.case_id == case_id).order_by(AttributionReport.version))).all()
            attempts = (await session.scalars(select(ExecutionAttempt).where(
                ExecutionAttempt.case_id == case_id).order_by(ExecutionAttempt.number))).all()
            reviews = (await session.scalars(select(HumanReview).where(
                HumanReview.case_id == case_id).order_by(HumanReview.created_at))).all()
            trace_events = (await session.scalars(select(TraceEvent).where(
                TraceEvent.case_id == case_id).order_by(TraceEvent.created_at))).all()
            events_by_trace: dict[str, list[TraceEvent]] = {}
            for trace in trace_events:
                events_by_trace.setdefault(trace.trace_id, []).append(trace)
            latest = reports[-1] if reports else None
            return await self._case_summary(session, case, latest) | {
                "source_trace_id": case.source_trace_id, "reason_code": case.reason_code,
                "reason_text": case.reason_text, "snapshot_hash": case.snapshot_hash,
                "latest_report": self._report_dict(latest) if latest else None,
                "reports": [self._report_dict(report) for report in reports],
                "attempts": [{"attempt_id": attempt.attempt_id, "attempt_number": attempt.number,
                              "status": attempt.state, "error_code": attempt.error_code,
                              "error_message": attempt.error_detail, "started_at": attempt.started_at,
                              "finished_at": attempt.finished_at,
                              "model_calls": sum(
                                  event.event_type == "MODEL_CALL_STARTED"
                                  for event in events_by_trace.get(
                                      f"attr-{case_id}-attempt-{attempt.number}", [])),
                              "tool_calls": sum(
                                  event.event_type == "TOOL_CALL_STARTED"
                                  for event in events_by_trace.get(
                                      f"attr-{case_id}-attempt-{attempt.number}", [])),
                              "raw_log_available": bool(events_by_trace.get(
                                  f"attr-{case_id}-attempt-{attempt.number}")),
                              "duration_ms": (
                                  int((attempt.finished_at - attempt.started_at).total_seconds() * 1000)
                                  if attempt.finished_at else None)}
                             for attempt in attempts],
                "reviews": [{"review_id": review.review_id, "report_id": review.report_id,
                             "version": index + 1, "reviewer": review.reviewer_subject,
                             "action": review.action, "comment": review.notes,
                             "report_version": next((
                                 item.version for item in reports
                                 if item.report_id == review.report_id), 0),
                             "publish_knowledge": False, "created_at": review.created_at}
                            for index, review in enumerate(reviews)],
                "trace_events": [{"trace_event_id": trace.trace_event_id,
                                  "trace_id": trace.trace_id, "event_type": trace.event_type,
                                  "name": trace.event_type.replace("_", " ").title(),
                                  "payload": trace.payload, "created_at": trace.created_at}
                                 for trace in trace_events],
                "error_code": case.error_code, "error_message": case.error_message,
                "superseded_by_case_id": case.superseded_by_case_id,
            }

    async def get_job(self, job_id: str) -> dict:
        async with self.sessions() as session:
            job = await session.get(AttributionJob, job_id)
            if not job:
                raise NotFoundError("job not found")
            cases = (await session.scalars(select(AttributionCase).where(
                AttributionCase.job_id == job_id))).all()
            case_ids = [case.case_id for case in cases]
            completed = sum(case.state not in {CaseState.QUEUED, CaseState.RUNNING} for case in cases)
            if not cases:
                status = "COMPLETED"
            elif completed == len(cases):
                status = "COMPLETED"
            elif any(case.state == CaseState.RUNNING for case in cases):
                status = "RUNNING"
            else:
                status = "QUEUED"
            return {
                "job_id": job.job_id, "run_id": job.run_id, "status": status,
                "case_ids": case_ids, "total_cases": len(cases), "completed_cases": completed,
                "created_at": job.created_at, "updated_at": max(
                    [job.updated_at, *(case.updated_at for case in cases)]),
            }

    async def request_review(
        self, case_id: str, review: ReviewRequest,
        *, publish_knowledge: KnowledgePublishRequest | None = None,
    ) -> dict:
        now = _now()
        if (publish_knowledge and publish_knowledge.expires_at is not None
                and _as_aware(publish_knowledge.expires_at) <= now):
            raise ValidationError("knowledge expiry must be in the future")
        if publish_knowledge and review.action == "REQUEST_CHANGES":
            raise ValidationError("changes-requested reviews cannot publish knowledge")
        if review.knowledge_decisions and review.action == "REQUEST_CHANGES":
            # Sending a case back means the reviewer has not accepted its findings,
            # so there is nothing yet to accept or reject about them.
            raise ValidationError("changes-requested reviews cannot record knowledge decisions")
        for decision in review.knowledge_decisions or []:
            if (decision.decision != "REJECT" and decision.expires_at is not None
                    and _as_aware(decision.expires_at) <= now):
                raise ValidationError("knowledge expiry must be in the future")
        async with self.sessions() as session, session.begin():
            case = await self._mutable_case_for_update(session, case_id)
            if case.version != review.expected_version:
                raise ConflictError("case version is stale", details={"current_version": case.version})
            report = await self._latest_report(session, case_id)
            action = review.action
            if action in {"AMEND_AND_APPROVE", "MANUAL_AND_APPROVE"}:
                if not review.contributions or not review.summary:
                    raise ValidationError("amend-and-approve requires contributions and summary")
                if action == "MANUAL_AND_APPROVE" and case.state != CaseState.FAILED:
                    raise StateTransitionError("manual-and-approve is only allowed after failure")
                if action == "AMEND_AND_APPROVE" and case.state not in {
                    CaseState.NEEDS_REVIEW, CaseState.CHANGES_REQUESTED,
                }:
                    raise StateTransitionError("case is not available for amendment")
                if action == "AMEND_AND_APPROVE" and (
                    not report or report.version != review.expected_report_version
                ):
                    raise ConflictError("report version is stale")
                contribution_map = {item.cause_code: item.signed_contribution_qty
                                    for item in review.contributions}
                if len(contribution_map) != len(review.contributions):
                    raise ValidationError("manual cause codes must be unique")
                report = AttributionReport(
                    report_id=_id(), case_id=case_id, version=(report.version if report else 0) + 1,
                    report={"summary": review.summary,
                            "contributions": [item.model_dump() for item in review.contributions],
                            **conserve(case.override_qty - case.recommended_qty, contribution_map),
                            "report_version": "manual-review-v1"},
                    partial=False, source="MANUAL", created_at=now,
                )
                session.add(report)
                case.partial = False
                action = "APPROVE"
            elif not report or report.version != review.expected_report_version:
                raise ConflictError("report version is stale")
            if action == "APPROVE":
                if case.state not in {CaseState.NEEDS_REVIEW, CaseState.CHANGES_REQUESTED}:
                    if review.action not in {"AMEND_AND_APPROVE", "MANUAL_AND_APPROVE"}:
                        raise StateTransitionError("case is not ready for approval")
                if review.action == "MANUAL_AND_APPROVE":
                    case.state = CaseState.NEEDS_REVIEW
                if case.state not in {CaseState.NEEDS_REVIEW, CaseState.CHANGES_REQUESTED}:
                    raise StateTransitionError("case is not ready for approval")
                if case.partial or report.partial:
                    raise ValidationError("partial report must be manually amended before approval")
                self._validate_report_conservation(case, report.report)
                case.state = CaseState.HUMAN_APPROVED
                case.approved_report_id = report.report_id
            else:
                if case.state not in {CaseState.NEEDS_REVIEW, CaseState.HUMAN_APPROVED}:
                    raise StateTransitionError("case cannot request changes")
                case.state = CaseState.CHANGES_REQUESTED
                case.approved_report_id = None
                knowledge_entries = (await session.scalars(select(KnowledgeEntry).where(
                    KnowledgeEntry.case_id == case_id,
                    KnowledgeEntry.invalidated.is_(False),
                ))).all()
                for entry in knowledge_entries:
                    entry.invalidated = True
                    entry.version += 1
            case.version += 1
            case.updated_at = now
            review_id = _id()
            session.add(HumanReview(review_id=review_id, case_id=case_id,
                                    report_id=report.report_id,
                                    reviewer_subject=review.reviewer_subject, action=review.action,
                                    notes=review.notes, created_at=now))
            if review.knowledge_decisions:
                self._apply_knowledge_decisions(
                    session, case=case, report=report, review=review,
                    review_id=review_id, now=now)
            if publish_knowledge:
                if case.state != CaseState.HUMAN_APPROVED:
                    raise StateTransitionError("only an approved report may be published as knowledge")
                prior_value, proposed_value = self._knowledge_values(case, publish_knowledge)
                if prior_value == proposed_value:
                    raise ValidationError(
                        "knowledge must propose a value different from the prior")
                session.add(KnowledgeEntry(
                    knowledge_id=_id(), case_id=case_id, report_id=report.report_id,
                    version=1,
                    scope={"shop_code": publish_knowledge.scope_shop_code,
                           "goods_code": publish_knowledge.scope_goods_code,
                           "category": publish_knowledge.scope_category},
                    scope_shop_code=publish_knowledge.scope_shop_code,
                    scope_goods_code=publish_knowledge.scope_goods_code,
                    scope_category=publish_knowledge.scope_category,
                    applies_from=(publish_knowledge.applies_from.isoformat()
                                  if publish_knowledge.applies_from else None),
                    applies_to=(publish_knowledge.applies_to.isoformat()
                                if publish_knowledge.applies_to else None),
                    kind=publish_knowledge.kind.value,
                    prior_value=prior_value, proposed_value=proposed_value,
                    status="CANDIDATE", effective_weight=0.0,
                    evidence=publish_knowledge.evidence,
                    posterior=knowledge_math.posterior(0, 0),
                    expires_at=publish_knowledge.expires_at, invalidated=False,
                    created_at=now, updated_at=now,
                ))
            await self._refresh_run_state(session, case.run_id)
            return self._case_dict(case)

    @staticmethod
    def _apply_knowledge_decisions(
        session: AsyncSession, *, case: AttributionCase, report: AttributionReport,
        review: ReviewRequest, review_id: str, now: datetime,
    ) -> None:
        """Record the reviewer's verdict on every candidate the agent proposed.

        Accepted and amended candidates become inert knowledge entries; rejected
        ones become rejection rows. Both halves are written from the same loop so
        a reviewer cannot end up having judged a candidate that left no trace,
        which is what made the previous single publish checkbox unable to teach
        anything: agreement produced a bare number and disagreement produced
        nothing at all.
        """
        if case.state != CaseState.HUMAN_APPROVED:
            raise StateTransitionError(
                "knowledge decisions are only recorded on an approved report")
        candidates = {
            str(item.get("candidate_id")): item
            for item in (report.report or {}).get("knowledge_candidates", [])
            if isinstance(item, dict)
        }
        for decision in review.knowledge_decisions or []:
            candidate = candidates.get(decision.candidate_id)
            if candidate is None:
                raise ValidationError(
                    "knowledge decision does not match any candidate in the report",
                    details={"candidate_id": decision.candidate_id})
            if decision.decision == "REJECT":
                session.add(KnowledgeRejection(
                    rejection_id=_id(), case_id=case.case_id, report_id=report.report_id,
                    review_id=review_id, candidate_id=decision.candidate_id,
                    cause_code=decision.cause_code or candidate.get("cause_code"),
                    kind=(decision.kind.value if decision.kind else candidate.get("kind")),
                    domain=decision.domain or candidate.get("domain"),
                    scope_shop_code=decision.scope_shop_code,
                    scope_goods_code=decision.scope_goods_code,
                    scope_category=decision.scope_category,
                    prior_value=candidate.get("prior_value"),
                    proposed_value=candidate.get("proposed_value"),
                    reason_code=decision.reject_reason.value,
                    note=decision.note, candidate=candidate,
                    reviewer_subject=review.reviewer_subject, created_at=now))
                continue
            # A candidate the engine already showed cannot reach the ordered
            # quantity has no value to publish, whatever the reviewer clicked.
            if not candidate.get("acceptable", True):
                raise ValidationError(
                    "this candidate could not be calibrated and cannot be accepted",
                    details={"candidate_id": decision.candidate_id})
            prior_value = (decision.prior_value if decision.prior_value is not None
                           else candidate.get("prior_value"))
            if prior_value is None or float(prior_value) == float(decision.proposed_value):
                raise ValidationError(
                    "knowledge must propose a value different from the prior",
                    details={"candidate_id": decision.candidate_id})
            session.add(KnowledgeEntry(
                knowledge_id=_id(), case_id=case.case_id, report_id=report.report_id,
                version=1,
                scope={"shop_code": decision.scope_shop_code,
                       "goods_code": decision.scope_goods_code,
                       "category": decision.scope_category,
                       "scope_label": decision.scope_label or candidate.get("scope_label")},
                scope_shop_code=decision.scope_shop_code,
                scope_goods_code=decision.scope_goods_code,
                scope_category=decision.scope_category,
                applies_from=(decision.applies_from.isoformat()
                              if decision.applies_from else candidate.get("applies_from")),
                applies_to=(decision.applies_to.isoformat()
                            if decision.applies_to else candidate.get("applies_to")),
                kind=decision.kind.value,
                prior_value=float(prior_value),
                proposed_value=float(decision.proposed_value),
                # Inert on creation. Approval says the proposition is credible;
                # only completed outcomes say it is correct.
                status="CANDIDATE", effective_weight=0.0,
                evidence={
                    **decision.evidence,
                    "candidate": candidate,
                    "decision": decision.decision,
                    "condition": decision.condition or candidate.get("condition"),
                    "reviewer": review.reviewer_subject,
                    "review_id": review_id,
                    "note": decision.note,
                },
                posterior=knowledge_math.posterior(0, 0),
                expires_at=decision.expires_at, invalidated=False,
                created_at=now, updated_at=now))

    async def create_manual_report(self, case_id: str, request: ManualReportRequest) -> dict:
        now = _now()
        async with self.sessions() as session, session.begin():
            case = await self._mutable_case_for_update(session, case_id)
            if case.version != request.expected_case_version:
                raise ConflictError("case version is stale", details={"current_version": case.version})
            if case.state not in {CaseState.FAILED, CaseState.NEEDS_REVIEW, CaseState.CHANGES_REQUESTED}:
                raise StateTransitionError("manual report is allowed after failure or during review")
            contributions = {item.cause_code: item.signed_contribution_qty for item in request.contributions}
            if len(contributions) != len(request.contributions):
                raise ValidationError("manual cause codes must be unique")
            report_payload = {
                "summary": request.summary,
                "contributions": [item.model_dump() for item in request.contributions],
                **conserve(case.override_qty - case.recommended_qty, contributions),
                "report_version": "manual-v1",
            }
            latest = await self._latest_report(session, case_id)
            report = AttributionReport(report_id=_id(), case_id=case_id,
                version=(latest.version if latest else 0) + 1, report=report_payload,
                partial=False, source="MANUAL", created_at=now)
            session.add(report)
            case.state = CaseState.NEEDS_REVIEW
            case.partial = False
            case.version += 1
            case.updated_at = now
            await self._refresh_run_state(session, case.run_id)
            return self._report_dict(report)

    async def retry_case(self, case_id: str, expected_case_version: int,
                         output_language: str | None = None) -> dict:
        async with self.sessions() as session, session.begin():
            case = await self._mutable_case_for_update(session, case_id)
            if case.version != expected_case_version:
                raise ConflictError("case version is stale")
            if case.state not in {CaseState.FAILED, CaseState.CANCELLED}:
                raise StateTransitionError("only failed or cancelled cases can retry")
            case.state, case.cancel_requested, case.lease_owner, case.lease_expires_at = (
                CaseState.QUEUED, False, None, None)
            if output_language is not None:
                if output_language not in {"zh-CN", "en-US"}:
                    raise ValidationError("unsupported attribution output language")
                case.output_language = output_language
            case.version += 1
            case.updated_at = _now()
            await self._refresh_run_state(session, case.run_id)
            return self._case_dict(case)

    async def cancel_case(self, case_id: str, expected_case_version: int) -> dict:
        async with self.sessions() as session, session.begin():
            case = await self._mutable_case_for_update(session, case_id)
            if case.version != expected_case_version:
                raise ConflictError("case version is stale")
            if case.state in {CaseState.HUMAN_APPROVED, CaseState.SUPERSEDED}:
                raise StateTransitionError("case cannot be cancelled")
            case.cancel_requested = True
            case.state = CaseState.CANCELLED
            case.lease_owner = None
            case.lease_expires_at = None
            running_attempts = (await session.scalars(select(ExecutionAttempt).where(
                ExecutionAttempt.case_id == case_id,
                ExecutionAttempt.state == "RUNNING",
            ))).all()
            for attempt in running_attempts:
                attempt.state = "STALE"
                attempt.finished_at = _now()
                attempt.error_code = "CASE_CANCELLED"
                attempt.error_detail = "case cancellation was requested"
            lease = await session.get(WorkerLease, case_id)
            if lease:
                await session.delete(lease)
            case.version += 1
            case.updated_at = _now()
            await self._refresh_run_state(session, case.run_id)
            return self._case_dict(case)

    async def publish_knowledge(self, case_id: str, report_id: str, request: KnowledgePublishRequest) -> dict:
        now = _now()
        if request.expires_at is not None and _as_aware(request.expires_at) <= now:
            raise ValidationError("knowledge expiry must be in the future")
        async with self.sessions() as session, session.begin():
            case = await self._case_for_update(session, case_id)
            if case.state != CaseState.HUMAN_APPROVED or case.approved_report_id != report_id:
                raise StateTransitionError("only an approved report may be published as knowledge")
            prior_value, proposed_value = self._knowledge_values(case, request)
            if prior_value == proposed_value:
                raise ValidationError("knowledge must propose a value different from the prior")
            entry = KnowledgeEntry(
                knowledge_id=_id(), case_id=case_id, report_id=report_id, version=1,
                scope={"shop_code": request.scope_shop_code,
                       "goods_code": request.scope_goods_code,
                       "category": request.scope_category},
                scope_shop_code=request.scope_shop_code,
                scope_goods_code=request.scope_goods_code,
                scope_category=request.scope_category,
                applies_from=request.applies_from.isoformat() if request.applies_from else None,
                applies_to=request.applies_to.isoformat() if request.applies_to else None,
                kind=request.kind.value, prior_value=prior_value,
                proposed_value=proposed_value,
                # A new entry always starts inert: it earns engine weight from
                # completed outcomes, never from the approval that created it.
                status="CANDIDATE", effective_weight=0.0,
                evidence=request.evidence, posterior=knowledge_math.posterior(0, 0),
                expires_at=request.expires_at, invalidated=False,
                created_at=now, updated_at=now)
            session.add(entry)
            return self._knowledge_dict(entry)

    @staticmethod
    def _knowledge_values(
        case: AttributionCase, request: KnowledgePublishRequest,
    ) -> tuple[float, float]:
        """Fall back to the approved quantity gap when no value was supplied.

        A reviewer approving a case has already stated the correction in the only
        units the engine can act on: the difference between the recommendation
        and the quantity actually ordered, spread across the horizon it covers.
        Deriving it here keeps every entry replayable, which is what the old
        pointer-only knowledge row could never be.
        """
        if request.prior_value is not None and request.proposed_value is not None:
            return float(request.prior_value), float(request.proposed_value)
        horizon = outcome_math.horizon_days_from_snapshot(case.snapshot or {})
        gap = float(case.override_qty - case.recommended_qty)
        return 0.0, round(gap / max(horizon, 1), 4)

    async def list_knowledge(
        self, *, now: datetime | None = None, shop_code: str | None = None,
        goods_code: str | None = None, status: str | None = None,
        include_expired: bool = False,
    ) -> list[dict]:
        now = now or _now()
        async with self.sessions() as session:
            query = select(KnowledgeEntry).where(KnowledgeEntry.invalidated.is_(False))
            if not include_expired:
                # A null expiry means the entry stands until the evidence retires
                # it, which is the normal case now that confidence is measured.
                query = query.where((KnowledgeEntry.expires_at.is_(None)) |
                                    (KnowledgeEntry.expires_at > now))
            if shop_code:
                query = query.where(KnowledgeEntry.scope_shop_code == shop_code)
            if goods_code:
                query = query.where(KnowledgeEntry.scope_goods_code == goods_code)
            if status:
                query = query.where(KnowledgeEntry.status == status)
            entries = (await session.scalars(query)).all()
            return [self._knowledge_dict(entry) for entry in entries]

    async def list_knowledge_rejections(
        self, *, case_id: str | None = None, cause_code: str | None = None,
        reason_code: str | None = None, limit: int = 200,
    ) -> list[dict]:
        async with self.sessions() as session:
            query = select(KnowledgeRejection).order_by(
                KnowledgeRejection.created_at.desc()).limit(max(1, min(limit, 1000)))
            if case_id:
                query = query.where(KnowledgeRejection.case_id == case_id)
            if cause_code:
                query = query.where(KnowledgeRejection.cause_code == cause_code)
            if reason_code:
                query = query.where(KnowledgeRejection.reason_code == reason_code)
            return [self._rejection_dict(row) for row in (await session.scalars(query)).all()]

    async def knowledge_feedback_summary(self) -> dict[str, Any]:
        """Tally what reviewers accepted and rejected, per cause and per reason.

        This is the diagnostic agents' report card. A cause that is rejected
        nine times out of ten as WRONG_MAGNITUDE is telling its owner something
        specific and actionable, which is exactly the signal the old review --
        an action enum and an empty note -- threw away.
        """
        async with self.sessions() as session:
            accepted = (await session.execute(
                select(KnowledgeEntry.kind, func.count())
                .where(KnowledgeEntry.invalidated.is_(False))
                .group_by(KnowledgeEntry.kind))).all()
            rejected = (await session.execute(
                select(KnowledgeRejection.cause_code, KnowledgeRejection.reason_code,
                       func.count())
                .group_by(KnowledgeRejection.cause_code,
                          KnowledgeRejection.reason_code))).all()
            by_cause: dict[str, dict[str, Any]] = {}
            for cause_code, reason_code, count in rejected:
                bucket = by_cause.setdefault(
                    str(cause_code), {"rejected": 0, "reasons": {}})
                bucket["rejected"] += int(count)
                bucket["reasons"][str(reason_code)] = int(count)
            total_rejected = sum(item["rejected"] for item in by_cause.values())
            total_accepted = sum(int(count) for _, count in accepted)
            return {
                "accepted_total": total_accepted,
                "rejected_total": total_rejected,
                "acceptance_rate": (
                    round(total_accepted / (total_accepted + total_rejected), 4)
                    if total_accepted + total_rejected else None),
                "accepted_by_kind": {str(kind): int(count) for kind, count in accepted},
                "rejected_by_cause": by_cause,
            }

    async def claim_verdict_summary(
        self, *, date_from: datetime | None = None, date_to: datetime | None = None,
        shop_code: str | None = None,
    ) -> dict[str, Any]:
        """How often a stated reason survived contact with the evidence.

        Grouped over the denormalised verdict column rather than the report JSON:
        the point of the summary is to be cheap enough to look at routinely.
        Reports without a verdict -- manual attributions and anything written
        before the verdict existed -- are excluded rather than counted as
        agreement, so the rate never flatters itself with silence.
        """
        async with self.sessions() as session:
            query = (
                select(AttributionCase.reason_code, AttributionReport.claim_verdict,
                       func.count())
                .join(AttributionCase,
                      AttributionCase.case_id == AttributionReport.case_id)
                .where(AttributionReport.claim_verdict.is_not(None))
                .group_by(AttributionCase.reason_code, AttributionReport.claim_verdict)
            )
            if date_from is not None:
                query = query.where(AttributionReport.created_at >= _as_aware(date_from))
            if date_to is not None:
                query = query.where(AttributionReport.created_at <= _as_aware(date_to))
            if shop_code:
                query = query.where(AttributionCase.shop_code == shop_code)
            rows = (await session.execute(query)).all()

            by_reason: dict[str, dict[str, Any]] = {}
            totals: dict[str, int] = {}
            for reason_code, verdict, count in rows:
                bucket = by_reason.setdefault(
                    str(reason_code), {"total": 0, "verdicts": {}})
                bucket["total"] += int(count)
                bucket["verdicts"][str(verdict)] = (
                    bucket["verdicts"].get(str(verdict), 0) + int(count))
                totals[str(verdict)] = totals.get(str(verdict), 0) + int(count)
            for bucket in by_reason.values():
                # Out-of-scope claims are excluded from the denominator: the
                # registry, not the store manager, is what failed there, and
                # leaving them in would read as a store accuracy problem.
                judged = bucket["total"] - bucket["verdicts"].get("OUT_OF_SCOPE", 0)
                supported = bucket["verdicts"].get("SUPPORTED", 0)
                bucket["supported_rate"] = (
                    round(supported / judged, 4) if judged else None)
            graded = sum(totals.values()) - totals.get("OUT_OF_SCOPE", 0)
            return {
                "judged_total": sum(totals.values()),
                "by_verdict": totals,
                "by_reason_code": by_reason,
                "supported_rate": (
                    round(totals.get("SUPPORTED", 0) / graded, 4) if graded else None),
                # Claims the cause registry cannot express at all. A large number
                # here is a backlog item for attribution, not a store problem.
                "out_of_scope_total": totals.get("OUT_OF_SCOPE", 0),
            }

    @staticmethod
    def _rejection_dict(row: KnowledgeRejection) -> dict[str, Any]:
        return {
            "rejection_id": row.rejection_id, "case_id": row.case_id,
            "report_id": row.report_id, "candidate_id": row.candidate_id,
            "cause_code": row.cause_code, "kind": row.kind, "domain": row.domain,
            "scope_shop_code": row.scope_shop_code,
            "scope_goods_code": row.scope_goods_code,
            "scope_category": row.scope_category,
            "prior_value": row.prior_value, "proposed_value": row.proposed_value,
            "reason_code": row.reason_code, "note": row.note,
            "candidate": row.candidate or {},
            "reviewer_subject": row.reviewer_subject, "created_at": row.created_at,
        }

    async def active_knowledge_for(
        self, shop_code: str, goods_code: str, *, category: str | None = None,
        on_date: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Resolve the winning ACTIVE entry per kind for one store and SKU.

        The narrowest matching scope wins, so a SKU-level entry overrides a
        store-wide one instead of both applying in an order that depends on how
        the rows happen to be returned.
        """
        entries = await self.list_knowledge(shop_code=None, goods_code=None, status="ACTIVE")
        winners: dict[str, dict[str, Any]] = {}
        for entry in entries:
            scope = {
                "shop_code": entry["scope_shop_code"], "goods_code": entry["scope_goods_code"],
                "category": entry["scope_category"],
                "applies_from": entry["applies_from"], "applies_to": entry["applies_to"],
            }
            if not knowledge_math.scope_matches(
                    scope, shop_code=shop_code, goods_code=goods_code,
                    category=category, on_date=on_date):
                continue
            kind = entry["kind"]
            if not kind or entry["effective_weight"] <= 0:
                continue
            current = winners.get(kind)
            if current is None or knowledge_math.scope_specificity(scope) > current["_specificity"]:
                winners[kind] = {
                    **entry, "_specificity": knowledge_math.scope_specificity(scope),
                    "engine_target": knowledge_math.KIND_ENGINE_TARGET.get(kind),
                    "blended_value": knowledge_math.blend(
                        float(entry["prior_value"] or 0), float(entry["proposed_value"] or 0),
                        float(entry["effective_weight"] or 0)),
                }
        for winner in winners.values():
            winner.pop("_specificity", None)
        return winners

    @staticmethod
    def _knowledge_dict(entry: KnowledgeEntry) -> dict[str, Any]:
        return {
            "knowledge_id": entry.knowledge_id, "case_id": entry.case_id,
            "report_id": entry.report_id, "kind": entry.kind,
            "scope_shop_code": entry.scope_shop_code,
            "scope_goods_code": entry.scope_goods_code,
            "scope_category": entry.scope_category,
            "applies_from": entry.applies_from, "applies_to": entry.applies_to,
            "prior_value": entry.prior_value, "proposed_value": entry.proposed_value,
            "status": entry.status, "effective_weight": entry.effective_weight,
            "posterior": entry.posterior or {}, "evidence": entry.evidence or {},
            "invalidated": entry.invalidated, "expires_at": entry.expires_at,
            "created_at": entry.created_at, "updated_at": entry.updated_at,
            "version": entry.version, "scope": entry.scope,
        }

    async def review_count(self, case_id: str) -> int:
        async with self.sessions() as session:
            return int(await session.scalar(select(func.count()).select_from(HumanReview).where(
                HumanReview.case_id == case_id)) or 0)

    async def pending_review_count(self) -> int:
        async with self.sessions() as session:
            return int(await session.scalar(select(func.count()).select_from(AttributionCase).where(
                AttributionCase.state.in_([
                    CaseState.NEEDS_REVIEW, CaseState.CHANGES_REQUESTED, CaseState.FAILED,
                ])
            )) or 0)

    # ---- Administrator surfaces ----

    #: States that place a case in the human review queue and drive the navigation badge.
    PENDING_REVIEW_STATES = (
        CaseState.NEEDS_REVIEW, CaseState.CHANGES_REQUESTED, CaseState.FAILED,
    )

    async def admin_overview(self, *, now: datetime | None = None) -> dict:
        """Queue depth, lease occupancy and backlog age for the operations console."""
        now = now or _now()
        async with self.sessions() as session:
            state_rows = (await session.execute(
                select(AttributionCase.state, func.count()).group_by(AttributionCase.state))).all()
            counts = {state.value if hasattr(state, "value") else str(state): int(total)
                      for state, total in state_rows}
            run_rows = (await session.execute(
                select(ReplenishmentRun.state, func.count()).group_by(ReplenishmentRun.state))).all()
            run_counts = {state.value if hasattr(state, "value") else str(state): int(total)
                          for state, total in run_rows}
            oldest_queued = await session.scalar(
                select(func.min(AttributionCase.created_at)).where(
                    AttributionCase.state.in_([CaseState.QUEUED, CaseState.RUNNING])))
            leases = (await session.scalars(select(WorkerLease).order_by(
                WorkerLease.expires_at))).all()
            lease_cases = {lease.case_id for lease in leases}
            case_rows = {
                case.case_id: case
                for case in (await session.scalars(select(AttributionCase).where(
                    AttributionCase.case_id.in_(lease_cases)))).all()
            } if lease_cases else {}
            return {
                "generated_at": now,
                "cases_by_state": counts,
                "runs_by_state": run_counts,
                "pending_review": sum(
                    counts.get(state.value, 0) for state in self.PENDING_REVIEW_STATES),
                "backlog": {
                    "queued": counts.get(CaseState.QUEUED.value, 0),
                    "running": counts.get(CaseState.RUNNING.value, 0),
                    "oldest_started_at": oldest_queued,
                    "oldest_age_seconds": (
                        int((now - _as_aware(oldest_queued)).total_seconds())
                        if oldest_queued else None),
                },
                "leases": [{
                    "case_id": lease.case_id,
                    "worker_id": lease.worker_id,
                    "expires_at": lease.expires_at,
                    "expired": _as_aware(lease.expires_at) <= now,
                    "seconds_remaining": int(
                        (_as_aware(lease.expires_at) - now).total_seconds()),
                    "shop_code": getattr(case_rows.get(lease.case_id), "shop_code", None),
                    "goods_code": getattr(case_rows.get(lease.case_id), "goods_code", None),
                    "state": getattr(case_rows.get(lease.case_id), "state", None),
                } for lease in leases],
            }

    async def list_jobs(self, *, limit: int = 50, offset: int = 0) -> dict:
        async with self.sessions() as session:
            jobs = (await session.scalars(select(AttributionJob).order_by(
                AttributionJob.created_at.desc()).limit(min(limit, 100)).offset(offset))).all()
            job_ids = [job.job_id for job in jobs]
            rollup: dict[str, dict[str, int]] = {job_id: {} for job_id in job_ids}
            if job_ids:
                rows = (await session.execute(
                    select(AttributionCase.job_id, AttributionCase.state, func.count())
                    .where(AttributionCase.job_id.in_(job_ids))
                    .group_by(AttributionCase.job_id, AttributionCase.state))).all()
                for job_id, state, total in rows:
                    rollup[job_id][state.value if hasattr(state, "value") else str(state)] = int(total)
            items = []
            for job in jobs:
                states = rollup.get(job.job_id, {})
                total = sum(states.values())
                open_cases = states.get(CaseState.QUEUED.value, 0) + states.get(CaseState.RUNNING.value, 0)
                items.append({
                    "job_id": job.job_id, "run_id": job.run_id,
                    "status": "RUNNING" if states.get(CaseState.RUNNING.value) else (
                        "QUEUED" if open_cases else "COMPLETED"),
                    "total_cases": total, "completed_cases": total - open_cases,
                    "pending_review": sum(
                        states.get(state.value, 0) for state in self.PENDING_REVIEW_STATES),
                    "cases_by_state": states,
                    "created_at": job.created_at, "updated_at": job.updated_at,
                })
            return {
                "items": items,
                "total": int(await session.scalar(
                    select(func.count()).select_from(AttributionJob)) or 0),
                "page": offset // max(limit, 1) + 1,
                "page_size": min(limit, 100),
            }

    async def review_queue(self, *, limit: int = 50, offset: int = 0,
                           state: CaseState | None = None) -> dict:
        """Pending review cases annotated with the submission consequence of dismissing them.

        Cancelling a case clears the review badge but never satisfies submission
        readiness, which requires HUMAN_APPROVED. ``blocks_run`` makes that visible
        before an administrator acts.
        """
        states = [state] if state else list(self.PENDING_REVIEW_STATES)
        async with self.sessions() as session:
            query = (select(AttributionCase).where(AttributionCase.state.in_(states))
                     .order_by(AttributionCase.created_at).limit(min(limit, 100)).offset(offset))
            cases = (await session.scalars(query)).all()
            run_ids = {case.run_id for case in cases}
            runs = {
                run.run_id: run
                for run in (await session.scalars(select(ReplenishmentRun).where(
                    ReplenishmentRun.run_id.in_(run_ids)))).all()
            } if run_ids else {}
            items = []
            for case in cases:
                run = runs.get(case.run_id)
                override = (run.draft_overrides or {}).get(
                    _line_key(case.shop_code, case.goods_code, case.decision_date)) if run else None
                blocks_run = bool(
                    override and override["override_qty"] != override["recommended_qty"])
                items.append(await self._case_summary(session, case) | {
                    "reason_code": case.reason_code,
                    "error_code": case.error_code,
                    "blocks_run": blocks_run,
                    "run_state": run.state if run else None,
                    "run_locked": bool(run and run.state == RunState.SUBMITTED_LOCKED),
                })
            total = int(await session.scalar(select(func.count()).select_from(
                AttributionCase).where(AttributionCase.state.in_(states))) or 0)
            return {"items": items, "total": total,
                    "page": offset // max(limit, 1) + 1, "page_size": min(limit, 100)}

    async def bulk_dismiss(self, items: Sequence[tuple[str, int]], *,
                           actor: str, reason: str) -> dict:
        """Cancel many cases, reporting per-case outcomes rather than failing wholesale.

        Each case carries its own optimistic-concurrency version, so a single stale
        entry must not discard the rest of the batch.
        """
        succeeded: list[dict] = []
        failed: list[dict] = []
        for case_id, expected_version in items:
            try:
                case = await self.cancel_case(case_id, expected_version)
            except AttributionError as exc:
                failed.append({"case_id": case_id, "code": exc.code, "message": exc.message})
                continue
            await self.append_trace(case_id, f"admin-{case_id}", "ADMIN_DISMISSED",
                                    {"actor": actor, "reason": reason})
            succeeded.append(case)
        return {"succeeded": succeeded, "failed": failed,
                "succeeded_count": len(succeeded), "failed_count": len(failed)}


    async def append_trace(self, case_id: str, trace_id: str, event_type: str, payload: dict) -> None:
        async with self.sessions() as session, session.begin():
            if not await session.get(AttributionCase, case_id):
                raise NotFoundError("case not found")
            session.add(TraceEvent(trace_event_id=_id(), trace_id=trace_id, case_id=case_id,
                event_type=event_type, payload=self._redact(payload), created_at=_now()))

    async def get_trace(self, trace_id: str) -> list[dict]:
        async with self.sessions() as session:
            events = (await session.scalars(select(TraceEvent).where(
                TraceEvent.trace_id == trace_id).order_by(TraceEvent.created_at))).all()
            return [{"event_type": e.event_type, "payload": e.payload, "created_at": e.created_at} for e in events]

    async def get_attempt_log(self, case_id: str, attempt_number: int) -> list[dict]:
        trace_id = f"attr-{case_id}-attempt-{attempt_number}"
        async with self.sessions() as session:
            case = await session.get(AttributionCase, case_id)
            if not case:
                raise NotFoundError("case not found", details={"case_id": case_id})
            attempt = await session.scalar(select(ExecutionAttempt).where(
                ExecutionAttempt.case_id == case_id,
                ExecutionAttempt.number == attempt_number,
            ))
            if not attempt:
                raise NotFoundError("attempt not found", details={
                    "case_id": case_id, "attempt_number": attempt_number,
                })
            events = (await session.scalars(select(TraceEvent).where(
                TraceEvent.case_id == case_id,
                TraceEvent.trace_id == trace_id,
            ).order_by(TraceEvent.created_at))).all()
            return [{
                "timestamp": event.created_at,
                "trace_id": event.trace_id,
                "event_type": event.event_type,
                "payload": event.payload,
            } for event in events]

    # Worker-facing leasing and attempt operations.
    async def claim_case(self, worker_id: str, lease_seconds: int = 90) -> dict | None:
        now = _now()
        async with self.sessions() as session, session.begin():
            case = await session.scalar(select(AttributionCase).where(
                AttributionCase.state.in_([CaseState.QUEUED, CaseState.RUNNING]),
                (AttributionCase.lease_expires_at.is_(None)) | (AttributionCase.lease_expires_at < now),
            ).order_by(AttributionCase.created_at).with_for_update(skip_locked=True))
            if not case:
                return None
            case.state = CaseState.RUNNING
            case.lease_owner = worker_id
            case.lease_expires_at = now + timedelta(seconds=lease_seconds)
            case.version += 1
            case.updated_at = now
            await session.merge(WorkerLease(case_id=case.case_id, worker_id=worker_id,
                                            expires_at=case.lease_expires_at, updated_at=now))
            return self._case_dict(case) | {
                "snapshot": case.snapshot, "reason_code": case.reason_code,
                "reason_text": case.reason_text,
            }

    async def renew_lease(self, case_id: str, worker_id: str, lease_seconds: int = 90) -> bool:
        async with self.sessions() as session, session.begin():
            case = await session.get(AttributionCase, case_id)
            if not case or case.lease_owner != worker_id or case.state != CaseState.RUNNING:
                return False
            case.lease_expires_at = _now() + timedelta(seconds=lease_seconds)
            lease = await session.get(WorkerLease, case_id)
            if lease:
                lease.expires_at = case.lease_expires_at
                lease.updated_at = _now()
            return True

    async def begin_attempt(self, case_id: str, worker_id: str) -> dict:
        now = _now()
        async with self.sessions() as session, session.begin():
            case = await self._case_for_update(session, case_id)
            if case.state != CaseState.RUNNING or case.lease_owner != worker_id:
                raise StateTransitionError("worker does not own the running case lease")
            count = int(await session.scalar(select(func.count()).select_from(ExecutionAttempt).where(
                ExecutionAttempt.case_id == case_id)) or 0)
            attempt = ExecutionAttempt(attempt_id=_id(), case_id=case_id, number=count + 1,
                state="RUNNING", started_at=now)
            session.add(attempt)
            return {"attempt_id": attempt.attempt_id, "number": attempt.number}

    async def finish_attempt(self, case_id: str, attempt_id: str, worker_id: str, *,
                             report: dict | None = None,
                             partial: bool = False, error_code: str | None = None,
                             error_detail: str | None = None) -> dict:
        now = _now()
        async with self.sessions() as session, session.begin():
            case_ref = await session.get(AttributionCase, case_id)
            if not case_ref:
                raise NotFoundError("case not found", details={"case_id": case_id})
            run = await session.scalar(select(ReplenishmentRun).where(
                ReplenishmentRun.run_id == case_ref.run_id).with_for_update())
            if not run:
                raise NotFoundError("run not found", details={"run_id": case_ref.run_id})
            case = await self._case_for_update(session, case_id)
            attempt = await session.scalar(select(ExecutionAttempt).where(
                ExecutionAttempt.attempt_id == attempt_id,
            ).with_for_update().execution_options(populate_existing=True))
            if not attempt or attempt.case_id != case_id:
                raise NotFoundError("attempt not found")
            run_locked = run.state == RunState.SUBMITTED_LOCKED
            if (attempt.state != "RUNNING" or case.state != CaseState.RUNNING or
                    case.lease_owner != worker_id or run_locked):
                if attempt.state == "RUNNING":
                    attempt.state = "STALE"
                    attempt.finished_at = now
                    attempt.error_code = "RUN_LOCKED" if run_locked else "LEASE_FENCED"
                    attempt.error_detail = (
                        "run was submitted before worker completion" if run_locked
                        else "worker no longer owns this case lease")
                return self._case_dict(case) | {"ignored_stale_completion": True}
            attempt.finished_at = now
            if case.cancel_requested:
                attempt.state = "CANCELLED"
                attempt.error_code = "CASE_CANCELLED"
                attempt.error_detail = "case cancellation was requested"
                case.state = CaseState.CANCELLED
                case.error_code = "CASE_CANCELLED"
                case.error_message = "case cancellation was requested"
            elif report is not None:
                latest = await self._latest_report(session, case_id)
                saved = AttributionReport(report_id=_id(), case_id=case_id,
                    version=(latest.version if latest else 0) + 1, report=report, partial=partial,
                    source="AGENT", created_at=now,
                    claim_verdict=(report.get("operator_claim") or {}).get("verdict"))
                session.add(saved)
                attempt.state = "SUCCEEDED"
                case.state, case.partial = CaseState.NEEDS_REVIEW, partial
                case.error_code = None
                case.error_message = None
            else:
                attempt.state, attempt.error_code, attempt.error_detail = "FAILED", error_code, error_detail
                attempts = int(await session.scalar(select(func.count()).select_from(ExecutionAttempt).where(
                    ExecutionAttempt.case_id == case_id)) or 0)
                case.state = CaseState.QUEUED if attempts < 3 and not case.cancel_requested else CaseState.FAILED
                if case.cancel_requested:
                    case.state = CaseState.CANCELLED
                case.error_code = error_code
                case.error_message = error_detail
            case.lease_owner = None
            # Lease expiry doubles as the deterministic retry-not-before marker. A crashed
            # worker has no finish record and is instead reclaimed after its normal lease.
            case.lease_expires_at = (now + timedelta(seconds=2 ** (attempts - 1))
                                     if report is None and case.state == CaseState.QUEUED else None)
            lease = await session.get(WorkerLease, case_id)
            if lease:
                await session.delete(lease)
            case.version += 1
            case.updated_at = now
            await self._refresh_run_state(session, case.run_id)
            return self._case_dict(case)

    def _validate_event_against_run(self, run: ReplenishmentRun, event) -> None:
        if snapshot_hash(event.recommendation_snapshot) != event.snapshot_hash:
            raise ValidationError("snapshot_hash does not match recommendation_snapshot")
        replay_inputs = {"shop", "sku_info", "forecast", "decision_date"}
        if not replay_inputs <= set(event.recommendation_snapshot):
            raise SnapshotUnavailableError(
                "historical snapshot lacks deterministic replay inputs",
                details={"missing": sorted(replay_inputs - set(event.recommendation_snapshot))},
            )
        results = run.payload.get("results") if isinstance(run.payload, dict) else None
        if results is None:
            return
        match = next((result for result in results if str(result.get("sku") or result.get("goods_code")) ==
                      event.goods_code), None)
        if match and int(match.get("chosen_qty", match.get("final_qty", -1))) != event.recommended_qty:
            raise ValidationError("recommended quantity conflicts with local run record")

    async def _readiness_in_session(self, session: AsyncSession, run: ReplenishmentRun) -> SubmissionReadiness:
        blockers: list[dict] = []
        modified = 0
        approved = 0
        for _key, override in (run.draft_overrides or {}).items():
            if override["override_qty"] == override["recommended_qty"]:
                continue
            modified += 1
            cases = (await session.scalars(select(AttributionCase).where(
                AttributionCase.run_id == run.run_id,
                AttributionCase.shop_code == override["shop_code"],
                AttributionCase.goods_code == override["goods_code"],
                AttributionCase.decision_date == override["decision_date"],
                AttributionCase.state != CaseState.SUPERSEDED,
            ).order_by(AttributionCase.case_version.desc()))).all()
            case = cases[0] if cases else None
            if case and case.state == CaseState.HUMAN_APPROVED and self._matching_binding(case, override):
                approved += 1
            else:
                blockers.append({
                    "sku": override["goods_code"], "case_id": case.case_id if case else None,
                    "code": "HUMAN_APPROVAL_REQUIRED",
                    "message": "A matching HUMAN_APPROVED attribution case is required",
                    "status": case.state if case else "MISSING",
                })
        ready = not blockers
        if run.state != RunState.SUBMITTED_LOCKED:
            target_state = RunState.READY_TO_SUBMIT if ready else (
                RunState.ATTRIBUTION_REVIEW_REQUIRED if any(
                    b["status"] in {
                        CaseState.NEEDS_REVIEW, CaseState.CHANGES_REQUESTED,
                        CaseState.FAILED, CaseState.CANCELLED,
                    }
                    for b in blockers) else RunState.ATTRIBUTION_RUNNING)
            if run.state != target_state:
                run.state = target_state
                run.version += 1
                run.updated_at = _now()
        return SubmissionReadiness(run_id=run.run_id, run_version=run.version, status=run.state,
                                   ready=ready, modified_count=modified, approved_count=approved,
                                   blockers=blockers)

    async def _refresh_run_state(self, session: AsyncSession, run_id: str) -> None:
        run = await session.get(ReplenishmentRun, run_id)
        if run and run.state != RunState.SUBMITTED_LOCKED:
            await self._readiness_in_session(session, run)
            run.updated_at = _now()

    @staticmethod
    def _matching_binding(case: AttributionCase, override: dict) -> bool:
        return (case.recommended_qty == override["recommended_qty"] and
                case.override_qty == override["override_qty"] and
                case.snapshot_hash == override["snapshot_hash"])

    async def _case_for_update(self, session: AsyncSession, case_id: str) -> AttributionCase:
        case = await session.scalar(select(AttributionCase).where(
            AttributionCase.case_id == case_id).with_for_update().execution_options(
                populate_existing=True))
        if not case:
            raise NotFoundError("case not found", details={"case_id": case_id})
        return case

    async def _mutable_case_for_update(
        self, session: AsyncSession, case_id: str,
    ) -> AttributionCase:
        case_ref = await session.get(AttributionCase, case_id)
        if not case_ref:
            raise NotFoundError("case not found", details={"case_id": case_id})
        run = await session.scalar(select(ReplenishmentRun).where(
            ReplenishmentRun.run_id == case_ref.run_id).with_for_update())
        if not run:
            raise NotFoundError("run not found", details={"run_id": case_ref.run_id})
        if run.state == RunState.SUBMITTED_LOCKED:
            raise StateTransitionError("submitted run and its attribution cases are read-only")
        return await self._case_for_update(session, case_id)

    async def _latest_report(self, session: AsyncSession, case_id: str) -> AttributionReport | None:
        return await session.scalar(select(AttributionReport).where(
            AttributionReport.case_id == case_id).order_by(AttributionReport.version.desc()))

    @staticmethod
    def _validate_report_conservation(case: AttributionCase, report: dict) -> None:
        expected = case.override_qty - case.recommended_qty
        actual = float(report.get("signed_gap", expected))
        contributions = report.get("allocations", report.get("contributions", []))
        total = sum(float(item.get("signed_contribution_qty", 0)) for item in contributions)
        # Counterfactual reports are anchored on a world with the questioned
        # assumptions switched off, so their allocations conserve to the distance
        # from that anchor rather than to the store manager's gap. Defaulting the
        # anchor to the recommendation keeps manually written reports -- whose
        # reviewer-entered causes are stated straight against the gap -- passing
        # exactly as before.
        anchor = float(report.get("conservation_anchor_qty", case.recommended_qty))
        anchored_total = case.override_qty - anchor
        unexplained = float(report.get("unexplained_signed_gap", anchored_total - total))
        if abs(actual - expected) > 1e-9 or abs(total + unexplained - anchored_total) > 1e-9:
            raise ValidationError("report fails quantity conservation")

    @staticmethod
    def _redact(value: Any) -> Any:
        if isinstance(value, dict):
            content_type = str(value.get("type", "")).lower()
            if "reasoning" in content_type or content_type in {"thought", "chain_of_thought"}:
                return {"type": value.get("type"), "redacted": "private_reasoning"}
            sensitive_keys = {
                "reason_text", "thought", "reasoning", "chain_of_thought",
                "authorization", "token", "access_token", "refresh_token", "api_key",
                "password", "secret", "credential", "connection_string",
            }
            return {
                key: (
                    "[REDACTED]" if key.lower() in sensitive_keys
                    else AttributionRepository._redact(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [AttributionRepository._redact(item) for item in value]
        return value

    @staticmethod
    def _run_dict(run: ReplenishmentRun) -> dict:
        return {
            "run_id": run.run_id, "state": run.state, "version": run.version,
            "submitted_at": run.submitted_at, "submitted_by": run.submitted_by,
        }

    async def _run_view(self, session: AsyncSession, run: ReplenishmentRun) -> dict[str, Any]:
        payload = copy.deepcopy(run.payload) if isinstance(run.payload, dict) else {}
        cases = (await session.scalars(select(AttributionCase).where(
            AttributionCase.run_id == run.run_id,
            AttributionCase.state != CaseState.SUPERSEDED,
        ).order_by(AttributionCase.case_version.desc()))).all()
        current_cases: dict[str, AttributionCase] = {}
        for case in cases:
            current_cases.setdefault(
                _line_key(case.shop_code, case.goods_code, case.decision_date), case)
        overrides = run.draft_overrides or {}
        for result in payload.get("results", []):
            shop_code = str(result.get("shop") or payload.get("shop_code") or "")
            goods_code = str(result.get("sku") or result.get("goods_code") or "")
            decision_date = str(result.get("apply_date") or payload.get("decision_date") or "")[:10]
            key = _line_key(shop_code, goods_code, decision_date)
            override = overrides.get(key)
            if override:
                result["final_qty"] = override["override_qty"]
            case = current_cases.get(key)
            result["attribution_case_id"] = case.case_id if case else None
            result["attribution_status"] = case.state if case else None
        payload["total_qty"] = sum(
            int(item.get("final_qty", item.get("chosen_qty", 0)) or 0)
            for item in payload.get("results", [])
        )
        payload["status"] = run.state
        payload["version"] = run.version
        payload["submitted_at"] = run.submitted_at
        payload["submitted_by"] = run.submitted_by
        payload["attribution_total"] = len(current_cases)
        payload["attribution_approved"] = sum(
            case.state == CaseState.HUMAN_APPROVED for case in current_cases.values())
        payload["attribution_pending"] = (
            payload["attribution_total"] - payload["attribution_approved"])
        return payload

    @staticmethod
    def _case_dict(case: AttributionCase, report: AttributionReport | None = None) -> dict:
        report_data = report.report if report else {}
        coverage = report_data.get("coverage_ratio")
        sku_info = case.snapshot.get("sku_info", {}) if isinstance(case.snapshot, dict) else {}
        signed_gap = case.override_qty - case.recommended_qty
        return AttributionCaseResponse(case_id=case.case_id, job_id=case.job_id, run_id=case.run_id,
            state=case.state, status=case.state, version=case.version, event_id=case.event_id,
            case_version=case.case_version, shop_code=case.shop_code,
            shop_name=case.snapshot.get("shop_name") if isinstance(case.snapshot, dict) else None,
            goods_code=case.goods_code, goods_name=sku_info.get("goods_name"),
            decision_date=case.decision_date, recommended_qty=case.recommended_qty,
            override_qty=case.override_qty, output_language=case.output_language,
            signed_gap=signed_gap,
            direction="UP" if signed_gap > 0 else "DOWN", snapshot_hash=case.snapshot_hash,
            partial=case.partial, coverage_ratio=coverage,
            report_version=report.version if report else None, created_at=case.created_at,
            updated_at=case.updated_at).model_dump(mode="json")

    async def _case_summary(self, session: AsyncSession, case: AttributionCase,
                            report: AttributionReport | None = None) -> dict:
        return self._case_dict(case, report or await self._latest_report(session, case.case_id))

    @staticmethod
    def _report_dict(report: AttributionReport) -> dict:
        payload = report.report or {}
        contributions = payload.get("allocations", payload.get("contributions", []))
        allocations = []
        total_abs = sum(abs(float(item.get("signed_contribution_qty", 0))) for item in contributions)
        for item in contributions:
            amount = float(item.get("signed_contribution_qty", 0))
            allocations.append({
                "cause_code": item.get("cause_code", "OTHER"),
                "domain": item.get("domain", "manual"),
                "label": item.get("label"),
                "signed_contribution_qty": amount,
                "absolute_contribution_weight": item.get(
                    "absolute_contribution_weight", abs(amount) / total_abs if total_abs else 0.0),
                "expected_direction": item.get("expected_direction", "NONE"),
                "explanation": item.get("explanation", ""),
                "evidence_refs": item.get("evidence_refs", []),
                "counterfactual_result": item.get("counterfactual_result"),
            })
        shapley = payload.get("shapley", {})
        return {
            "report_id": report.report_id, "version": report.version,
            "summary": payload.get("summary", ""),
            "model_summary": payload.get("model_summary", ""),
            "primary_cause": payload.get("primary_cause"),
            "signed_gap": payload.get("signed_gap", 0),
            "unexplained_signed_gap": payload.get("unexplained_signed_gap", 0),
            "coverage_ratio": payload.get("coverage_ratio", 0),
            "recommended_qty": payload.get("recommended_qty"),
            "override_qty": payload.get("override_qty"),
            "baseline_qty": payload.get("baseline_qty"),
            # The point the counterfactual measures from. Without it a reader cannot
            # tell what the allocations are a share of, and the conservation check
            # shown next to them cannot be reproduced.
            "bare_baseline_qty": payload.get("bare_baseline_qty"),
            "conservation_anchor_qty": payload.get(
                "conservation_anchor_qty", payload.get("recommended_qty")),
            "explained_signed_qty": payload.get("explained_signed_qty"),
            "replay_drift_qty": payload.get("replay_drift_qty"),
            "partial": report.partial,
            "risk_flags": payload.get("risk_flags", []),
            "unknown_cause_codes": payload.get("unknown_cause_codes", []),
            "unquantifiable_cause_codes": payload.get("unquantifiable_cause_codes", []),
            "conflicts": payload.get("conflicts", []),
            "allocations": allocations,
            "knowledge_candidates": payload.get("knowledge_candidates", []),
            "operator_claim": payload.get("operator_claim"),
            "evidence": payload.get("evidence", []),
            "shapley_method": payload.get("shapley_method", shapley.get("method", "exact")),
            "shapley_samples": payload.get("shapley_samples", shapley.get("sample_count")),
            "shapley_error_estimate": payload.get(
                "shapley_error_estimate", shapley.get("error_estimate")),
            "source": report.source, "created_at": report.created_at,
        }
