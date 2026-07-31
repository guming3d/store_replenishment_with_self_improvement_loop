from datetime import date

from engine import run
from skills.soft.factors import combine


def test_replay_hooks_preserve_existing_default_behavior():
    factors = combine("啤酒", date(2026, 7, 23), 1, False, 60)
    assert factors == combine("啤酒", date(2026, 7, 23), 1, False, 60, factor_overrides=None)
    params = {"fill_rate": 0.95, "coverage": 2, "case_pack": 1, "moq": 0, "shelf_max": 999}
    args = ("shop", {"goods_code": "sku", "goods_name": "Beer", "category": "啤酒"},
            {"mean": 3, "std": 1, "days": 60}, date(2026, 7, 23))
    kwargs = {"inventory_snapshot": {"on_hand": 0, "in_transit": 0, "reserved": 0, "expiring": 0},
              "params": params}
    ordinary = run(*args, **kwargs)
    explicit_defaults = run(*args, **kwargs, factor_overrides=None, target_daily_demand_delta=0)
    assert ordinary["chosen_qty"] == explicit_defaults["chosen_qty"]
    changed = run(*args, **kwargs, factor_overrides={"season": 1.0}, target_daily_demand_delta=2)
    # trace[3] is the soft-signal step; trace[2] resolves knowledge ahead of it.
    assert changed["trace"][3]["output"] != ordinary["trace"][3]["output"]
