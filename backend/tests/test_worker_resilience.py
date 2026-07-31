"""The poll loop must survive an unexpected error rather than strand every case.

The failure these tests pin down was observed live: the loop caught only
``SQLAlchemyError``, so one unexpected exception ended the task, ``last_poll_error``
stayed ``None`` and — because the task object is retained — asyncio never reported
the unretrieved exception. Every case created afterwards sat in QUEUED forever.
"""
import asyncio

import pytest

from attribution.worker import AttributionWorker


class _FakeRepository:
    """Stands in for AttributionRepository with a scriptable ``claim_case``."""

    def __init__(self, *errors: BaseException) -> None:
        self._errors = list(errors)
        self.calls = 0

    async def claim_case(self, worker_id: str, lease_seconds: int = 90):
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return None


async def _drain(worker: AttributionWorker, *, until, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if until():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached within timeout")


@pytest.mark.asyncio
async def test_a_non_database_poll_error_does_not_kill_the_loop():
    repository = _FakeRepository(RuntimeError("boom"))
    worker = AttributionWorker(repository, concurrency=1)
    await worker.start()
    try:
        await _drain(worker, until=lambda: repository.calls >= 3)
        status = worker.status()
        assert status["running"] is True
        assert status["healthy"] is True
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_the_poll_error_is_recorded_then_cleared():
    repository = _FakeRepository(ValueError("transient"))
    worker = AttributionWorker(repository, concurrency=1)
    await worker.start()
    try:
        await _drain(worker, until=lambda: repository.calls >= 1)
        # The first poll fails and is recorded; the next success clears it.
        await _drain(worker, until=lambda: worker.status()["last_poll_error"] is None
                     and repository.calls >= 2)
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_a_crashed_loop_is_restarted_by_the_supervisor():
    repository = _FakeRepository()
    worker = AttributionWorker(repository, concurrency=1)
    original_run = worker._run
    crashes = {"n": 0}

    async def _flaky_run() -> None:
        if crashes["n"] == 0:
            crashes["n"] += 1
            raise RuntimeError("loop exploded once")
        await original_run()

    worker._run = _flaky_run  # type: ignore[method-assign]
    await worker.start()
    try:
        # The supervisor waits out its backoff, then the healthy loop starts polling.
        await _drain(worker, until=lambda: repository.calls >= 2, timeout=8.0)
        status = worker.status()
        assert status["running"] is True
        assert status["crash_count"] == 1
        assert "loop exploded once" in status["crash_reason"]
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_the_supervisor_records_a_crash_when_restart_is_disabled():
    class _FatalRepository:
        def __init__(self) -> None:
            self.calls = 0

        async def claim_case(self, worker_id: str, lease_seconds: int = 90):
            self.calls += 1
            raise RuntimeError("poll exploded")

    repository = _FatalRepository()
    worker = AttributionWorker(repository, concurrency=1)
    # Force the crash past the inner poll guard so the supervisor sees it.
    worker.restart_on_crash = False

    async def _explode() -> None:
        raise RuntimeError("loop exploded")

    worker._run = _explode  # type: ignore[method-assign]
    await worker.start()
    with pytest.raises(RuntimeError):
        await worker._task
    status = worker.status()
    assert status["healthy"] is False
    assert status["crash_count"] == 1
    assert "loop exploded" in status["crash_reason"]
    assert "Traceback" in status["crash_reason"]
    worker._task = None


@pytest.mark.asyncio
async def test_stop_is_clean_when_nothing_went_wrong():
    repository = _FakeRepository()
    worker = AttributionWorker(repository, concurrency=1)
    await worker.start()
    await _drain(worker, until=lambda: repository.calls >= 1)
    await worker.stop()
    status = worker.status()
    assert status["running"] is False
    assert status["healthy"] is True
    assert status["crash_count"] == 0
    assert status["crash_reason"] is None
