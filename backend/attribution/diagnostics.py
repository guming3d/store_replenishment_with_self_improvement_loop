"""Startup-only typed registry of diagnostic agents and their safe tools."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DiagnosticAgentDefinition:
    agent_id: str
    version: str
    domain: str
    applicable_scenarios: tuple[str, ...]
    required_evidence: tuple[str, ...]
    deterministic_tools: tuple[str, ...]
    finding_schema: str
    enabled: bool
    default_model_deployment_env: str


DIAGNOSTIC_AGENTS: tuple[DiagnosticAgentDefinition, ...] = (
    DiagnosticAgentDefinition(
        agent_id="seasonal-diagnostic", version="seasonal-v1", domain="seasonality",
        applicable_scenarios=("seasonal", "holiday"), required_evidence=(
            "seasonality_seed", "holiday_seed", "decision_date", "recommendation_trace"),
        deterministic_tools=("replay_factor_override",),
        finding_schema="SeasonalFindingV1", enabled=True,
        default_model_deployment_env="FOUNDRY_MODEL_DEPLOYMENT",
    ),
    DiagnosticAgentDefinition(
        agent_id="substitution-diagnostic", version="substitution-v1", domain="substitution",
        applicable_scenarios=("substitution",), required_evidence=(
            "substitution_seed", "substitute_inventory", "substitute_demand", "recommendation_trace"),
        deterministic_tools=("substitution_target_daily_delta", "replay_target_demand_delta"),
        finding_schema="SubstitutionFindingV1", enabled=True,
        default_model_deployment_env="FOUNDRY_MODEL_DEPLOYMENT",
    ),
)


def list_diagnostic_agents() -> list[dict]:
    """Return serializable declarations; no runtime registration is exposed."""
    return [asdict(agent) for agent in DIAGNOSTIC_AGENTS]
