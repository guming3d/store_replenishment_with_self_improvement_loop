"""Replenishment parameter configuration (store-level + store/SKU-level).

Provides the tunable parameters for the **continuous-review (s, S)** replenishment
policy (不定时不定量): the system decides *whether* to order (inventory position
below the reorder point) and *how much* (order up to the order-up-to level), so
there is no fixed review cycle. Values are user-configurable and persisted to
``replenishment_config.json`` so they survive backend restarts.

Two former parameters are no longer configured here:
  * ``lead_time`` — fixed by the standard schedule
    (申请→次日到货→后天上架 = 2 天).
  * ``on_hand`` — now supplied by the live/synthetic inventory feed
    (``skills/data/inventory_feed``) instead of a static default.

Resolution order for a (store, sku) parameter set:
  1. per store+SKU override -> sku[shop_code][goods_code]
  2. per-store default       -> store[shop_code]
  3. system default          -> PARAM_SPECS[*].default

Each level stores only the keys the user set; missing keys fall through to the
next level. ``PARAM_SPECS`` doubles as the schema the frontend uses to render the
configuration panel (labels / min / max / step / default).
"""
from __future__ import annotations
import json, os, threading

# ---- Parameter catalogue -----------------------------------------------------
# Store-level params tune the service target and the order batch size; SKU-level
# params encode the physical ordering constraints. Fixed lead time and on-hand
# (from the inventory feed) are intentionally NOT here.
PARAM_SPECS: list[dict] = [
    {"key": "fill_rate",     "type": "percent", "scope": "store", "default": 0.90, "min": 0.50, "max": 0.9999, "step": 0.01,
     "label": "服务水平(填充率)", "label_en": "Service Level (Fill Rate)",
     "help": "目标周期服务水平, 越高安全库存/补货点越高", "help_en": "Target cycle service level; higher means a higher safety stock / reorder point"},
    {"key": "coverage",      "type": "int",     "scope": "store", "default": 7,    "min": 1,    "max": 90,     "step": 1,
     "label": "目标覆盖天数", "label_en": "Target Coverage (days)",
     "help": "触发补货时一次补足到可覆盖的目标销售天数(决定订货批量与补货频次)",
     "help_en": "When a reorder triggers, order up to cover this many days of demand (sets batch size / order frequency)"},
    {"key": "case_pack",     "type": "int",     "scope": "sku",   "default": 6,    "min": 1,    "max": 1000,   "step": 1,
     "label": "箱规/最小包装量", "label_en": "Case Pack",
     "help": "补货数量向上取整到该包装规格的整数倍", "help_en": "Order qty is rounded up to a multiple of this pack size"},
    {"key": "moq",           "type": "int",     "scope": "sku",   "default": 0,    "min": 0,    "max": 10000,  "step": 1,
     "label": "最小起订量(MOQ)", "label_en": "Min Order Qty (MOQ)",
     "help": "供应商要求的最小起订量", "help_en": "Supplier minimum order quantity"},
    {"key": "shelf_max",     "type": "int",     "scope": "sku",   "default": 999,  "min": 1,    "max": 100000, "step": 1,
     "label": "货架最大陈列量", "label_en": "Shelf Max Capacity",
     "help": "门店货架/仓容上限, 补货数量不超过该值", "help_en": "Shelf / capacity cap; order qty never exceeds this"},
]

PARAM_KEYS: list[str] = [s["key"] for s in PARAM_SPECS]
_SPEC: dict[str, dict] = {s["key"]: s for s in PARAM_SPECS}
DEFAULTS: dict = {s["key"]: s["default"] for s in PARAM_SPECS}

# Scope split: store-level params are shared by every SKU in a store; SKU-level
# params are configured per store+SKU. Store config only persists STORE_KEYS and
# SKU overrides only persist SKU_KEYS (see set_store_config / set_sku_config).
STORE_KEYS: list[str] = [s["key"] for s in PARAM_SPECS if s.get("scope", "store") == "store"]
SKU_KEYS: list[str] = [s["key"] for s in PARAM_SPECS if s.get("scope", "store") == "sku"]

