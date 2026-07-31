"""Deterministic counterfactual, substitution, and allocation primitives."""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import random
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal, get_args

from .claims import UNSUPPORTED_VERDICTS, judge_operator_claim
from .errors import SnapshotUnavailableError, ValidationError

SHAPLEY_VERSION = "shapley-v1"
SUBSTITUTION_VERSION = "substitution-v1"

# The single source of truth for the cause vocabulary. The model's structured output is
# constrained to exactly these strings (see harness.HarnessFinding) and every quantifier
# below is registered under one of them, so the two halves cannot drift apart: a code the
# model can emit but nothing can quantify -- or the reverse -- fails at import time.
CauseCode = Literal["SEASONAL_SHIFT", "HOLIDAY_EFFECT", "SUBSTITUTION_TRANSFER"]
CAUSE_CODES: tuple[str, ...] = get_args(CauseCode)
CAUSE_LABELS = {
    "zh-CN": {
        "SEASONAL_SHIFT": "季节性变化",
        "HOLIDAY_EFFECT": "节假日影响",
        "SUBSTITUTION_TRANSFER": "替代品需求转移",
    },
    "en-US": {
        "SEASONAL_SHIFT": "Seasonal Shift",
        "HOLIDAY_EFFECT": "Holiday Effect",
        "SUBSTITUTION_TRANSFER": "Substitution Transfer",
    },
}
NARRATIVE_VERSION = "narrative-v3"
NARRATIVE = {
    "zh-CN": {
        "headline": "{product}（门店 {shop}，{date}）：系统建议 {recommended} 件，实际下单 {override} 件，"
                    "相差 {gap} 件。",
        "explained": "在系统建议的 {recommended} 件中，有 {explained} 件来自这些证据：{breakdown}；"
                     "把这些证据全部去掉，系统只会建议 {bare} 件。",
        "breakdown_item": "{label} {qty} 件（占 {share}）",
        "residual": "但这些证据解释不了店长的调整：仍有 {residual} 件差异需要人工确认。",
        "residual_small": "这些证据已经解释了这次调整的绝大部分，只剩 {residual} 件差异需要人工确认。",
        "fully_explained": "代入证据重算的结果与店长的下单量一致。",
        "no_cause": "系统目前只能核对{candidates}这 {count} 类原因，"
                    "归因模型逐项核对后判断它们与这次调整都无关，因此没有任何一项能算出数量。",
        "no_effect": "归因模型认为{labels}与这次调整有关，但把{labels}从重算中去掉，"
                     "建议量仍是 {bare} 件、与系统建议的 {recommended} 件相同："
                     "{labels}并没有推动这次建议，也解释不了店长的调整。",
        "needs_review": "因此这 {gap} 件差异目前找不到系统可核验的原因，"
                        "请审核时补充说明店长这样下单的依据（例如门店促销、临期清货、周边施工、竞品缺货等）。",
        "degenerate": "提示：这些原因即使全部去掉，重算的建议量也不会变化，因此它们无法作为这次调整的依据。",
        "drift": "另需注意：用当时的快照重算得到 {baseline} 件，与记录的系统建议 {recommended} 件不一致，"
                 "该差异也计入了未解释部分。",
        "conflict": "警告：证据合计推动的方向与店长的调整方向相反，这次调整需要人工复核。",
        "direction_conflict": "警告：{labels}的证据描述方向与重算结果相反，文字说明与数量不一致。",
        "cause_heading": "各项证据说明：",
        "cause_line": "· {label}（{qty} 件）：{explanation}",
        "unquantifiable": "另需注意：{labels}被判定为适用，但系统没有对应的可量化证据数据，"
                          "因此无法计入已解释数量。",
        "unknown_cause": "警告：归因模型返回了系统无法识别的原因类型（{codes}），已忽略。",
    },
    "en-US": {
        "headline": "{product} (store {shop}, {date}): the system suggested {recommended} units, "
                    "the store manager ordered {override}, a difference of {gap} units.",
        "explained": "Of the {recommended} units the system suggested, {explained} come from "
                     "this evidence: {breakdown}. With all of it switched off the system "
                     "would have suggested {bare}.",
        "breakdown_item": "{label} {qty} units ({share})",
        "residual": "That evidence does not explain the store manager's adjustment: "
                    "{residual} units of difference still need review.",
        "residual_small": "The evidence accounts for nearly all of the adjustment; only "
                          "{residual} units still need review.",
        "fully_explained": "Replaying the evidence lands exactly on the quantity the store "
                           "manager ordered.",
        "no_cause": "The system can only check {count} kinds of cause — {candidates} — and the "
                    "attribution model judged none of them relevant to this adjustment, so "
                    "there is nothing it can quantify.",
        "no_effect": "The attribution model judged {labels} relevant, but switching {labels} off "
                     "still leaves the recommendation at {bare} units, the same {recommended} "
                     "the system suggested: {labels} did not drive this recommendation and "
                     "cannot explain the store manager's adjustment.",
        "needs_review": "So these {gap} units have no cause the system can verify. Please record "
                        "why the store manager ordered this way (a local promotion, clearing "
                        "short-dated stock, nearby construction, a competitor stockout, and so on).",
        "degenerate": "Note: switching all of these off leaves the recommended quantity unchanged, "
                      "so they cannot serve as grounds for this adjustment.",
        "drift": "Note: replaying the frozen snapshot yields {baseline} units rather than the "
                 "recorded suggestion of {recommended}; that difference is also left unexplained.",
        "conflict": "Warning: the evidence points the opposite way to the store manager's "
                    "adjustment. This case needs human review.",
        "direction_conflict": "Warning: the evidence for {labels} describes the opposite direction "
                              "to the replay result, so the wording and the numbers disagree.",
        "cause_heading": "Evidence detail:",
        "cause_line": "- {label} ({qty} units): {explanation}",
        "unquantifiable": "Note: {labels} was judged applicable but no matching quantifiable "
                          "evidence data exists, so it contributes nothing to the explained "
                          "amount.",
        "unknown_cause": "Warning: the attribution model returned unrecognised cause types "
                         "({codes}), which were ignored.",
    },
}


