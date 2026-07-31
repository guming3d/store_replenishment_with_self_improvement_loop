"""Soft-information skills: translate fuzzy semantics into demand deltas (Δ).

Each returns a multiplicative factor on baseline demand plus a short reason.
LLM-backed in production; here heuristic fallbacks keep this self-contained
so the deterministic core stays auditable.
"""
from __future__ import annotations
from datetime import date

CATEGORY_SEASON = {"果汁饮料": "summer", "啤酒": "summer", "水饺/馄饨": "winter"}


def season_factor(category: str, dt: date) -> dict:
    m = dt.month
    s = CATEGORY_SEASON.get(category)
    f, why = 1.0, "无明显季节性"
    if s == "summer" and m in (6, 7, 8): f, why = 1.25, "夏季旺季,冷饮/啤酒走量提升"
    if s == "winter" and m in (12, 1, 2): f, why = 1.30, "冬季旺季,速冻热销"
    return {"factor": f, "reason": why}


def holiday_factor(dt: date) -> dict:
    if (dt.month, dt.day) in [(1, 1), (5, 1), (10, 1)] or (dt.month == 2 and dt.day <= 15):
        return {"factor": 1.4, "reason": "节假日备货,建议提前分批平滑库存"}
    return {"factor": 1.0, "reason": "非节假日"}


def promo_factor(promo_uplift: float, on_promo: bool) -> dict:
    if on_promo and promo_uplift > 1.0:
        return {"factor": round(promo_uplift, 2), "reason": f"促销期,历史 uplift x{promo_uplift}"}
    return {"factor": 1.0, "reason": "无促销"}


def new_product_factor(days_of_history: int) -> dict:
    if days_of_history < 30:
        return {"factor": 1.15, "reason": "新品冷启动,样本不足,谨慎放量"}
    return {"factor": 1.0, "reason": "非新品"}


def combine(category: str, dt: date, promo_uplift: float, on_promo: bool, days: int,
            factor_overrides: dict[str, float] | None = None) -> dict:
    """Combine factors, optionally replaying evidence-backed seasonal/holiday values.

    The optional argument is intentionally additive: calls made by the existing
    replenishment path retain byte-for-byte equivalent factor selection.
    """
    s, h, p, n = (season_factor(category, dt), holiday_factor(dt),
                  promo_factor(promo_uplift, on_promo), new_product_factor(days))
    for name, value in (factor_overrides or {}).items():
        if name in {"season", "holiday"}:
            if not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{name} factor override must be a positive number")
            target = s if name == "season" else h
            target["factor"] = float(value)
            target["reason"] = f"attribution replay override ({name})"
    total = s["factor"] * h["factor"] * p["factor"] * n["factor"]
    return {"total_delta": round(total, 2),
            "factors": {"season": s, "holiday": h, "promo": p, "new": n}}
