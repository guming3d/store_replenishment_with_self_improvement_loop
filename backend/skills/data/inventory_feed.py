"""Current-inventory feed for the store (门店当前库存).

The continuous-review policy needs the *current* stock position of every SKU in
a store the moment an operator opens the app. No live WMS/POS feed exists in this
demo, so this module manufactures a **deterministic, reproducible** current
inventory from the demand forecast + (shop, sku, date) — the same store/SKU/day
always yields the same numbers, so screenshots and back-tests stay stable — while
still producing a realistic spread where some SKUs need replenishing and others
do not.

Store staff can override any field (they physically counted the shelf); overrides
are persisted to ``inventory_overrides.json`` (gitignored) and win over the
synthetic base. Swapping in a real feed later only means replacing
``synth_inventory`` / ``get_inventory`` — the engine consumes the merged result.

Fields (all in selling units, >= 0):
  - ``on_hand``:    units physically in the store.
  - ``in_transit``: open replenishment already on the way (avoid double-ordering).
  - ``reserved``:   committed to online / other channels.
  - ``expiring``:   units that will pass shelf-life within the protection period.
  - ``days_to_expiry``: remaining shelf-life (days) of that near-expiry lot; lets
    the engine credit back what can still be sold before it perishes.
  - ``recent_zero_days``: consecutive zero-sales days (phantom-stock signal).
"""
from __future__ import annotations
import json, os, random, threading

_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                     "api", "inventory_overrides.json")
_LOCK = threading.Lock()
_OVERRIDES: dict = {}   # shop -> goods -> {field: value}

FIELDS = ("on_hand", "in_transit", "reserved", "expiring", "days_to_expiry", "recent_zero_days")


# ---- deterministic synthetic base -------------------------------------------
def _seed(shop: str, goods: str, date: str) -> int:
    h = 2166136261
    for ch in f"{shop}|{goods}|{date}":
        h = (h ^ ord(ch)) * 16777619 & 0xFFFFFFFF
    return h


def synth_inventory(shop: str, goods: str, mean: float, date: str) -> dict:
    """Reproducible synthetic current inventory for one SKU on one day.

    ``on_hand`` is drawn as a random days-of-supply multiple of the daily mean,
    so low-turn SKUs occasionally sit at zero (trigger a reorder) while others
    are comfortably stocked (skip). Bounded and seeded for full reproducibility.
    All quantities are whole selling units (physical stock is never fractional).
    """
    mean = max(0.0, float(mean or 0.0))
    rng = random.Random(_seed(str(shop), str(goods), str(date)))
    base = mean if mean > 0 else 0.6   # give near-zero movers a small nominal base

    days_cover = round(rng.uniform(0.0, 11.0), 2)
    on_hand = int(round(base * days_cover))

    in_transit = int(round(base * rng.uniform(1.0, 4.0))) if rng.random() < 0.22 else 0
    reserved = int(round(base * rng.uniform(0.2, 1.0))) if rng.random() < 0.15 else 0
    expiring = int(round(base * rng.uniform(0.2, 0.8))) if rng.random() < 0.10 else 0
    # A short zero-sales streak now and then feeds the phantom-stock check.
    recent_zero_days = rng.choice([0, 0, 0, 0, 2, 5, 9]) if on_hand > 0 else 0
    # Remaining shelf-life (days) of the near-expiry lot; only meaningful when some
    # units are actually near expiry. Drawn LAST so every field above is unchanged
    # and prior (shop, sku, date) snapshots reproduce exactly.
    days_to_expiry = rng.randint(1, 5) if expiring > 0 else 0

    return {"on_hand": on_hand, "in_transit": in_transit, "reserved": reserved,
            "expiring": expiring, "days_to_expiry": days_to_expiry,
            "recent_zero_days": recent_zero_days}


# ---- persistence -------------------------------------------------------------
def _load() -> None:
    global _OVERRIDES
    try:
        with open(_FILE, encoding="utf-8") as f:
            data = json.load(f)
        _OVERRIDES = data if isinstance(data, dict) else {}
    except FileNotFoundError:
        _OVERRIDES = {}
    except Exception:
        _OVERRIDES = {}


def _save() -> None:
    try:
        with open(_FILE, "w", encoding="utf-8") as f:
            json.dump(_OVERRIDES, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _clean_fields(fields: dict) -> dict:
    out: dict = {}
    for k in FIELDS:
        if k in fields and fields[k] is not None:
            try:
                v = float(fields[k])
            except (TypeError, ValueError):
                continue
            # Physical stock counts are always whole selling units.
            out[k] = max(0, int(round(v)))
    return out


# ---- public API --------------------------------------------------------------
def get_inventory(shop: str, goods: str, mean: float, date: str) -> dict:
    """Merged current inventory: synthetic base with any staff override applied.

    Returns the six inventory fields plus ``overridden`` (list of overridden
    field names) and ``source`` ("override" if any field was overridden else
    "synthetic").
    """
    base = synth_inventory(shop, goods, mean, date)
    with _LOCK:
        ov = dict(_OVERRIDES.get(str(shop), {}).get(str(goods), {}))
    overridden = [k for k in FIELDS if k in ov]
    merged = dict(base)
    merged.update({k: ov[k] for k in overridden})
    merged["overridden"] = overridden
    merged["source"] = "override" if overridden else "synthetic"
    return merged


def set_inventory(shop: str, goods: str, fields: dict) -> dict:
    """Persist a staff override for a SKU. Only known, non-negative fields kept."""
    cleaned = _clean_fields(fields or {})
    with _LOCK:
        if not cleaned:
            bucket = _OVERRIDES.get(str(shop), {})
            existed = bucket.pop(str(goods), None) is not None
            if not bucket:
                _OVERRIDES.pop(str(shop), None)
            if existed:
                _save()
            return {}
        _OVERRIDES.setdefault(str(shop), {})[str(goods)] = cleaned
        _save()
    return dict(cleaned)


def clear_inventory(shop: str, goods: str) -> bool:
    """Drop a SKU override so it falls back to the synthetic base."""
    with _LOCK:
        bucket = _OVERRIDES.get(str(shop), {})
        existed = str(goods) in bucket
        bucket.pop(str(goods), None)
        if not bucket:
            _OVERRIDES.pop(str(shop), None)
        if existed:
            _save()
        return existed


def clear_store_inventory(shop: str) -> bool:
    """Drop all overrides for a store."""
    with _LOCK:
        existed = str(shop) in _OVERRIDES
        _OVERRIDES.pop(str(shop), None)
        if existed:
            _save()
        return existed


_load()