def _format_qty(value: float) -> str:
    """Render allocation amounts as signed units, dropping noise from float arithmetic."""
    rounded = round(float(value), 2)
    if abs(rounded - round(rounded)) < 0.005:
        return f"{int(round(rounded)):+d}"
    return f"{rounded:+.2f}"


def _compose_summary(
    *,
    language: str,
    snapshot: dict,
    recommended_qty: int,
    override_qty: int,
    baseline_qty: int,
    bare_baseline_qty: int,
    signed_gap: float,
    unexplained: float,
    allocation_rows: list[dict[str, Any]],
    candidate_labels: list[str],
    evidence_is_informative: bool,
    has_conflict: bool,
    direction_conflicts: list[str],
    unquantifiable_labels: list[str],
    unknown_cause_codes: list[str],
) -> str:
    """Narrate the finished report.

    The model writes its findings before any quantity exists, so it can never state what a
    cause was worth. Composing the store-facing text here instead means every number in the
    narrative is the conserved number shown in the table.
    """
    text = NARRATIVE.get(language, NARRATIVE["zh-CN"])
    joiner = "；" if language == "zh-CN" else "; "
    list_joiner = "、" if language == "zh-CN" else ", "
    sku_info = snapshot.get("sku_info", {})
    product = " · ".join(part for part in (
        sku_info.get("goods_name"), sku_info.get("goods_code")) if part) or "-"
    lines = [text["headline"].format(
        product=product,
        shop=snapshot.get("shop_name") or snapshot.get("shop") or "-",
        date=snapshot.get("decision_date", "-"),
        recommended=recommended_qty, override=override_qty, gap=_format_qty(signed_gap),
    )]

    explained = sum(float(row["signed_contribution_qty"]) for row in allocation_rows)
    contributing = [row for row in allocation_rows
                    if abs(float(row["signed_contribution_qty"])) >= 0.005]
    if contributing:
        lines.append(text["explained"].format(
            recommended=recommended_qty, bare=bare_baseline_qty,
            explained=_format_qty(explained),
            breakdown=joiner.join(
                text["breakdown_item"].format(
                    label=row["label"], qty=_format_qty(row["signed_contribution_qty"]),
                    share=f"{row['absolute_contribution_weight'] * 100:.0f}%",
                )
                for row in contributing
            ),
        ))
        # A residual is only a failure when it is large next to what was explained.
        # Treating a six-unit remainder on a 144-unit gap as "the evidence does not
        # explain this" contradicts the breakdown printed directly above it.
        if abs(unexplained) < 0.005:
            lines.append(text["fully_explained"])
        else:
            explained_scale = max(abs(explained), abs(signed_gap))
            minor = explained_scale > 0 and abs(unexplained) / explained_scale <= 0.1
            lines.append(text["residual_small" if minor else "residual"].format(
                residual=_format_qty(unexplained)))
    elif not allocation_rows:
        lines.append(text["no_cause"].format(
            candidates=list_joiner.join(candidate_labels), count=len(candidate_labels)))
        lines.append(text["needs_review"].format(gap=_format_qty(signed_gap)))
    else:
        labels = list_joiner.join(row["label"] for row in allocation_rows)
        lines.append(text["no_effect"].format(
            labels=labels, bare=bare_baseline_qty, recommended=recommended_qty))
        lines.append(text["needs_review"].format(gap=_format_qty(signed_gap)))

    # Only worth saying when the numbers above did not already say it: quantified evidence
    # that nonetheless moves nothing is the one case the breakdown reads as meaningful.
    if contributing and not evidence_is_informative:
        lines.append(text["degenerate"])
    if baseline_qty != recommended_qty:
        lines.append(text["drift"].format(baseline=baseline_qty, recommended=recommended_qty))
    if has_conflict:
        lines.append(text["conflict"])
    if direction_conflicts:
        lines.append(text["direction_conflict"].format(
            labels="、".join(direction_conflicts) if language == "zh-CN"
            else ", ".join(direction_conflicts)))
    # A cause the model asserted but nothing could measure used to vanish without trace,
    # leaving "no evidence applied" and "we had no data to score the evidence" identical
    # on screen. They call for opposite follow-up, so they are now said out loud.
    if unquantifiable_labels:
        lines.append(text["unquantifiable"].format(
            labels="、".join(unquantifiable_labels) if language == "zh-CN"
            else ", ".join(unquantifiable_labels)))
    if unknown_cause_codes:
        lines.append(text["unknown_cause"].format(codes=", ".join(unknown_cause_codes)))
    detail = [
        text["cause_line"].format(
            label=row["label"], qty=_format_qty(row["signed_contribution_qty"]),
            explanation=row["explanation"],
        )
        for row in allocation_rows if row.get("explanation")
    ]
    if detail:
        lines.append(text["cause_heading"])
        lines.extend(detail)
    return "\n".join(lines)


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def snapshot_hash(snapshot: dict) -> str:
    return hashlib.sha256(canonical_json(snapshot).encode("utf-8")).hexdigest()


