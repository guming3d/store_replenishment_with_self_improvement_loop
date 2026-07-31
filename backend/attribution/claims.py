"""Judge the store manager's stated reason against the evidence.

The reason code and free text a store manager attaches to an override are the
one part of a Case that arrives already interpreted. Until now nothing consumed
them: the coordinator is shown the claim but is forbidden to treat it as proof,
and no deterministic step read it at all, so the field was written on every
override and never checked against anything.

The verdict is deliberately *not* taken from the model. The coordinator prompt
includes the claim, so a model asked to grade it would be grading text it has
already been anchored on -- it can mark ``SEASONAL_SHIFT`` applicable because
the operator typed "seasonal" and then report agreement with itself. Every
verdict here is therefore computed from two things the model cannot fabricate:
which causes the deterministic registry could quantify, and whether
:mod:`attribution.proposals` could reverse-solve the engine to a value that
reproduces the quantity actually ordered. Fluent text cannot make an engine
parameter reach a quantity it cannot reach.

Nothing in this module may influence an allocation, a Shapley value or a
knowledge value. The claim is evidence about the *claimant*, not about demand.
"""
from __future__ import annotations

from typing import Any, Literal

CLAIM_VERDICT_VERSION = "operator-claim-v1"

#: Reason codes the adjustment UI offers, mapped onto the causes that would
#: corroborate them. An empty set is a statement, not an omission: the claim may
#: well be true, but attribution owns no engine parameter that could express it,
#: so it has to be reported as out of scope and routed elsewhere. Scoring such a
#: claim as "wrong" would blame a store manager for a gap in the cause registry.
CLAIM_TO_CAUSES: dict[str, frozenset[str]] = {
    "SEASONAL": frozenset({"SEASONAL_SHIFT", "HOLIDAY_EFFECT"}),
    "SUBSTITUTION": frozenset({"SUBSTITUTION_TRANSFER"}),
    # No DEMAND_LEVEL calibration exists yet, so a bare demand claim cannot be
    # corroborated even when it is correct.
    "DEMAND_CHANGE": frozenset(),
    # An inventory-accuracy signal, not a demand judgement. Repeated claims here
    # point at phantom stock, which is a data-quality problem and must not be
    # absorbed into demand knowledge.
    "INVENTORY_CONSTRAINT": frozenset(),
    "OTHER": frozenset(),
}

ClaimVerdict = Literal[
    "SUPPORTED",
    "UNCALIBRATED",
    "CONTRADICTED",
    "OUT_OF_SCOPE",
    "UNVERIFIABLE",
]

#: Verdicts that mean the evidence failed to back the stated reason. Kept as a
#: set rather than inlined so the review queue and the risk flag cannot drift
#: apart from the summary statistics.
UNSUPPORTED_VERDICTS: frozenset[str] = frozenset({"UNCALIBRATED", "CONTRADICTED"})


def _calibratable(candidates: list[dict[str, Any]]) -> set[str]:
    """Causes whose candidate the engine could actually be solved to.

    ``acceptable`` is set by :mod:`attribution.proposals` only for
    ``EXACT``/``APPROXIMATE`` calibration, i.e. only when some value of the
    parameter reproduces the ordered quantity. This is the anchor the model
    cannot forge.
    """
    return {
        str(item.get("cause_code"))
        for item in candidates or []
        if item.get("acceptable")
    }


def judge_operator_claim(
    *,
    reason_code: str | None,
    applicable_causes: set[str],
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare the operator's stated reason with what the evidence supports.

    ``applicable_causes`` must already be restricted to the deterministic cause
    registry; a code the registry does not know cannot corroborate anything.
    """
    claim = str(reason_code or "").strip().upper()
    claimed = CLAIM_TO_CAUSES.get(claim)
    supported = set(applicable_causes) & _calibratable(candidates or [])
    # Causes the evidence backs that the operator never mentioned. A store
    # manager acting on a real signal they cannot name is the opposite of a
    # false claim, and the two must not be collapsed into one statistic.
    unclaimed = sorted(supported - (claimed or frozenset()))

    if claimed is None:
        verdict: ClaimVerdict = "UNVERIFIABLE"
    elif not claimed:
        verdict = "OUT_OF_SCOPE"
    elif claimed & supported:
        verdict = "SUPPORTED"
    elif claimed & set(applicable_causes):
        # The evidence pointed the right way but no value of the parameter
        # reaches the ordered quantity, so the named cause cannot be what drove
        # the size of this override even if it is real.
        verdict = "UNCALIBRATED"
    else:
        verdict = "CONTRADICTED"

    return {
        "reason_code": claim or None,
        "verdict": verdict,
        "claimed_causes": sorted(claimed or ()),
        "corroborating_causes": sorted(claimed & supported) if claimed else [],
        "unclaimed_supported_causes": unclaimed,
        "version": CLAIM_VERDICT_VERSION,
    }