# Kept for backwards compatibility with modules that imported these names.
DEFAULT_FILL_RATE = _SPEC["fill_rate"]["default"]
MIN_FILL_RATE = _SPEC["fill_rate"]["min"]
MAX_FILL_RATE = _SPEC["fill_rate"]["max"]

_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "replenishment_config.json")
_LOCK = threading.Lock()
_CONFIG: dict = {"store": {}, "sku": {}}


# ---- validation --------------------------------------------------------------
def clamp_value(key: str, value):
    """Coerce + clamp a single parameter into its safe range and type."""
    spec = _SPEC.get(key)
    if spec is None:
        raise ValueError(f"unknown parameter {key!r}")
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number, got {value!r}")
    num = min(max(num, spec["min"]), spec["max"])
    return int(round(num)) if spec["type"] == "int" else round(num, 4)


def clamp_fill_rate(value) -> float:
    """Validate + clamp a user-supplied fill rate into the safe range."""
    return float(clamp_value("fill_rate", value))


def clamp_params(params: dict | None, *, partial: bool = True, keys: list[str] | None = None) -> dict:
    """Validate + clamp a params dict.

    Unknown keys (and any key not in ``keys``) are dropped. When ``partial``
    (SKU overrides) only the supplied keys are returned; otherwise (store
    defaults) every allowed key is present, filled from ``DEFAULTS`` when
    missing. ``keys`` restricts which parameter keys are accepted (defaults to
    all ``PARAM_KEYS``); this enforces the store/SKU scope split.
    """
    params = params or {}
    allowed = keys if keys is not None else PARAM_KEYS
    out: dict = {}
    for k in allowed:
        if k in params and params[k] is not None:
            out[k] = clamp_value(k, params[k])
        elif not partial:
            out[k] = DEFAULTS[k]
    return out


# ---- persistence -------------------------------------------------------------
def _sanitize(data) -> dict:
    store: dict = {}
    sku: dict = {}
    if isinstance(data, dict):
        for shop, params in (data.get("store") or {}).items():
            store[str(shop)] = clamp_params(params or {}, partial=False, keys=STORE_KEYS)
        for shop, skus in (data.get("sku") or {}).items():
            bucket: dict = {}
            for gc, params in (skus or {}).items():
                cleaned = clamp_params(params or {}, partial=True, keys=SKU_KEYS)
                if cleaned:
                    bucket[str(gc)] = cleaned
            if bucket:
                sku[str(shop)] = bucket
    return {"store": store, "sku": sku}


def _load() -> None:
    global _CONFIG
    try:
        with open(_CONFIG_FILE, encoding="utf-8") as f:
            _CONFIG = _sanitize(json.load(f))
    except FileNotFoundError:
        _CONFIG = {"store": {}, "sku": {}}
    except Exception:
        # Corrupt/unreadable file: fall back to empty rather than crash the API.
        _CONFIG = {"store": {}, "sku": {}}


