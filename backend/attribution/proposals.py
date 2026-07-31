"""Turn one override into a reviewable, replayable knowledge candidate.

An allocation row answers "what happened this once" and dies with the case. A
knowledge candidate answers "what should the engine believe from now on", which
is the only shape a reviewer can accept or reject on its merits and the only
shape the engine can later replay to find out whether it was right.

Every candidate is derived here, never written by the model. The model says
*which* condition applies and *when* it should fire again; this module solves
the engine for the parameter value that would have produced the quantity the
store manager actually ordered. Keeping the proposition and its number on the
same side of that line is what stops a fluent narrative from contradicting the
table beside it -- the defect that made the allocation-only report unreviewable,
where a confident seasonal paragraph sat next to a zero-unit contribution
because the engine had already applied the very factor being "discovered".

Calibrating against the human's quantity also removes that degeneracy by
construction: a candidate is the amount by which the engine's assumption has to
*change*, so it can never come out as "the engine already assumed this".
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Callable, Literal

from .deterministic import (
    CAUSE_CODES,
    SeedRepository,
    replay_engine,
    substitution_context,
    substitution_delta_at_rate,
)

PROPOSAL_VERSION = "knowledge-candidate-v1"

#: Which knowledge kind each cause proposes. A cause with no kind cannot produce
#: a candidate, and a kind with no engine target cannot be replayed, so the two
#: registries below are checked against each other at import time.
CAUSE_KNOWLEDGE_KIND: dict[str, str] = {
    "SEASONAL_SHIFT": "SEASONAL_FACTOR",
    "HOLIDAY_EFFECT": "HOLIDAY_FACTOR",
    "SUBSTITUTION_TRANSFER": "SUBSTITUTION_RATE",
}
if set(CAUSE_KNOWLEDGE_KIND) != set(CAUSE_CODES):  # pragma: no cover - import-time invariant
    raise RuntimeError(
        "cause vocabulary drift: "
        f"{sorted(set(CAUSE_CODES) ^ set(CAUSE_KNOWLEDGE_KIND))} lacks a knowledge kind"
    )

#: Search ranges for calibration. Factors are multiplicative on demand; transfer
#: rate is a share of the substitute's demand and so cannot exceed one.
FACTOR_BOUNDS = (0.05, 5.0)
RATE_BOUNDS = (0.0, 1.0)

#: How far a calibrated value may move from the engine's own before the
#: proposition stops being credible. Calibration is exact arithmetic, but exact
#: arithmetic will happily conclude that a store's summer beer factor should be
#: 4.6 when the real driver was a one-off display. Flagging the magnitude puts
#: that judgement in front of the reviewer instead of burying it in a decimal.
PLAUSIBLE_RATIO = {
    "SEASONAL_FACTOR": (0.5, 2.0),
    "HOLIDAY_FACTOR": (0.5, 2.0),
    "SUBSTITUTION_RATE": (0.33, 3.0),
}

CalibrationStatus = Literal["EXACT", "APPROXIMATE", "UNREACHABLE", "ALREADY_CORRECT"]

SCOPE_LABELS: tuple[str, ...] = ("SHOP_SKU", "SHOP_CATEGORY", "SKU", "CATEGORY")

STATEMENTS = {
    "zh-CN": {
        "SEASONAL_FACTOR": "{scope}：{window}的季节系数建议从 {prior} 调整为 {proposed}。",
        "HOLIDAY_FACTOR": "{scope}：{window}的节假日系数建议从 {prior} 调整为 {proposed}。",
        "SUBSTITUTION_RATE": "{scope}：与「{substitute}」之间的需求转移率建议从 {prior} "
                             "调整为 {proposed}。",
        "scope_shop_sku": "门店 {shop} · {product}",
        "scope_shop_category": "门店 {shop} · {category} 品类",
        "scope_sku": "全部门店 · {product}",
        "scope_category": "全部门店 · {category} 品类",
        "window_months": "{start} 至 {end}",
        "window_date": "{date}",
        "window_always": "长期",
        "effect": "按此重算，系统建议量从 {baseline} 件变为 {achieved} 件，与店长实际下单的 "
                  "{target} 件一致。",
        "effect_approximate": "按此重算，系统建议量从 {baseline} 件变为 {achieved} 件，"
                              "最接近店长实际下单的 {target} 件（受整箱与取整限制无法完全对齐）。",
        "effect_unreachable": "即使调整到边界值 {proposed}，重算也只能得到 {achieved} 件，"
                              "达不到店长实际下单的 {target} 件，因此这一条无法单独解释本次调整。",
        "effect_already": "引擎当前取值已能得出店长实际下单的 {target} 件，无需调整。",
        "implausible": "注意：该取值相对引擎现值变动了 {ratio} 倍，幅度偏大。把这次差异全部归到"
                       "这一条原因上可能并不成立，请确认真实驱动因素是否在别处。",
        "no_relationship": "系统中没有登记该单品的替代关系，无法据此提出转移率知识。",
    },
    "en-US": {
        "SEASONAL_FACTOR": "{scope}: the seasonal factor for {window} should move from "
                           "{prior} to {proposed}.",
        "HOLIDAY_FACTOR": "{scope}: the holiday factor for {window} should move from "
                          "{prior} to {proposed}.",
        "SUBSTITUTION_RATE": "{scope}: the demand transfer rate against \"{substitute}\" "
                             "should move from {prior} to {proposed}.",
        "scope_shop_sku": "Store {shop} - {product}",
        "scope_shop_category": "Store {shop} - {category} category",
        "scope_sku": "All stores - {product}",
        "scope_category": "All stores - {category} category",
        "window_months": "{start} to {end}",
        "window_date": "{date}",
        "window_always": "all year",
        "effect": "Replayed at this value the suggestion moves from {baseline} to {achieved} "
                  "units, matching the {target} the store manager ordered.",
        "effect_approximate": "Replayed at this value the suggestion moves from {baseline} to "
                              "{achieved} units, the closest reachable to the {target} the store "
                              "manager ordered (case-pack rounding prevents an exact match).",
        "effect_unreachable": "Even at the boundary value {proposed} the replay only reaches "
                              "{achieved} units, short of the {target} the store manager ordered, "
                              "so this cause cannot explain the override on its own.",
        "effect_already": "The engine's current value already reproduces the {target} units the "
                          "store manager ordered, so no change is proposed.",
        "implausible": "Note: this is a {ratio}x move against the engine's current value. "
                       "Attributing the whole difference to this one cause may not hold; check "
                       "whether the real driver lies elsewhere.",
        "no_relationship": "No substitution relationship is registered for this product, so no "
                           "transfer-rate knowledge can be proposed.",
    },
}


def _text(language: str) -> dict[str, str]:
    return STATEMENTS.get(language, STATEMENTS["zh-CN"])


def _format_value(value: float) -> str:
    rounded = round(float(value), 4)
    if abs(rounded - round(rounded, 2)) < 1e-9:
        return f"{rounded:.2f}"
    return f"{rounded:.4f}"


def solve_for_quantity(
    evaluate: Callable[[float], int],
    *,
    prior: float,
    target_qty: int,
    lower: float,
    upper: float,
    iterations: int = 40,
) -> tuple[float, int, CalibrationStatus]:
    """Find the parameter value at which the engine would have ordered target_qty.

    The engine's output is a step function of any single parameter -- case packs
    and integer rounding see to that -- so the answer is a whole interval rather
    than a point. We return the endpoint nearest the prior, which makes the
    candidate the *smallest* change consistent with what the store manager did
    instead of an arbitrary point inside the plateau.
    """
    cache: dict[float, int] = {}

    def probe_raw(value: float) -> int:
        key = round(value, 6)
        if key not in cache:
            cache[key] = int(evaluate(key))
        return cache[key]

    at_prior = probe_raw(prior)
    if at_prior == target_qty:
        return round(float(prior), 4), at_prior, "ALREADY_CORRECT"

    # Mirror the axis when the engine responds inversely, so the bisection below
    # only ever has to handle a non-decreasing step function.
    sign = 1.0 if probe_raw(upper) >= probe_raw(lower) else -1.0
    u_lower, u_upper = sorted((sign * lower, sign * upper))
    u_prior = min(max(sign * prior, u_lower), u_upper)

    def probe(u: float) -> int:
        return probe_raw(sign * u)

    if target_qty > at_prior:
        if probe(u_upper) < target_qty:
            boundary = round(sign * u_upper, 4)
            return boundary, probe_raw(boundary), "UNREACHABLE"
        low, high = u_prior, u_upper
        for _ in range(iterations):
            middle = (low + high) / 2
            if probe(middle) >= target_qty:
                high = middle
            else:
                low = middle
        value = round(sign * high, 4)
    else:
        if probe(u_lower) > target_qty:
            boundary = round(sign * u_lower, 4)
            return boundary, probe_raw(boundary), "UNREACHABLE"
        low, high = u_lower, u_prior
        for _ in range(iterations):
            middle = (low + high) / 2
            if probe(middle) <= target_qty:
                low = middle
            else:
                high = middle
        value = round(sign * low, 4)

    achieved = probe_raw(value)
    return value, achieved, "EXACT" if achieved == target_qty else "APPROXIMATE"


def _magnitude_ratio(kind: str, prior: float, proposed: float) -> tuple[float | None, bool]:
    """How far the calibrated value moved, and whether that is still credible.

    A zero prior has no ratio -- a transfer rate rising from "no transfer at all"
    is a change in kind, not in degree -- so it is reported as unbounded rather
    than dividing by zero or silently passing.
    """
    low, high = PLAUSIBLE_RATIO.get(kind, (0.5, 2.0))
    if prior == 0:
        return None, proposed == 0
    ratio = round(float(proposed) / float(prior), 3)
    return ratio, low <= ratio <= high


def _decision_date(snapshot: dict) -> date:
    return datetime.fromisoformat(str(snapshot["decision_date"])).date()


def _engine_factors(category: str, on: date, knowledge: Any = None) -> tuple[float, float]:
    """Read the factors the engine itself would apply; the seed is only evidence.

    Knowledge frozen into the snapshot is folded in, because once an entry is
    active the engine's real prior is the blended value. Reading the raw seed
    instead would show the reviewer a "from" number the engine had already
    stopped using, and calibrate the proposal against it.
    """
    from skills.soft.factors import holiday_factor, season_factor  # lazy: see replay_engine
    season = float(season_factor(category, on)["factor"])
    holiday = float(holiday_factor(on)["factor"])
    for directive in knowledge or []:
        value = directive.get("value")
        if value is None:
            continue
        if directive.get("target") == "factor_overrides.season":
            season = float(value)
        elif directive.get("target") == "factor_overrides.holiday":
            holiday = float(value)
    return season, holiday


def _season_month_span(category: str, reference: date, factor: float) -> tuple[date, date]:
    """The contiguous run of months over which the engine applies this factor.

    A seasonal correction that is only allowed to apply on the single day it was
    observed is not knowledge, it is a patch. Widening it to the engine's own
    season boundary is defensible because that is exactly the stretch the
    corrected value would replace.
    """
    from skills.soft.factors import season_factor  # lazy: see replay_engine

    def factor_on(month_start: date) -> float:
        return float(season_factor(category, month_start.replace(day=15))["factor"])

    start = reference.replace(day=1)
    for _ in range(11):
        previous = (start - timedelta(days=1)).replace(day=1)
        if factor_on(previous) != factor:
            break
        start = previous
    last = reference.replace(day=1)
    for _ in range(11):
        following = (last + timedelta(days=32)).replace(day=1)
        if factor_on(following) != factor:
            break
        last = following
    end = (last + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return start, end


def _scope_values(scope_label: str, snapshot: dict, shop_code: str | None) -> dict[str, Any]:
    sku = snapshot.get("sku_info", {})
    goods_code = str(sku.get("goods_code")) if sku.get("goods_code") else None
    category = str(sku.get("category")) if sku.get("category") else None
    return {
        "shop_code": shop_code if "SHOP" in scope_label else None,
        "goods_code": goods_code if "SKU" in scope_label else None,
        "category": category if "CATEGORY" in scope_label else None,
    }


def _scope_sentence(scope_label: str, text: dict[str, str], *, shop: str,
                    product: str, category: str) -> str:
    key = f"scope_{scope_label.lower()}"
    return text.get(key, text["scope_shop_sku"]).format(
        shop=shop, product=product, category=category)


def _normalise_scope(raw: Any, default: str = "SHOP_SKU") -> str:
    label = str(raw or "").strip().upper()
    return label if label in SCOPE_LABELS else default


def build_knowledge_candidates(
    case: dict[str, Any],
    model_output: dict[str, Any],
    *,
    baseline_qty: int,
    seeds: SeedRepository | None = None,
) -> list[dict[str, Any]]:
    """Derive one calibrated, human-judgeable proposition per applicable cause."""
    snapshot = case["snapshot"]
    language = str(case.get("output_language") or "zh-CN")
    text = _text(language)
    seeds = seeds or SeedRepository()
    override_qty = int(case["override_qty"])
    on = _decision_date(snapshot)
    sku = snapshot.get("sku_info", {})
    category = str(sku.get("category") or "")
    product = str(sku.get("goods_name") or sku.get("goods_code") or "")
    shop_code = str(case.get("shop_code") or snapshot.get("shop") or "")
    shop_name = str(snapshot.get("shop_name") or shop_code)
    season_prior, holiday_prior = _engine_factors(
        category, on, snapshot.get("knowledge_applied"))

    findings = {
        str(item.get("cause_code")): item
        for item in model_output.get("findings", [])
        if item.get("applicable") and str(item.get("cause_code")) in CAUSE_KNOWLEDGE_KIND
    }
    candidates: list[dict[str, Any]] = []
    for cause_code, finding in findings.items():
        kind = CAUSE_KNOWLEDGE_KIND[cause_code]
        scope_label = _normalise_scope(finding.get("proposed_scope"))
        blocked_reason: str | None = None
        substitute_code = ""
        substitute_label = ""
        window_text = text["window_always"]
        applies_from = applies_to = None

        if kind == "SEASONAL_FACTOR":
            prior, lower, upper = season_prior, *FACTOR_BOUNDS
            span_start, span_end = (
                (on.replace(day=1),
                 (on.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1))
                if prior == 1.0 else _season_month_span(category, on, prior)
            )
            applies_from, applies_to = span_start.isoformat(), span_end.isoformat()
            window_text = text["window_months"].format(start=applies_from, end=applies_to)

            def evaluate(value: float) -> int:
                return int(replay_engine(
                    snapshot, factor_overrides={"season": value})["final_qty"])
        elif kind == "HOLIDAY_FACTOR":
            prior, lower, upper = holiday_prior, *FACTOR_BOUNDS
            applies_from = applies_to = on.isoformat()
            window_text = text["window_date"].format(date=applies_from)

            def evaluate(value: float) -> int:
                return int(replay_engine(
                    snapshot, factor_overrides={"holiday": value})["final_qty"])
        else:
            context = substitution_context(snapshot, seeds.load("substitutions"))
            if context is None:
                candidates.append(_blocked_candidate(
                    cause_code, kind, finding, scope_label, snapshot, shop_code,
                    text["no_relationship"], language))
                continue
            substitute_code = str(context["substitute_goods_code"])
            # Prefer the shop-floor name: a reviewer cannot judge a claim about
            # "653270" but can judge one about the flavour on the next shelf peg.
            substitute_label = str(
                context["evidence"].get("substitute_goods_name")
                or context["relationship"].get("substitute_goods_name")
                or substitute_code)
            prior, lower, upper = (
                float(context["relationship"].get("transfer_rate", 0.0)), *RATE_BOUNDS)

            def evaluate(value: float, _context: dict = context) -> int:
                delta = substitution_delta_at_rate(_context, value)["target_daily_delta"]
                return int(replay_engine(
                    snapshot, target_daily_demand_delta=float(delta))["final_qty"])

        proposed, achieved, status = solve_for_quantity(
            evaluate, prior=prior, target_qty=override_qty, lower=lower, upper=upper)
        # Only a calibration that actually lands on the store manager's quantity is
        # a proposition worth voting on. A boundary value that still misses, or a
        # prior that already matched, is reported so the reviewer learns something
        # was ruled out -- but it must not be publishable, or the reviewer would be
        # accepting a number the engine has already shown cannot work.
        acceptable = status in {"EXACT", "APPROXIMATE"}
        scope_sentence = _scope_sentence(
            scope_label, text, shop=shop_name, product=product, category=category)
        effect_key = {
            "EXACT": "effect", "APPROXIMATE": "effect_approximate",
            "UNREACHABLE": "effect_unreachable", "ALREADY_CORRECT": "effect_already",
        }[status]
        effect = text[effect_key].format(
            baseline=baseline_qty, achieved=achieved, target=override_qty,
            proposed=_format_value(proposed))
        ratio, plausible = _magnitude_ratio(kind, float(prior), float(proposed))
        if acceptable and not plausible:
            effect = f"{effect} {text['implausible'].format(ratio=ratio if ratio else '∞')}"
        if acceptable:
            statement = text[kind].format(
                scope=scope_sentence, window=window_text, substitute=substitute_label,
                prior=_format_value(prior), proposed=_format_value(proposed))
        else:
            # Nothing is being proposed, so the headline is the finding itself.
            # Repeating it as a separate effect line just renders it twice.
            joiner = "：" if language == "zh-CN" else ": "
            statement, effect = f"{scope_sentence}{joiner}{effect}", ""
        candidates.append({
            "candidate_id": cause_code,
            "cause_code": cause_code,
            "kind": kind,
            "domain": str(finding.get("domain") or "seasonality"),
            "scope_label": scope_label,
            "scope": _scope_values(scope_label, snapshot, shop_code),
            "prior_value": round(float(prior), 4),
            "proposed_value": proposed if acceptable else None,
            "boundary_value": None if acceptable else proposed,
            "acceptable": acceptable,
            "applies_from": applies_from if acceptable else None,
            "applies_to": applies_to if acceptable else None,
            "baseline_qty": baseline_qty,
            "target_qty": override_qty,
            "achieved_qty": achieved,
            "impact_qty": float(achieved - baseline_qty),
            "calibration_status": status,
            "magnitude_ratio": ratio,
            "magnitude_plausible": plausible,
            # Whether this condition is expected to come round again is the only
            # thing that decides if the entry is worth carrying. The model is the
            # only party that knows, and it costs it no numbers to say.
            "recurring": bool(finding.get("recurring", True)),
            "condition": str(finding.get("condition") or "").strip(),
            "explanation": str(finding.get("explanation") or "").strip(),
            "evidence_refs": list(finding.get("evidence_refs") or []),
            "substitute_goods_code": substitute_code or None,
            "blocked_reason": blocked_reason,
            "statement": statement,
            "effect": effect,
            "proposal_version": PROPOSAL_VERSION,
        })
    candidates.sort(key=lambda item: (
        not item["acceptable"], -abs(item["impact_qty"]), item["cause_code"]))
    return candidates


def _blocked_candidate(
    cause_code: str, kind: str, finding: dict[str, Any], scope_label: str,
    snapshot: dict, shop_code: str, reason: str, language: str,
) -> dict[str, Any]:
    """A cause the model judged applicable but which has no engine parameter here.

    Kept rather than dropped: "the model thinks substitution applies but no
    relationship is registered" is a gap in the data the reviewer should see and
    the sub-agent's owner should act on, not a row to silently disappear.
    """
    return {
        "candidate_id": cause_code, "cause_code": cause_code, "kind": kind,
        "domain": str(finding.get("domain") or "substitution"),
        "scope_label": scope_label,
        "scope": _scope_values(scope_label, snapshot, shop_code),
        "prior_value": None, "proposed_value": None,
        "boundary_value": None, "acceptable": False,
        "applies_from": None, "applies_to": None,
        "baseline_qty": None, "target_qty": None, "achieved_qty": None,
        "impact_qty": 0.0, "calibration_status": "UNREACHABLE",
        "recurring": bool(finding.get("recurring", True)),
        "condition": str(finding.get("condition") or "").strip(),
        "explanation": str(finding.get("explanation") or "").strip(),
        "evidence_refs": list(finding.get("evidence_refs") or []),
        "substitute_goods_code": None, "blocked_reason": reason,
        "statement": reason, "effect": "",
        "proposal_version": PROPOSAL_VERSION,
    }
