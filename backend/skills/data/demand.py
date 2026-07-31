"""Demand-signal skills (deterministic): reconstruct true demand from sales that
were **censored** (truncated) by past stockouts.

Observed sales during a stockout under-report real demand: an item that sold 0
because the shelf was empty reads as "no demand" and drives chronic
under-ordering. This skill lifts the observed mean / std back toward uncensored
demand, using either an explicit stockout-day count or a conservative
intermittent-censoring heuristic. Bounded and reproducible; with no censoring
signal it is a no-op so unconfigured runs reproduce prior behaviour.
"""
from __future__ import annotations
import math

MAX_UPLIFT = 1.6   # never inflate observed demand by more than +60% (guardrail)


def reconstruct_demand(mean: float, std: float, days: int,
                       stockout_days: int = 0, on_promo: bool = False) -> dict:
    """Return uncensored ``true_mean`` / ``true_std`` plus the applied ``uplift``.

    Two corrections combine (bounded by ``MAX_UPLIFT``):

    1. **Explicit censoring** — if ``stockout_days`` of the history were
       out-of-stock, the observed mean under-counts by the uncensored fraction
       ``days / (days - stockout_days)``.
    2. **Intermittent-censoring heuristic** — a very low mean with a high
       coefficient of variation is often partly hidden stockout zeros; apply a
       small, capped lift. Skipped during promo (promo already lifts baseline).
    """
    mean = max(0.0, float(mean or 0.0))
    std = max(0.0, float(std or 0.0))
    days = int(days or 0)
    if mean <= 0:
        return {"true_mean": 0.0, "true_std": round(std, 2), "uplift": 1.0,
                "reason": "观测均值为0,不做还原"}

    # 1) explicit stockout censoring
    stockout_days = max(0, min(int(stockout_days or 0), max(0, days - 1)))
    frac_available = ((days - stockout_days) / days) if days else 1.0
    frac_available = max(0.2, frac_available)   # avoid blow-up on tiny windows
    uplift = 1.0 / frac_available

    # 2) intermittent-censoring heuristic (only for bursty near-zero demand)
    cov = std / mean if mean else 0.0
    heuristic_hit = (not on_promo) and mean < 2.0 and cov > 1.2
    if heuristic_hit:
        uplift *= 1.0 + min(0.25, (cov - 1.2) * 0.2)

    uplift = round(min(uplift, MAX_UPLIFT), 3)
    true_mean = round(mean * uplift, 2)
    # reconstruction adds uncertainty: scale std by sqrt of the uplift
    true_std = round(std * math.sqrt(uplift), 2)

    if uplift > 1.0:
        bits = []
        if stockout_days > 0:
            bits.append(f"{stockout_days}/{days}天缺货截断")
        if heuristic_hit:
            bits.append("低销高波动疑似隐性缺货")
        reason = f"缺货还原x{uplift}(" + ",".join(bits) + ")"
    else:
        reason = "无缺货截断,需求信号可信"
    return {"true_mean": true_mean, "true_std": true_std, "uplift": uplift,
            "reason": reason}