def _save() -> None:
    try:
        with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(_CONFIG, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---- resolution --------------------------------------------------------------
def resolve(shop_code: str | None = None, goods_code: str | None = None) -> dict:
    """Return the fully-resolved parameter set for a (store, sku).

    Layers system defaults <- store default <- store/SKU override.
    """
    with _LOCK:
        eff = dict(DEFAULTS)
        if shop_code is not None:
            eff.update(_CONFIG["store"].get(str(shop_code), {}))
            if goods_code is not None:
                eff.update(_CONFIG["sku"].get(str(shop_code), {}).get(str(goods_code), {}))
        return eff


def get_fill_rate(shop_code: str | None = None, goods_code: str | None = None) -> float:
    """Resolve the effective fill rate for a (store, sku)."""
    return float(resolve(shop_code, goods_code)["fill_rate"])


def is_configured(shop_code: str, goods_code: str | None = None) -> dict:
    """Report whether an explicit config exists for the requested scope.

    ``level`` is ``sku`` (store+SKU override present), ``store`` (only a store
    default present) or ``none``. For the store scope (``goods_code is None``,
    e.g. a batch run over the whole store) the store counts as configured when it
    has a store default **or at least one store/SKU override** — saving per-SKU
    parameters is enough to unblock a batch run.
    """
    with _LOCK:
        shop = str(shop_code)
        store_set = shop in _CONFIG["store"]
        sku_bucket = _CONFIG["sku"].get(shop, {})
        if goods_code is not None:
            sku_set = str(goods_code) in sku_bucket
            level = "sku" if sku_set else ("store" if store_set else "none")
            return {"configured": sku_set or store_set, "level": level,
                    "shop_code": shop, "goods_code": str(goods_code)}
        sku_any = bool(sku_bucket)
        level = "store" if store_set else ("sku" if sku_any else "none")
        return {"configured": store_set or sku_any, "level": level,
                "shop_code": shop, "goods_code": None}


# ---- getters -----------------------------------------------------------------
def get_store_config(shop_code: str) -> dict | None:
    """Explicit store-level params (None if the store was never configured)."""
    with _LOCK:
        v = _CONFIG["store"].get(str(shop_code))
        return dict(v) if v is not None else None


def get_sku_config(shop_code: str, goods_code: str) -> dict | None:
    """Explicit store/SKU-level override params (None if not overridden)."""
    with _LOCK:
        v = _CONFIG["sku"].get(str(shop_code), {}).get(str(goods_code))
        return dict(v) if v is not None else None


def list_sku_configs(shop_code: str) -> dict:
    """All store/SKU overrides for a store: ``{goods_code: {param: value}}``."""
    with _LOCK:
        return {gc: dict(p) for gc, p in _CONFIG["sku"].get(str(shop_code), {}).items()}


def get_all() -> dict:
    """A deep copy of the whole configuration (store defaults + SKU overrides)."""
    with _LOCK:
        return {"store": {k: dict(v) for k, v in _CONFIG["store"].items()},
                "sku": {k: {gc: dict(p) for gc, p in b.items()} for k, b in _CONFIG["sku"].items()}}


def param_specs() -> list[dict]:
    """Parameter schema for the frontend config panel."""
    return [dict(s) for s in PARAM_SPECS]


# ---- setters -----------------------------------------------------------------
def set_store_config(shop_code: str, params: dict) -> dict:
    """Set the store-level default parameters.

    Only STORE-scoped keys (service level, target coverage) are persisted;
    SKU-scoped keys are ignored so they stay per store+SKU.
    """
    cleaned = clamp_params(params, partial=False, keys=STORE_KEYS)
    with _LOCK:
        _CONFIG["store"][str(shop_code)] = cleaned
        _save()
    return dict(cleaned)


def set_sku_config(shop_code: str, goods_code: str, params: dict) -> dict:
    """Set a store/SKU-level override.

    Only SKU-scoped keys (case pack, MOQ, shelf max) are persisted;
    STORE-scoped keys are ignored so they stay at the store level.
    """
    cleaned = clamp_params(params, partial=True, keys=SKU_KEYS)
    with _LOCK:
        if not cleaned:
            # nothing to override -> treat as a clear
            existed = _CONFIG["sku"].get(str(shop_code), {}).pop(str(goods_code), None) is not None
            if not _CONFIG["sku"].get(str(shop_code)):
                _CONFIG["sku"].pop(str(shop_code), None)
            if existed:
                _save()
            return {}
        _CONFIG["sku"].setdefault(str(shop_code), {})[str(goods_code)] = cleaned
        _save()
    return dict(cleaned)


def clear_store_config(shop_code: str) -> bool:
    """Remove a store-level default. Returns True if one existed."""
    with _LOCK:
        existed = str(shop_code) in _CONFIG["store"]
        _CONFIG["store"].pop(str(shop_code), None)
        if existed:
            _save()
        return existed


def clear_sku_config(shop_code: str, goods_code: str) -> bool:
    """Remove a store/SKU override so it falls back to the store default."""
    with _LOCK:
        bucket = _CONFIG["sku"].get(str(shop_code), {})
        existed = str(goods_code) in bucket
        bucket.pop(str(goods_code), None)
        if not bucket:
            _CONFIG["sku"].pop(str(shop_code), None)
        if existed:
            _save()
        return existed


_load()
