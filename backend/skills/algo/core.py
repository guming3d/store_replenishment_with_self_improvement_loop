"""Deterministic replenishment core skills (auditable, backtestable).

Implements a **continuous-review (s, S)** policy (不定时不定量): every time the
store is evaluated the SKU is ordered only when its inventory *position* has
fallen to/through the **reorder point** ``s``; when it does, enough is ordered to
lift the position back to the **order-up-to level** ``S``. Timing is driven by
stock depletion (not a fixed cycle) and the quantity varies with how far below
``s`` the position sits — hence 不定时 (variable timing) 不定量 (variable qty).

  reorder point  s = μ·L + z·σ·√L                    (cover the lead time)
  order-up-to    S = μ·(L+C) + z·σ·√(L+C)            (cover lead time + coverage)

where μ/σ are daily demand mean/std, L is the on-shelf lead time (from the run's
补货流程), C the target coverage days, and z the safety factor from the fill rate.

The AI orchestrator only decides WHICH skills to call and how to combine
candidates; all math lives here and stays reproducible. Module-level constants
are fallbacks only — the engine supplies resolved parameters per call.
"""
from __future__ import annotations
import math, random
from dataclasses import dataclass, asdict
from statistics import NormalDist

LEAD_TIME = 2              # on-shelf lead time in days (fallback default)
COVERAGE = 7               # target coverage days (fallback default)
DEFAULT_FILL_RATE = 0.90   # target cycle service level when nothing is configured


@dataclass
class Candidate:
    qty: int
    method: str
    risk: str


def param_learn(forecast: dict, lead_time: int = LEAD_TIME, coverage: int = COVERAGE) -> dict:
    """Self-learned params from forecast distribution (no manual maintenance)."""
    mean, std = forecast.get("mean", 0), forecast.get("std", 0)
    return {"daily_mean": mean, "daily_std": std,
            "cov": round(std / mean, 2) if mean else 0,
            "lead_time": lead_time, "coverage": coverage}


def z_from_fill_rate(fill_rate: float) -> float:
    """Convert a target fill rate (cycle service level, e.g. 0.95) into the
    safety-stock z-multiplier via the inverse normal CDF."""
    fr = min(max(float(fill_rate), 0.50), 0.9999)
    return round(NormalDist().inv_cdf(fr), 3)


def safety_stock(daily_std: float, z: float, days: float) -> float:
    """Safety stock covering ``days`` of protection: z·σ·√days."""
    return round(z * daily_std * math.sqrt(max(0.0, days)), 1)


def reorder_point(daily_mean: float, daily_std: float, z: float, lead_time: float) -> float:
    """Reorder point s = μ·L + z·σ·√L — the position at/below which we must order."""
    return round(daily_mean * lead_time + z * daily_std * math.sqrt(max(0.0, lead_time)), 1)


def order_up_to(daily_mean: float, daily_std: float, z: float,
                lead_time: float, coverage: float) -> float:
    """Order-up-to level S = μ·(L+C) + z·σ·√(L+C)."""
    horizon = lead_time + coverage
    return round(daily_mean * horizon + z * daily_std * math.sqrt(max(0.0, horizon)), 1)


def monte_carlo(daily_mean: float, daily_std: float, position: float,
                lead_time: int = LEAD_TIME, coverage: int = COVERAGE,
                runs: int = 2000) -> Candidate:
    """Newsvendor sampling for complex/fresh SKUs. Seeded for reproducibility.

    Samples demand over the protection horizon (lead time + coverage) and returns
    the P90 shortfall against the current position.
    """
    horizon = int(round(lead_time + coverage))
    rng = random.Random(round(daily_mean * 1000) + round(daily_std * 1000) + round(position))
    need = []
    for _ in range(runs):
        d = sum(max(0, rng.gauss(daily_mean, daily_std or 0.1)) for _ in range(horizon))
        need.append(max(0, d - position))
    need.sort()
    q = need[int(0.9 * runs)]
    return Candidate(qty=math.ceil(q), method="monte_carlo_p90", risk="low")


def constraint_round(qty: float, case_pack: int = 6, moq: int = 0, shelf_max: int = 999) -> int:
    """Round a raw order qty up to case pack, apply MOQ floor and shelf cap.

    A non-positive raw qty means "do not order" and returns 0 (MOQ never
    manufactures an order that the trigger did not ask for).
    """
    if qty <= 0:
        return 0
    case_pack = max(1, int(case_pack))
    q = max(qty, moq)
    q = math.ceil(q / case_pack) * case_pack
    return int(min(q, shelf_max))


def recommend(forecast: dict, position: float, scenario: str, params: dict | None = None,
              *, lead_time: int | None = None, case_pack: int | None = None,
              fill_rate: float | None = None) -> dict:
    """Continuous-review (s, S) recommendation for one SKU.

    ``position`` is the net available-to-sell inventory position (on-hand +
    in-transit − reserved − expiring). ``params`` carries the resolved
    parameters (fill_rate, coverage, case_pack, moq, shelf_max); ``lead_time``
    is supplied by the engine from the run's flow. Returns the reorder point,
    order-up-to level, whether a reorder is triggered, and rounded candidates.
    """
    params = params or {}
    fr = fill_rate if fill_rate is not None else params.get("fill_rate", DEFAULT_FILL_RATE)
    cp = case_pack if case_pack is not None else params.get("case_pack", 6)
    lt = int(lead_time if lead_time is not None else params.get("lead_time", LEAD_TIME))
    coverage = int(params.get("coverage", COVERAGE))
    moq = int(params.get("moq", 0))
    shelf_max = int(params.get("shelf_max", 999))

    p = param_learn(forecast, lt, coverage)
    z = z_from_fill_rate(fr)
    ss = safety_stock(p["daily_std"], z, lt)
    s = reorder_point(p["daily_mean"], p["daily_std"], z, lt)
    big_s = order_up_to(p["daily_mean"], p["daily_std"], z, lt, coverage)

    position = round(max(0.0, float(position or 0.0)), 2)
    triggered = position <= s
    raw = max(0.0, big_s - position) if triggered else 0.0
    analytic = constraint_round(raw, cp, moq, shelf_max)
    cands = [Candidate(analytic, "analytic_target", "low")]
    if triggered and scenario in ("fresh", "longtail", "new", "promo"):
        mc = monte_carlo(p["daily_mean"], p["daily_std"], position, lt, coverage)
        cands.append(Candidate(constraint_round(mc.qty, cp, moq, shelf_max), "monte_carlo", mc.risk))

    return {"safety_stock": ss, "reorder_point": s, "order_up_to": big_s,
            "target_stock": big_s, "position": position, "triggered": bool(triggered),
            "raw_qty": round(raw, 1),
            "fill_rate": round(float(fr), 4), "service_z": z,
            "candidates": [asdict(c) for c in cands], "params": p}
