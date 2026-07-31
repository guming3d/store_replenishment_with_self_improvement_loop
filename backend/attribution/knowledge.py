"""Confidence evolution for learned store and SKU knowledge.

One approved case is an anecdote. A knowledge entry therefore starts with no
weight in the engine at all and earns it only as completed outcomes confirm it,
which is why the weight here is a *lower* confidence bound rather than a point
estimate: two hits and no misses is not evidence, and a point estimate would
score it 1.0 and let a single lucky case rewrite a store's parameters.

The same bound runs in reverse. When outcomes start contradicting an active
entry the bound falls back through the promotion line on its own, so knowledge
that stops working is demoted by the arithmetic instead of waiting for someone
to notice it. That is the difference between this and the expiry timestamp it
replaces: an expiry says when to stop trusting a fact, not whether it was ever
true.
"""
from __future__ import annotations

import math
from typing import Any, Literal

KNOWLEDGE_VERSION = "knowledge-v1"

KnowledgeStatus = Literal["CANDIDATE", "SHADOW", "ACTIVE", "RETIRED"]
KNOWLEDGE_STATUSES: tuple[str, ...] = ("CANDIDATE", "SHADOW", "ACTIVE", "RETIRED")

#: Kinds a knowledge entry may propose. Each maps to exactly one engine input,
#: because knowledge that cannot be replayed through the engine cannot be
#: verified and so could never be promoted past SHADOW.
KnowledgeKind = Literal[
    "SEASONAL_FACTOR",
    "HOLIDAY_FACTOR",
    "SUBSTITUTION_RATE",
    "DEMAND_LEVEL",
    "FILL_RATE",
    "SHELF_MAX",
]
KNOWLEDGE_KINDS: tuple[str, ...] = (
    "SEASONAL_FACTOR", "HOLIDAY_FACTOR", "SUBSTITUTION_RATE",
    "DEMAND_LEVEL", "FILL_RATE", "SHELF_MAX",
)

#: Which engine input each kind overrides. The resolver reads this so a new kind
#: cannot be added without saying where it lands, which is the mistake that left
#: the seasonal cause unable to move any quantity.
KIND_ENGINE_TARGET: dict[str, str] = {
    "SEASONAL_FACTOR": "factor_overrides.season",
    "HOLIDAY_FACTOR": "factor_overrides.holiday",
    "SUBSTITUTION_RATE": "seed.substitutions.transfer_rate",
    "DEMAND_LEVEL": "target_daily_demand_delta",
    "FILL_RATE": "params.fill_rate",
    "SHELF_MAX": "params.shelf_max",
}
if set(KIND_ENGINE_TARGET) != set(KNOWLEDGE_KINDS):  # pragma: no cover - import-time invariant
    raise RuntimeError(
        "knowledge kind drift: "
        f"{sorted(set(KNOWLEDGE_KINDS) ^ set(KIND_ENGINE_TARGET))} lacks an engine target"
    )

#: 95% one-sided normal quantile used for the Wilson interval.
Z_SCORE = 1.6449

#: A knowledge entry has to beat a coin flip before it earns any weight at all.
PROMOTION_FLOOR = 0.5

#: Completed outcomes required before an entry may leave CANDIDATE. Below this
#: the interval is so wide that the weight would be zero anyway; the threshold
#: exists to make the intent explicit rather than incidental.
MIN_SHADOW_SAMPLES = 5


def wilson_lower_bound(hits: int, trials: int, *, z: float = Z_SCORE) -> float:
    """Lower bound of the success rate; 0.0 when there is no evidence at all.

    Wilson rather than Wald because the counts here are small and often extreme
    (three hits, no misses), exactly where the normal approximation returns a
    bound above 1 or below 0.
    """
    hits, trials = int(hits), int(trials)
    if trials <= 0 or hits < 0 or hits > trials:
        return 0.0
    p = hits / trials
    denominator = 1 + z * z / trials
    centre = p + z * z / (2 * trials)
    margin = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials))
    return max(0.0, min(1.0, (centre - margin) / denominator))


def posterior(hit_count: int, miss_count: int) -> dict[str, Any]:
    """Beta(1,1)-prior posterior over 'this entry improves accuracy'."""
    hits, misses = max(0, int(hit_count)), max(0, int(miss_count))
    alpha, beta = 1 + hits, 1 + misses
    n = alpha + beta
    return {
        "version": KNOWLEDGE_VERSION,
        "hit_count": hits,
        "miss_count": misses,
        "sample_size": hits + misses,
        "mean": round(alpha / n, 6),
        "variance": round(alpha * beta / (n * n * (n + 1)), 6),
        "lower_bound": round(wilson_lower_bound(hits, hits + misses), 6),
    }


def effective_weight(hit_count: int, miss_count: int, *,
                     floor: float = PROMOTION_FLOOR) -> float:
    """Map the confidence bound onto a 0..1 blend weight.

    Zero until the bound clears ``floor``, then rising linearly. Knowledge is
    therefore phased into the engine as evidence accrues instead of switching on
    at full strength the moment a reviewer approves one case.
    """
    lower = wilson_lower_bound(hit_count, int(hit_count) + int(miss_count))
    if lower <= floor:
        return 0.0
    return round(min(1.0, (lower - floor) / (1 - floor)), 6)


def blend(prior_value: float, proposed_value: float, weight: float) -> float:
    """Interpolate between what the engine assumes today and what was learned."""
    w = max(0.0, min(1.0, float(weight)))
    return float(prior_value) * (1 - w) + float(proposed_value) * w


