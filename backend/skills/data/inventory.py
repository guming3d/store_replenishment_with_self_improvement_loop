"""Inventory-position skills (deterministic, auditable).

Turn a raw on-hand number into a *net available-to-sell* position and flag
phantom / ghost inventory. These correct the order **position** that drives
replenishment: ordering against on-hand alone double-counts pipeline stock and
ignores units that are reserved to other channels or about to expire.

All functions are pure and side-effect free so results stay reproducible and
backtestable; a live inventory feed can be wired in later without touching the
math. When no extra signal is supplied the position collapses to the raw
on-hand, so existing (unconfigured) runs reproduce prior behaviour exactly.
"""
from __future__ import annotations


def inventory_position(on_hand: float, in_transit: float = 0.0,
                       reserved: float = 0.0, expiring: float = 0.0,
                       days_to_expiry: float | None = None,
                       daily_demand: float = 0.0) -> dict:
    """Net available-to-sell = on_hand + in_transit - reserved - expiring_waste (>= 0).

    - ``in_transit``: open POs / units already on the way (avoid re-ordering them).
    - ``reserved``: committed to online / promotions / other channels.
    - ``expiring``: units whose shelf-life ends inside the protection period.
    - ``days_to_expiry``: remaining shelf-life (days) of that near-expiry lot.
    - ``daily_demand``: expected daily sell-through, used to see how much of the
      near-expiry lot can actually be sold before it perishes.

    Near-expiry units still occupy the shelf and, under FEFO, sell *first*. So
    only the part we cannot realistically move in time is true waste::

        expiring_sellable = min(expiring, floor(daily_demand * days_to_expiry))
        expiring_waste    = expiring - expiring_sellable

    and only ``expiring_waste`` is deducted from the position. When
    ``days_to_expiry`` is unknown (``None``) we stay conservative and treat the
    whole near-expiry lot as waste, reproducing the previous behaviour exactly.
    """
    on_hand = max(0, int(round(float(on_hand or 0.0))))
    in_transit = max(0, int(round(float(in_transit or 0.0))))
    reserved = max(0, int(round(float(reserved or 0.0))))
    expiring = max(0, int(round(float(expiring or 0.0))))
    daily_demand = max(0.0, float(daily_demand or 0.0))

    d = None if days_to_expiry is None else max(0, int(round(float(days_to_expiry or 0.0))))
    if expiring and d is not None:
        # floor() keeps the sellable credit conservative -> guards against
        # under-ordering / stockouts if the lot moves slower than expected.
        expiring_sellable = min(expiring, int(daily_demand * d))
        expiring_waste = expiring - expiring_sellable
    else:
        expiring_sellable = 0
        expiring_waste = expiring

    available = max(0, on_hand + in_transit - reserved - expiring_waste)
    notes = []
    if in_transit:
        notes.append(f"在途{in_transit:g}(不再重复下单)")
    if reserved:
        notes.append(f"预占{reserved:g}")
    if expiring:
        if d is not None:
            notes.append(f"临期{expiring:g}(剩{d}天:可售{expiring_sellable:g}/损耗{expiring_waste:g})")
        else:
            notes.append(f"临期{expiring:g}(计入损耗)")
    return {"on_hand": on_hand, "in_transit": in_transit,
            "reserved": reserved, "expiring": expiring,
            "days_to_expiry": d, "expiring_sellable": expiring_sellable,
            "expiring_waste": expiring_waste, "available": available,
            "reason": "净可用=" + "; ".join(notes) if notes else "净可用=在手(无在途/预占/临期)"}


def phantom_check(available: float, recent_zero_days: int = 0,
                  expected_daily: float = 0.0, threshold_days: int = 7) -> dict:
    """Flag suspected phantom / ghost inventory.

    Phantom inventory = the system shows stock on hand yet the shelf is empty,
    producing a run of zero-sales days for an item that should be selling. When
    ``available > 0`` and there is a long zero-sales streak for a normally-selling
    SKU, the on-hand number is untrustworthy: replenishment should not be blocked
    by it (break the "no sales -> no demand -> never restock" loop) and the unit
    should be sent for a physical count.
    """
    available = max(0.0, float(available or 0.0))
    recent_zero_days = max(0, int(recent_zero_days or 0))
    expected_daily = max(0.0, float(expected_daily or 0.0))
    suspect = (available > 0 and recent_zero_days >= threshold_days
               and expected_daily > 0)
    if suspect:
        reason = (f"账面净可用{available:g}但连续{recent_zero_days}天零动销"
                  f"(日常应动销~{expected_daily:g}/天),疑似幽灵库存,建议实盘核查")
    elif available <= 0:
        reason = "净可用为0,受库存约束"
    else:
        reason = "库存健康,无幽灵库存迹象"
    return {"phantom_suspect": bool(suspect), "recent_zero_days": recent_zero_days,
            "reason": reason}
