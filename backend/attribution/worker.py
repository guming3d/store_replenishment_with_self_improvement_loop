"""Lease-based asynchronous attribution worker."""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import traceback
import uuid
from contextlib import suppress
from typing import Awaitable, Callable

from .errors import AttributionError
from .harness import run_harness_attribution
from .repository import AttributionRepository

Executor = Callable[[dict], Awaitable[dict]]

logger = logging.getLogger(__name__)

#: How long the supervisor waits before restarting a crashed poll loop. Doubles on
#: each consecutive crash so a permanently broken dependency cannot become a hot loop.
RESTART_BACKOFF_SECONDS = 1.0
MAX_RESTART_BACKOFF_SECONDS = 60.0


class AttributionWorker:
    """Polls for queued cases and runs the attribution harness against them.

    The poll loop is supervised: a crash is recorded, logged and then retried with
    backoff. Nothing about a single bad poll may strand every future case in QUEUED,
    which is what an unsupervised loop does — silently, because the task object is
    retained and asyncio therefore never reports the unretrieved exception.
    """

    def __init__(self, repository: AttributionRepository, *, executor: Executor = run_harness_attribution,
                 concurrency: int | None = None, lease_seconds: int = 90, attempt_timeout: int = 600,
                 restart_on_crash: bool = True) -> None:
        self.repository = repository
        self.executor = executor
        self.concurrency = concurrency or int(os.getenv("ATTRIBUTION_WORKER_CONCURRENCY", "4"))
        self.lease_seconds = lease_seconds
        self.attempt_timeout = attempt_timeout
        self.restart_on_crash = restart_on_crash
        self.worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._last_poll_error: str | None = None
        self._crash_reason: str | None = None
        self._crash_count = 0

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._crash_reason = None
        self._crash_count = 0
        self._task = asyncio.create_task(self._supervise(), name=f"attribution-worker-{self.worker_id}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _supervise(self) -> None:
        """Run the poll loop, recording and retrying crashes instead of dying silently."""
        backoff = RESTART_BACKOFF_SECONDS
        while not self._stop.is_set():
            try:
                await self._run()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._crash_count += 1
                self._crash_reason = f"{type(exc).__name__}: {exc}"
                logger.exception(
                    "attribution worker poll loop crashed (crash #%s, worker_id=%s)",
                    self._crash_count, self.worker_id,
                )
                if not self.restart_on_crash:
                    self._crash_reason = (
                        f"{self._crash_reason}\n"
                        f"{''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))}"
                    )
                    raise
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
            backoff = min(backoff * 2, MAX_RESTART_BACKOFF_SECONDS)

    async def _run(self) -> None:
        semaphore = asyncio.Semaphore(self.concurrency)
        active: set[asyncio.Task] = set()
        poll_backoff = 0.25

        def on_process_done(completed: asyncio.Task) -> None:
            active.discard(completed)
            if completed.cancelled():
                return
            exc = completed.exception()
            if exc is not None:
                logger.exception("attribution case processing raised", exc_info=exc)

        while not self._stop.is_set():
            poll_failed = False
            while len(active) < self.concurrency and not self._stop.is_set():
                try:
                    case = await self.repository.claim_case(self.worker_id, self.lease_seconds)
                    self._last_poll_error = None
                    poll_backoff = 0.25
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Any poll failure — not just the database ones — must be survivable.
                    # Letting an unexpected type escape kills the loop and strands every
                    # subsequent case in QUEUED with no lease and no attempt row.
                    self._last_poll_error = f"{type(exc).__name__}: {exc}"
                    logger.warning("attribution worker poll failed: %s", self._last_poll_error,
                                   exc_info=True)
                    poll_failed = True
                    break
                if not case:
                    break
                task = asyncio.create_task(self._process(case, semaphore))
                active.add(task)
                task.add_done_callback(on_process_done)
            if poll_failed:
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=poll_backoff)
                poll_backoff = min(poll_backoff * 2, 5.0)
                continue
            if not active:
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=0.25)
            else:
                await asyncio.wait(active, timeout=0.25, return_when=asyncio.FIRST_COMPLETED)
        if active:
            await asyncio.gather(*active, return_exceptions=True)

    def status(self) -> dict:
        task_failed = bool(self._task and self._task.done() and not self._stop.is_set())
        return {
            "running": bool(self._task and not self._task.done()),
            "healthy": not task_failed,
            "last_poll_error": self._last_poll_error,
            "crash_count": self._crash_count,
            "crash_reason": self._crash_reason,
        }

    async def _process(self, case: dict, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            try:
                attempt = await self.repository.begin_attempt(case["case_id"], self.worker_id)
            except AttributionError:
                return
            trace_id = f"attr-{case['case_id']}-attempt-{attempt['number']}"
            await self.repository.append_trace(
                case["case_id"], trace_id, "ATTEMPT_STARTED",
                {"attempt_number": attempt["number"], "worker_id": self.worker_id},
            )
            renewal = asyncio.create_task(self._renew(case["case_id"]))
            try:
                async def emit(event_type: str, payload: dict) -> None:
                    await self.repository.append_trace(
                        case["case_id"], trace_id, event_type,
                        {"attempt_number": attempt["number"], **payload},
                    )

                execution_case = {
                    **case,
                    "_attempt_id": attempt["attempt_id"],
                    "_attempt_number": attempt["number"],
                    "_trace_id": trace_id,
                    "_trace_emitter": emit,
                }
                report = await asyncio.wait_for(
                    self.executor(execution_case), timeout=self.attempt_timeout)
                if not isinstance(report, dict):
                    raise TypeError("Harness executor returned a non-object report")
                partial = bool(report.get("partial", False))
                await self.repository.finish_attempt(case["case_id"], attempt["attempt_id"],
                                                     self.worker_id,
                                                     report=report, partial=partial)
                await self.repository.append_trace(
                    case["case_id"], trace_id, "ATTEMPT_COMPLETED",
                    {"attempt_number": attempt["number"], "partial": partial},
                )
            except asyncio.TimeoutError:
                await self.repository.finish_attempt(case["case_id"], attempt["attempt_id"],
                                                     self.worker_id,
                                                     error_code="ATTEMPT_TIMEOUT",
                                                     error_detail=f"hard limit {self.attempt_timeout}s")
                await self.repository.append_trace(
                    case["case_id"], trace_id, "ATTEMPT_FAILED",
                    {"attempt_number": attempt["number"], "error_code": "ATTEMPT_TIMEOUT"},
                )
            except AttributionError as exc:
                chain = exc.details.get("exception_chain")
                classification = " -> ".join(chain) if isinstance(chain, list) else None
                root_message = exc.details.get("root_message")
                cause = ": ".join(part for part in (classification, root_message) if part)
                detail = f"{exc.message}: {cause}" if cause else exc.message
                await self.repository.finish_attempt(case["case_id"], attempt["attempt_id"],
                                                     self.worker_id,
                                                     error_code=exc.code, error_detail=detail)
                await self.repository.append_trace(
                    case["case_id"], trace_id, "ATTEMPT_FAILED",
                    {"attempt_number": attempt["number"], "error_code": exc.code},
                )
            except Exception as exc:
                await self.repository.finish_attempt(case["case_id"], attempt["attempt_id"],
                                                     self.worker_id,
                                                     error_code="EXECUTION_ERROR",
                                                     error_detail=f"{type(exc).__name__}: {exc}")
                await self.repository.append_trace(
                    case["case_id"], trace_id, "ATTEMPT_FAILED",
                    {"attempt_number": attempt["number"], "error_code": "EXECUTION_ERROR"},
                )
            finally:
                renewal.cancel()
                with suppress(asyncio.CancelledError):
                    await renewal

    async def _renew(self, case_id: str) -> None:
        interval = max(1, self.lease_seconds // 2)
        while True:
            await asyncio.sleep(interval)
            if not await self.repository.renew_lease(case_id, self.worker_id, self.lease_seconds):
                return