class SeedRepository:
    """Readonly, versioned P0 evidence from package JSON files."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(__file__).with_name("seeds")

    def load(self, name: str) -> dict:
        path = self.root / f"{name}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data.get("version"), str):
            raise ValidationError(f"seed {name} has no version")
        return data


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def substitution_target_daily_delta(
    *,
    relationship_direction: float,
    transfer_rate: float,
    substitute_reconstructed_daily_demand: float,
    substitute_reorder_point: float,
    substitute_available_position: float,
    target_true_daily_demand: float,
    max_transfer_ratio: float,
) -> dict:
    """Implement the approved demand-transfer formula with bidirectional clipping."""
    pressure = clamp(
        (substitute_reorder_point - substitute_available_position) /
        max(substitute_reorder_point, 1),
        -1,
        1,
    )
    raw = (relationship_direction * transfer_rate * substitute_reconstructed_daily_demand *
           pressure)
    cap = abs(target_true_daily_demand) * max_transfer_ratio
    delta = clamp(raw, -cap, cap)
    return {
        "version": SUBSTITUTION_VERSION,
        "inventory_pressure": pressure,
        "raw_target_daily_delta": raw,
        "target_daily_delta": delta,
        "max_abs_delta": cap,
    }


def replay_engine(snapshot: dict, *, factor_overrides: dict[str, float] | None = None,
                  target_daily_demand_delta: float | None = None) -> dict:
    """Replay a frozen recommendation snapshot; never consult current live data.

    Knowledge is read from the snapshot rather than resolved live: an entry
    approved after the decision was taken must not change what that decision was,
    or every counterfactual anchored on the replay would drift as the knowledge
    base grows.
    """
    required = {"shop", "sku_info", "forecast", "decision_date"}
    if not required <= set(snapshot):
        raise SnapshotUnavailableError(
            "historical snapshot lacks deterministic replay inputs",
            details={"missing": sorted(required - set(snapshot))},
        )
    from engine import run  # lazily preserve independent deterministic API startup

    decision_date = datetime.fromisoformat(str(snapshot["decision_date"])).date()
    return run(
        snapshot["shop"], snapshot["sku_info"], snapshot["forecast"], decision_date,
        inventory_snapshot=snapshot.get("inventory_snapshot"),
        flow=snapshot.get("flow", "A"),
        on_promo=snapshot.get("on_promo"),
        params=snapshot.get("params"),
        fill_rate=snapshot.get("fill_rate"),
        factor_overrides=factor_overrides,
        target_daily_demand_delta=target_daily_demand_delta,
        knowledge=snapshot.get("knowledge_applied"),
    )


def _seed_int(case_id: str) -> int:
    return int.from_bytes(hashlib.sha256(case_id.encode("utf-8")).digest()[:8], "big")


def shapley_values(
    cause_codes: list[str],
    value: Callable[[frozenset[str]], float],
    *,
    case_id: str,
) -> tuple[dict[str, float], dict]:
    """Allocate any coalition value exactly through ten causes, else deterministically."""
    if len(set(cause_codes)) != len(cause_codes):
        raise ValidationError("cause codes must be unique")
    n = len(cause_codes)
    if n == 0:
        return {}, {"version": SHAPLEY_VERSION, "method": "exact", "sample_count": 0}
    allocations = {cause: 0.0 for cause in cause_codes}
    if n <= 10:
        factorial = math.factorial
        denominator = factorial(n)
        all_codes = frozenset(cause_codes)
        for cause in cause_codes:
            others = all_codes - {cause}
            for r in range(n):
                weight = factorial(r) * factorial(n - r - 1) / denominator
                for subset in itertools.combinations(others, r):
                    coalition = frozenset(subset)
                    allocations[cause] += weight * (value(coalition | {cause}) - value(coalition))
        metadata = {"version": SHAPLEY_VERSION, "method": "exact", "sample_count": 0}
    else:
        samples = 2048
        rng = random.Random(_seed_int(case_id))
        for _ in range(samples):
            permutation = list(cause_codes)
            rng.shuffle(permutation)
            coalition: frozenset[str] = frozenset()
            before = value(coalition)
            for cause in permutation:
                coalition = coalition | {cause}
                after = value(coalition)
                allocations[cause] += after - before
                before = after
        allocations = {key: amount / samples for key, amount in allocations.items()}
        metadata = {
            "version": SHAPLEY_VERSION, "method": "deterministic_permutation_sample",
            "sample_count": samples, "seed": _seed_int(case_id), "error_estimate": None,
        }
    return allocations, metadata


def conserve(signed_gap: float, contributions: dict[str, float], *,
             anchored_total: float | None = None) -> dict:
    """Check the allocation adds up against the total it is entitled to explain.

    ``anchored_total`` is that total when the decomposition is anchored somewhere
    other than the recommendation — the counterfactual measures each cause from a
    world where the questioned assumptions are neutral, so what the allocations
    sum to is the assumption-driven part of the order, not the store manager's
    gap. Omitting it keeps the older behaviour for manually written reports,
    whose reviewer-entered causes are stated directly against that gap.
    """
    total_to_explain = signed_gap if anchored_total is None else float(anchored_total)
    allocated = sum(contributions.values())
    unexplained = total_to_explain - allocated
    absolute_total = abs(total_to_explain)
    coverage = 0.0 if absolute_total == 0 else sum(
        abs(v) for v in contributions.values()) / absolute_total
    return {
        "signed_gap": signed_gap,
        "anchored_total_qty": total_to_explain,
        "allocated_signed_qty": allocated,
        "unexplained_signed_gap": unexplained,
        "coverage_ratio": coverage,
        "conservation_version": "conservation-v2",
    }


def _seasonal_factor(snapshot: dict, seed: dict) -> float | None:
    sku = snapshot["sku_info"]
    month = str(datetime.fromisoformat(str(snapshot["decision_date"])).month)
    sku_factor = seed.get("sku", {}).get(str(sku.get("goods_code")), {}).get(month)
    category_factor = seed.get("category", {}).get(str(sku.get("category")), {}).get(month)
    value = sku_factor if sku_factor is not None else category_factor
    return float(value) if value is not None and float(value) != 1.0 else None


def _holiday_factor(snapshot: dict, seed: dict) -> float | None:
    decision_date = datetime.fromisoformat(str(snapshot["decision_date"]))
    value = seed.get("dates", {}).get(decision_date.strftime("%m-%d"))
    return float(value) if value is not None and float(value) != 1.0 else None


def substitution_context(snapshot: dict, seed: dict) -> dict | None:
    """Return the relationship and frozen evidence behind the substitution cause.

    Knowledge calibration has to re-run the transfer formula at a *different*
    rate than the seed's, so the inputs cannot stay private to the delta
    computation below: both callers read the same resolved pair or they would
    disagree about which substitute the case is even about.
    """
    target = str(snapshot["sku_info"].get("goods_code"))
    evidence_by_sku = snapshot.get("substitution_evidence", {})
    for relationship in seed.get("relationships", []):
        if str(relationship.get("target_goods_code")) != target:
            continue
        substitute = str(relationship.get("substitute_goods_code"))
        evidence = evidence_by_sku.get(substitute)
        if not isinstance(evidence, dict):
            continue
        return {"relationship": relationship, "evidence": evidence,
                "substitute_goods_code": substitute}
    return None


def substitution_delta_at_rate(context: dict, transfer_rate: float) -> dict:
    """Re-run the approved transfer formula with a substituted transfer rate."""
    relationship, evidence = context["relationship"], context["evidence"]
    return substitution_target_daily_delta(
        relationship_direction=float(relationship.get("relationship_direction", 1)),
        transfer_rate=float(transfer_rate),
        substitute_reconstructed_daily_demand=float(
            evidence["substitute_reconstructed_daily_demand"]),
        substitute_reorder_point=float(evidence["substitute_reorder_point"]),
        substitute_available_position=float(evidence["substitute_available_position"]),
        target_true_daily_demand=float(evidence["target_true_daily_demand"]),
        max_transfer_ratio=float(relationship.get("max_transfer_ratio", 0.25)),
    )


def _substitution_delta(snapshot: dict, seed: dict) -> tuple[float, dict] | None:
    context = substitution_context(snapshot, seed)
    if context is None:
        return None
    result = substitution_delta_at_rate(
        context, float(context["relationship"].get("transfer_rate", 0)))
    return float(result["target_daily_delta"]), {
        "substitute_goods_code": context["substitute_goods_code"], **result,
    }


def substitute_codes_for_target(goods_code: str, seed: dict) -> list[str]:
    """List substitutes whose evidence a snapshot must freeze for this target SKU.

    Snapshot construction and attribution replay must agree on which substitutes matter,
    so both read the relationship seed through this one helper.
    """
    target = str(goods_code)
    codes: list[str] = []
    for relationship in seed.get("relationships", []):
        if str(relationship.get("target_goods_code")) != target:
            continue
        substitute = str(relationship.get("substitute_goods_code"))
        if substitute not in codes:
            codes.append(substitute)
    return codes


def _resolve_seasonal(snapshot: dict, seeds: SeedRepository) -> tuple[dict, dict] | None:
    seed = seeds.load("seasonality")
    factor = _seasonal_factor(snapshot, seed)
    if factor is None:
        return None
    return {"factor_name": "season", "factor": factor}, {
        "evidence_id": "seasonality-seed", "evidence_type": "SEASONAL_FACTOR",
        "title": "Versioned seasonal factor", "source": "readonly-seed",
        "source_version": seed["version"], "observed_at": snapshot["decision_date"],
        "fresh": True, "payload": {"factor": factor},
    }


def _resolve_holiday(snapshot: dict, seeds: SeedRepository) -> tuple[dict, dict] | None:
    seed = seeds.load("holidays")
    factor = _holiday_factor(snapshot, seed)
    if factor is None:
        return None
    return {"factor_name": "holiday", "factor": factor}, {
        "evidence_id": "holiday-seed", "evidence_type": "HOLIDAY_FACTOR",
        "title": "Versioned holiday factor", "source": "readonly-seed",
        "source_version": seed["version"], "observed_at": snapshot["decision_date"],
        "fresh": True, "payload": {"factor": factor},
    }


def _resolve_substitution(snapshot: dict, seeds: SeedRepository) -> tuple[dict, dict] | None:
    seed = seeds.load("substitutions")
    resolved = _substitution_delta(snapshot, seed)
    if resolved is None:
        return None
    delta, details = resolved
    return {"target_daily_demand_delta": delta}, {
        "evidence_id": "substitution-seed", "evidence_type": "SUBSTITUTION_RELATIONSHIP",
        "title": "Versioned substitution relationship", "source": "readonly-seed",
        "source_version": seed["version"], "observed_at": snapshot["decision_date"],
        "fresh": True, "payload": details,
    }


# Registered under the same literals the model is constrained to emit. The equality check
# below is the guard: adding a code to CauseCode without a quantifier (or vice versa) is
# an ImportError, not a silently empty attribution report in production.
CAUSE_RESOLVERS: dict[str, Callable[[dict, SeedRepository], tuple[dict, dict] | None]] = {
    "SEASONAL_SHIFT": _resolve_seasonal,
    "HOLIDAY_EFFECT": _resolve_holiday,
    "SUBSTITUTION_TRANSFER": _resolve_substitution,
}
if set(CAUSE_RESOLVERS) != set(CAUSE_CODES):  # pragma: no cover - import-time invariant
    raise RuntimeError(
        "cause vocabulary drift: "
        f"{sorted(set(CAUSE_CODES) ^ set(CAUSE_RESOLVERS))} lacks a resolver or a literal"
    )


def build_attribution_report(
    case: dict[str, Any],
    model_output: dict[str, Any],
    *,
    seeds: SeedRepository | None = None,
) -> dict[str, Any]:
    """Convert model applicability findings into deterministic, conserved quantities."""
    snapshot = case["snapshot"]
    output_language = str(case.get("output_language") or "zh-CN")
    cause_labels = CAUSE_LABELS.get(output_language, CAUSE_LABELS["zh-CN"])
    recommended_qty = int(case["recommended_qty"])
    override_qty = int(case["override_qty"])
    # Anchor every counterfactual on the engine's own reproduction of the advice.
    # A cause is then measured as "evidence disagrees with what the engine already
    # assumed", which is the only quantity that can explain an operator override.
    # Anchoring on an all-factors-neutral world instead makes Shapley conserve to
    # a total that has nothing to do with signed_gap.
    baseline_qty = int(replay_engine(snapshot)["final_qty"])
    seeds = seeds or SeedRepository()
    finding_by_code = {
        str(item.get("cause_code")): item
        for item in model_output.get("findings", [])
        if item.get("applicable")
    }
    cause_values: dict[str, dict[str, Any]] = {}
    evidence: list[dict[str, Any]] = []
    # A code outside the registry can no longer be produced by the constrained model, but a
    # hand-written or replayed payload still can, and dropping it silently is what made
    # every historical report explain nothing without saying so.
    unknown_cause_codes = sorted(set(finding_by_code) - set(CAUSE_CODES))
    unquantifiable_causes: list[str] = []
    for code in CAUSE_CODES:
        resolved = CAUSE_RESOLVERS[code](snapshot, seeds)
        if code not in finding_by_code:
            continue
        if resolved is None:
            unquantifiable_causes.append(code)
            continue
        value, evidence_item = resolved
        cause_values[code] = value
        evidence.append(evidence_item)

    cause_codes = list(cause_values)
    factor_causes = {
        code: value for code, value in cause_values.items() if "factor_name" in value
    }
    # Counterfactuals measure each named cause from a world where *only* the named
    # causes are neutral. Anchoring on the engine's own reproduction instead made
    # every seasonal and holiday cause score a structural zero: the coalition
    # replay re-asserted the very factor the engine had already applied, so the
    # difference was zero by construction and the two causes could never be
    # attributed anything. Assumptions nobody questioned are deliberately left
    # alone -- they are inputs to this decision, not part of what is decomposed.
    neutral_factors = {value["factor_name"]: 1.0 for value in factor_causes.values()}
    has_demand_cause = any(
        "target_daily_demand_delta" in value for value in cause_values.values())
    bare_baseline_qty = int(replay_engine(
        snapshot, factor_overrides=neutral_factors or None,
        target_daily_demand_delta=0.0 if has_demand_cause else None,
    )["final_qty"])
    # Shapley asks for the same coalition repeatedly -- n*2^n evaluations over only
    # 2^n distinct sets -- and every miss is a full engine replay, so memoise them.
    replay_cache: dict[frozenset[str], int] = {frozenset(): bare_baseline_qty}

    def replay_for(coalition: frozenset[str]) -> int:
        """Replay one coalition; named causes outside it are switched off."""
        cached = replay_cache.get(coalition)
        if cached is not None:
            return cached
        overrides = {
            value["factor_name"]: (value["factor"] if code in coalition else 1.0)
            for code, value in factor_causes.items()
        }
        demand_delta = sum(
            value.get("target_daily_demand_delta", 0.0)
            for code, value in cause_values.items() if code in coalition
        ) if has_demand_cause else None
        replay = replay_engine(
            snapshot, factor_overrides=overrides or None,
            target_daily_demand_delta=demand_delta,
        )
        replay_cache[coalition] = int(replay["final_qty"])
        return replay_cache[coalition]

    allocations, shapley = shapley_values(
        cause_codes,
        lambda coalition: float(replay_for(coalition) - bare_baseline_qty),
        case_id=case["case_id"],
    )
    attributed_qty = replay_for(frozenset(cause_codes))
    explained_signed_qty = float(attributed_qty - bare_baseline_qty)
    # Efficiency is exact for the enumerated branch; the sampled branch can only
    # approximate it, and that violation is precisely the allocation's error.
    shapley["error_estimate"] = explained_signed_qty - sum(allocations.values())
    # Evidence that moves nothing even against a neutral world explains nothing,
    # however confidently the model asserted it.
    evidence_is_informative = bool(cause_codes) and (
        attributed_qty != bare_baseline_qty
        or any(replay_for(frozenset({code})) != bare_baseline_qty for code in cause_codes)
    )
    signed_gap = override_qty - recommended_qty
    conservation = conserve(signed_gap, allocations,
                            anchored_total=float(override_qty - bare_baseline_qty))
    allocation_rows = []
    total_abs = sum(abs(value) for value in allocations.values())
    # The residual is part of the story, so it belongs in the denominator: without
    # it a cause that explains 1 of a 50 unit gap still renders as "100%".
    weight_base = total_abs + abs(float(conservation["unexplained_signed_gap"]))
    for code in cause_codes:
        finding = finding_by_code.get(code, {})
        solo_qty = replay_for(frozenset({code}))
        allocation_rows.append({
            "cause_code": code,
            "domain": "substitution" if code == "SUBSTITUTION_TRANSFER" else "seasonality",
            "label": cause_labels.get(code, code.replace("_", " ").title()),
            "signed_contribution_qty": allocations[code],
            "absolute_contribution_weight": abs(allocations[code]) / weight_base if weight_base else 0.0,
            "expected_direction": str(finding.get("expected_direction", "NONE")),
            "explanation": finding.get(
                "explanation", "Deterministic replay of versioned evidence."),
            "evidence_refs": finding.get("evidence_refs", []) or [
                item["evidence_id"] for item in evidence
                if item["evidence_id"].startswith(code.split("_", 1)[0].lower())
            ],
            "counterfactual_result": {
                "cause_code": code, "baseline_qty": bare_baseline_qty,
                "counterfactual_qty": solo_qty,
                "signed_impact_qty": solo_qty - bare_baseline_qty,
                "inputs": cause_values[code],
            },
        })
    # The model asserts a direction in words before any quantity exists; replay computes the
    # real one. Surfacing a mismatch stops a fluent explanation from contradicting its own row.
    direction_conflicts = [
        row["label"] for row in allocation_rows
        if row["expected_direction"] in {"INCREASE", "DECREASE"}
        and abs(float(row["signed_contribution_qty"])) >= 0.005
        and (float(row["signed_contribution_qty"]) > 0) != (row["expected_direction"] == "INCREASE")
    ]
    has_conflict = bool(signed_gap and sum(allocations.values()) * signed_gap < 0)
    risk_flags = []
    if abs(float(conservation["unexplained_signed_gap"])) > 1e-9:
        risk_flags.append("UNEXPLAINED_RESIDUAL")
    if baseline_qty != recommended_qty:
        risk_flags.append("SNAPSHOT_REPLAY_DRIFT")
    if cause_codes and not evidence_is_informative:
        risk_flags.append("EVIDENCE_MATCHES_BASELINE")
    if direction_conflicts:
        risk_flags.append("DIRECTION_CONTRADICTS_EVIDENCE")
    if unknown_cause_codes:
        risk_flags.append("UNKNOWN_CAUSE_CODE")
    if unquantifiable_causes:
        risk_flags.append("EVIDENCE_UNAVAILABLE_FOR_CAUSE")
    model_summary = str(model_output.get("summary") or "").strip()
    # Imported here rather than at module scope: proposals builds on the replay
    # primitives above, so a top-level import would close the cycle.
    from .proposals import build_knowledge_candidates

    knowledge_candidates = build_knowledge_candidates(
        case, model_output, baseline_qty=baseline_qty, seeds=seeds)
    # The model named causes but none of them, at any value the engine accepts,
    # reproduces what the store manager ordered. That is a finding in itself:
    # whatever drove this override is outside the parameters attribution can move.
    if knowledge_candidates and not any(
            item["acceptable"] for item in knowledge_candidates):
        risk_flags.append("NO_CALIBRATABLE_CANDIDATE")
    # What the store manager said they were doing, checked against what the
    # evidence supports. Scored here rather than by the model, which is shown the
    # claim and would only be grading its own anchor. The verdict is reported and
    # counted; it never feeds an allocation, a Shapley value or a knowledge value.
    operator_claim = judge_operator_claim(
        reason_code=case.get("reason_code"),
        applicable_causes=set(finding_by_code) & set(CAUSE_CODES),
        candidates=knowledge_candidates,
    )
    if operator_claim["verdict"] in UNSUPPORTED_VERDICTS:
        risk_flags.append("OPERATOR_CLAIM_UNSUPPORTED")
    return {
        "summary": _compose_summary(
            language=output_language, snapshot=snapshot,
            recommended_qty=recommended_qty, override_qty=override_qty,
            baseline_qty=baseline_qty, bare_baseline_qty=bare_baseline_qty,
            signed_gap=signed_gap,
            unexplained=float(conservation["unexplained_signed_gap"]),
            allocation_rows=allocation_rows,
            candidate_labels=[cause_labels.get(code, code) for code in CAUSE_CODES],
            evidence_is_informative=evidence_is_informative,
            has_conflict=has_conflict, direction_conflicts=direction_conflicts,
            unquantifiable_labels=[cause_labels.get(code, code)
                                   for code in unquantifiable_causes],
            unknown_cause_codes=unknown_cause_codes,
        ),
        # Kept verbatim for review and trace comparison, but never shown as the headline: it is
        # written before the quantities exist and so cannot reference them.
        "model_summary": model_summary,
        "primary_cause": max(allocations, key=lambda code: abs(allocations[code]))
        if total_abs else None,
        "allocations": allocation_rows,
        "evidence": evidence,
        # The reviewable half of the report: propositions about what the engine
        # should believe next time, each calibrated against what the store
        # manager actually ordered.
        "knowledge_candidates": knowledge_candidates,
        # The operator's stated reason, graded against the evidence. Reported for
        # review and tallied per reason code; it moves no quantity.
        "operator_claim": operator_claim,
        **conservation,
        "recommended_qty": recommended_qty,
        "override_qty": override_qty,
        "baseline_qty": baseline_qty,
        # What the engine would have ordered with the questioned assumptions
        # switched off, and therefore the point every counterfactual is measured
        # from and the total the allocations are required to conserve to.
        "bare_baseline_qty": bare_baseline_qty,
        "conservation_anchor_qty": bare_baseline_qty,
        "attributed_qty": attributed_qty,
        "explained_signed_qty": explained_signed_qty,
        "replay_drift_qty": baseline_qty - recommended_qty,
        "shapley": shapley,
        "risk_flags": risk_flags,
        "unknown_cause_codes": unknown_cause_codes,
        "unquantifiable_cause_codes": unquantifiable_causes,
        "conflicts": ["ATTRIBUTION_DIRECTION_CONFLICT"] if has_conflict else [],
        "partial": bool(model_output.get("partial", False)),
        "decomposition_version": "bare-baseline-v2",
        "narrative_version": NARRATIVE_VERSION,
        "report_version": "deterministic-attribution-v3",
    }