def next_status(current: str, *, sample_size: int, weight: float,
                invalidated: bool = False) -> str:
    """Advance the lifecycle from the evidence, never skipping shadow evaluation.

    RETIRED is terminal on purpose. An entry whose evidence turned against it
    should be re-mined as a fresh candidate with a fresh audit trail rather than
    quietly resurrected once a couple of favourable outcomes arrive.
    """
    if current not in KNOWLEDGE_STATUSES:
        raise ValueError(f"unknown knowledge status: {current}")
    if invalidated or current == "RETIRED":
        return "RETIRED"
    if sample_size < MIN_SHADOW_SAMPLES:
        return "CANDIDATE" if current == "CANDIDATE" else current
    if weight > 0:
        return "ACTIVE"
    return "RETIRED" if current == "ACTIVE" else "SHADOW"


def apply_outcome(entry: dict[str, Any], *, improved: bool) -> dict[str, Any]:
    """Fold one completed outcome into an entry's posterior, weight and status.

    ``improved`` is the verdict from :mod:`attribution.outcomes`, not a reviewer
    opinion: knowledge is promoted on measured accuracy so that the loop cannot
    converge on whatever the store manager happens to prefer.
    """
    stats = entry.get("posterior") or {}
    hits = int(stats.get("hit_count", 0)) + (1 if improved else 0)
    misses = int(stats.get("miss_count", 0)) + (0 if improved else 1)
    updated = posterior(hits, misses)
    weight = effective_weight(hits, misses)
    return {
        "posterior": updated,
        "effective_weight": weight,
        "status": next_status(
            str(entry.get("status") or "CANDIDATE"),
            sample_size=updated["sample_size"], weight=weight,
            invalidated=bool(entry.get("invalidated")),
        ),
    }


def scope_matches(entry_scope: dict[str, Any], *, shop_code: str | None,
                  goods_code: str | None, category: str | None = None,
                  on_date: str | None = None) -> bool:
    """Whether an entry applies to a store, SKU and date.

    A null field in the entry means "any", so a store-wide entry matches every
    SKU in that store while a SKU entry never leaks to its neighbours.
    """
    if entry_scope.get("shop_code") and entry_scope["shop_code"] != shop_code:
        return False
    if entry_scope.get("goods_code") and entry_scope["goods_code"] != goods_code:
        return False
    if entry_scope.get("category") and category and entry_scope["category"] != category:
        return False
    if on_date:
        applies_from = entry_scope.get("applies_from")
        applies_to = entry_scope.get("applies_to")
        if applies_from and on_date < applies_from:
            return False
        if applies_to and on_date > applies_to:
            return False
    return True


def scope_specificity(entry_scope: dict[str, Any]) -> int:
    """Rank overlapping entries so the narrowest scope wins.

    Without this a store-wide entry and a SKU entry would both match and the
    winner would depend on row order.
    """
    score = 0
    if entry_scope.get("shop_code"):
        score += 4
    if entry_scope.get("goods_code"):
        score += 2
    if entry_scope.get("category"):
        score += 1
    return score


#: Targets :func:`engine.run` knows how to apply. Kept as an independent list
#: rather than imported so that this module stays free of the engine, with
#: ``test_knowledge_engine_loop`` asserting the two never drift apart.
ENGINE_APPLICABLE_TARGETS: frozenset[str] = frozenset({
    "factor_overrides.season",
    "factor_overrides.holiday",
    "target_daily_demand_delta",
    "params.fill_rate",
    "params.shelf_max",
})


def engine_directives(entries: Any) -> dict[str, Any]:
    """Translate resolved knowledge into inputs :func:`engine.run` can consume.

    Pure and database-free, so the same call can resolve live knowledge for a new
    run or re-apply the directive list frozen into a past snapshot. Entries whose
    target the engine cannot reach — today only ``SUBSTITUTION_RATE``, which
    names a seed input rather than an engine argument — are returned under
    ``unsupported`` instead of being dropped, because knowledge that a reviewer
    approved and that then quietly does nothing is worse than knowledge that was
    never approved at all.

    Accepts either the ``{kind: entry}`` mapping from ``active_knowledge_for`` or
    any iterable of entries.
    """
    if isinstance(entries, dict):
        records = list(entries.values())
    else:
        records = list(entries or [])

    directives: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    for entry in records:
        if not isinstance(entry, dict):
            continue
        # A frozen directive already carries its target; a knowledge entry needs
        # its kind mapped, so a snapshot can be replayed without the entry row.
        kind = entry.get("kind")
        target = entry.get("target") or entry.get("engine_target") \
            or KIND_ENGINE_TARGET.get(str(kind))
        if not target:
            unsupported.append({"kind": kind, "target": None,
                                "knowledge_id": entry.get("knowledge_id") or entry.get("id"),
                                "reason": "UNKNOWN_KIND"})
            continue
        value = entry.get("blended_value")
        if value is None:
            value = entry.get("value")
        if value is None:
            value = entry.get("proposed_value")
        record = {"kind": kind, "target": target, "value": value,
                  "weight": entry.get("weight", entry.get("effective_weight")),
                  "knowledge_id": entry.get("knowledge_id") or entry.get("id")}
        if target not in ENGINE_APPLICABLE_TARGETS:
            unsupported.append({**record, "reason": "ENGINE_CANNOT_APPLY"})
            continue
        if value is None:
            unsupported.append({**record, "reason": "NO_VALUE"})
            continue
        directives.append(record)

    return {"directives": directives, "unsupported": unsupported}
