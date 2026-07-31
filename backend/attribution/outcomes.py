"""Deterministic ground truth for the replenishment learning loop.

Attribution explains the gap between the system's recommendation and the store
manager's override. It never says which of the two was closer to what the store
actually needed, so on its own it can only accumulate human preference. This
module supplies the missing half: it scores both quantities against realised
daily sales, and that verdict is what lets knowledge be promoted or retired on
accuracy rather than on confidence.

Every function here is pure. Window arithmetic, the hindsight-optimal quantity
and the verdict are all reproducible from the stored row, so a completed outcome
can be recomputed and audited without touching the engine or the sales feed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

OUTCOME_VERSION = "outcome-v1"

#: Verdict vocabulary. ``PENDING`` means the judgement window has not closed yet,
#: which is a different thing from a tie and must not be counted as one.
Verdict = Literal["PENDING", "ENGINE_BETTER", "HUMAN_BETTER", "TIE"]
OutcomeStatus = Literal["PENDING", "PARTIAL", "COMPLETE"]

#: The order is sized to cover lead time plus coverage days. Demand on the
#: decision date itself is served from the position that was already measured,
#: so the fair judgement window opens the following day.
WINDOW_OFFSET_DAYS = 1

#: Case-pack rounding means neither quantity can land exactly on the ideal, so a
#: difference smaller than half a case is noise rather than a better decision.
MIN_TOLERANCE_UNITS = 1.0


def _to_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)[:10]).date()


def demand_window(decision_date: str | date, horizon_days: int) -> tuple[str, str]:
    """Return the inclusive ISO date window whose demand the order was sized for."""
    if horizon_days < 1:
        raise ValueError("horizon_days must be at least 1")
    start = _to_date(decision_date) + timedelta(days=WINDOW_OFFSET_DAYS)
    end = start + timedelta(days=horizon_days - 1)
    return start.isoformat(), end.isoformat()


def window_dates(window_start: str, window_end: str) -> list[str]:
    start, end = _to_date(window_start), _to_date(window_end)
    if end < start:
        raise ValueError("window_end precedes window_start")
    return [(start + timedelta(days=offset)).isoformat()
            for offset in range((end - start).days + 1)]


def horizon_days_from_snapshot(snapshot: dict[str, Any], *, default: int = 5) -> int:
    """Read lead time plus coverage out of a frozen recommendation snapshot.

    The snapshot is the only admissible source: reading today's config would
    score a past decision against parameters that were not in force when it was
    made. Falls back to ``default`` only when the snapshot predates the field.
    """
    flow = str(snapshot.get("flow") or "A").upper()
    lead_time = 1 if flow == "B" else 2
    params = snapshot.get("params")
    coverage = None
    if isinstance(params, dict):
        coverage = params.get("coverage")
    try:
        coverage = int(coverage) if coverage is not None else None
    except (TypeError, ValueError):
        coverage = None
    if coverage is None:
        return max(1, default)
    return max(1, lead_time + coverage)


def realised_demand(daily_units: dict[str, float], window_start: str, window_end: str,
                    *, lost_sales: dict[str, float] | None = None) -> dict[str, Any]:
    """Sum sales across the window, counting known lost sales as unmet demand.

    Sold units alone understate demand on any day the shelf was empty, which
    would systematically make the lower of the two quantities look correct.
    Where the feed reports lost sales they are added back; where it does not,
    the shortfall is flagged rather than silently assumed to be zero.
    """
    lost_sales = lost_sales or {}
    expected = window_dates(window_start, window_end)
    observed = [day for day in expected if day in daily_units]
    sold = sum(float(daily_units[day]) for day in observed)
    lost = sum(float(lost_sales.get(day, 0) or 0) for day in observed)
    return {
        "expected_days": len(expected),
        "observed_days": len(observed),
        "missing_days": [day for day in expected if day not in daily_units],
        "units_sold": round(sold, 4),
        "lost_sales_units": round(lost, 4),
        "actual_demand": round(sold + lost, 4),
        "complete": len(observed) == len(expected),
    }


def hindsight_optimal_qty(actual_demand: float, opening_position: float) -> float:
    """The quantity that would have exactly covered realised demand.

    Negative need means the position already covered the window, in which case
    the correct order was zero -- not a negative number, because the engine
    cannot un-order stock that is already on the shelf.
    """
    return round(max(0.0, float(actual_demand) - float(opening_position)), 4)


def tolerance_units(case_pack: int | None) -> float:
    """Half a case, floored at one unit: the granularity neither side controls."""
    try:
        pack = int(case_pack or 1)
    except (TypeError, ValueError):
        pack = 1
    return max(MIN_TOLERANCE_UNITS, pack / 2)


def judge(*, recommended_qty: float, ordered_qty: float, ideal_qty: float,
          tolerance: float) -> Verdict:
    engine_error = abs(float(recommended_qty) - float(ideal_qty))
    human_error = abs(float(ordered_qty) - float(ideal_qty))
    if abs(engine_error - human_error) <= tolerance:
        return "TIE"
    return "HUMAN_BETTER" if human_error < engine_error else "ENGINE_BETTER"


@dataclass(frozen=True)
class OutcomeInputs:
    """Everything needed to score one decision, all frozen at decision time."""
    recommended_qty: int
    ordered_qty: int
    opening_position: float
    horizon_days: int
    case_pack: int = 1


def score_outcome(inputs: OutcomeInputs, demand: dict[str, Any]) -> dict[str, Any]:
    """Score one decision against realised demand.

    Returns a partial result while the window is still open: the caller stores it
    so the dashboard can show progress, but ``verdict`` stays ``PENDING`` and the
    row must not feed knowledge promotion until ``status`` is ``COMPLETE``.
    """
    observed_days = int(demand["observed_days"])
    expected_days = int(demand["expected_days"])
    if observed_days == 0:
        status: OutcomeStatus = "PENDING"
    elif demand["complete"]:
        status = "COMPLETE"
    else:
        status = "PARTIAL"

    result: dict[str, Any] = {
        "version": OUTCOME_VERSION,
        "status": status,
        "observed_days": observed_days,
        "expected_days": expected_days,
        "missing_days": demand["missing_days"],
        "units_sold": demand["units_sold"],
        "lost_sales_units": demand["lost_sales_units"],
        "actual_demand": demand["actual_demand"],
        "verdict": "PENDING",
        "ideal_qty": None,
        "engine_error": None,
        "human_error": None,
        "engine_abs_error": None,
        "human_abs_error": None,
        "stockout_units": None,
        "overstock_units": None,
        "tolerance_units": tolerance_units(inputs.case_pack),
    }
    if status != "COMPLETE":
        return result

    actual = float(demand["actual_demand"])
    ideal = hindsight_optimal_qty(actual, inputs.opening_position)
    engine_error = round(float(inputs.recommended_qty) - ideal, 4)
    human_error = round(float(inputs.ordered_qty) - ideal, 4)
    supplied = float(inputs.opening_position) + float(inputs.ordered_qty)
    result.update({
        "ideal_qty": ideal,
        "engine_error": engine_error,
        "human_error": human_error,
        "engine_abs_error": abs(engine_error),
        "human_abs_error": abs(human_error),
        "stockout_units": round(max(0.0, actual - supplied), 4),
        "overstock_units": round(max(0.0, supplied - actual), 4),
        "verdict": judge(
            recommended_qty=inputs.recommended_qty, ordered_qty=inputs.ordered_qty,
            ideal_qty=ideal, tolerance=result["tolerance_units"],
        ),
    })
    return result


def accuracy_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate completed outcomes into the numbers the accuracy board shows.

    Only completed rows count. Including partial ones would let a window that has
    seen two of five days drag the error down and make accuracy look like it
    improved when all that happened was that the data had not arrived yet.
    """
    complete = [row for row in rows if row.get("status") == "COMPLETE"]
    total = len(complete)
    summary: dict[str, Any] = {
        "version": OUTCOME_VERSION,
        "sample_size": total,
        "pending_count": len([row for row in rows if row.get("status") != "COMPLETE"]),
        "engine_better": 0, "human_better": 0, "tie": 0,
        "engine_mae": None, "human_mae": None,
        "engine_mape": None, "human_mape": None,
        "stockout_units": 0.0, "overstock_units": 0.0,
        "human_win_rate": None, "engine_win_rate": None, "tie_rate": None,
        "accuracy_gain_units": None,
    }
    if not total:
        return summary
    for row in complete:
        verdict = row.get("verdict")
        if verdict == "ENGINE_BETTER":
            summary["engine_better"] += 1
        elif verdict == "HUMAN_BETTER":
            summary["human_better"] += 1
        elif verdict == "TIE":
            summary["tie"] += 1
        summary["stockout_units"] += float(row.get("stockout_units") or 0)
        summary["overstock_units"] += float(row.get("overstock_units") or 0)
    engine_abs = [abs(float(row["engine_error"])) for row in complete
                  if row.get("engine_error") is not None]
    human_abs = [abs(float(row["human_error"])) for row in complete
                 if row.get("human_error") is not None]
    if engine_abs:
        summary["engine_mae"] = round(sum(engine_abs) / len(engine_abs), 4)
    if human_abs:
        summary["human_mae"] = round(sum(human_abs) / len(human_abs), 4)
    # MAPE is undefined where the ideal order was zero, so those rows are
    # dropped from the percentage rather than being given an arbitrary
    # denominator that would make the percentage unreadable.
    scaled = [row for row in complete if float(row.get("ideal_qty") or 0) > 0]
    if scaled:
        summary["engine_mape"] = round(sum(
            abs(float(row["engine_error"])) / float(row["ideal_qty"]) for row in scaled
        ) / len(scaled), 4)
        summary["human_mape"] = round(sum(
            abs(float(row["human_error"])) / float(row["ideal_qty"]) for row in scaled
        ) / len(scaled), 4)
    decided = summary["engine_better"] + summary["human_better"]
    if decided:
        summary["human_win_rate"] = round(summary["human_better"] / decided, 4)
        summary["engine_win_rate"] = round(summary["engine_better"] / decided, 4)
    summary["tie_rate"] = round(summary["tie"] / total, 4)
    if summary["engine_mae"] is not None and summary["human_mae"] is not None:
        summary["accuracy_gain_units"] = round(
            summary["engine_mae"] - summary["human_mae"], 4)
    summary["stockout_units"] = round(summary["stockout_units"], 4)
    summary["overstock_units"] = round(summary["overstock_units"], 4)
    return summary


def is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
