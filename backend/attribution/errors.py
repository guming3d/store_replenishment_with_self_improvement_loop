"""Stable, transport-neutral errors for attribution callers."""
from __future__ import annotations


class AttributionError(Exception):
    code = "ATTRIBUTION_ERROR"
    status_code = 400

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AttributionError):
    code, status_code = "NOT_FOUND", 404


class ConflictError(AttributionError):
    code, status_code = "CONFLICT", 409


class ValidationError(AttributionError):
    code, status_code = "VALIDATION_ERROR", 422


class SnapshotUnavailableError(ValidationError):
    code = "HISTORICAL_SNAPSHOT_UNAVAILABLE"


class StateTransitionError(ConflictError):
    code = "INVALID_STATE_TRANSITION"


class GateBlockedError(ConflictError):
    code = "SUBMISSION_GATE_BLOCKED"


class AgentUnavailableError(AttributionError):
    code, status_code = "AGENT_UNAVAILABLE", 503


class BudgetExceededError(AttributionError):
    code, status_code = "ATTEMPT_BUDGET_EXCEEDED", 503
