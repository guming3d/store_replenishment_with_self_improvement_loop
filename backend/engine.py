"""Scenario routing + continuous-review replenishment engine.

Fully-automatic **不定时不定量** replenishment for a store pulling from the central
warehouse. For each SKU the engine:

  1. classifies the demand scenario (fresh / new / long-tail / promo / standard);
  2. reconstructs true demand from stock-out-censored history (skills.data.demand);
  3. applies soft signals (weekday / weather / promo) to the daily mean;
  4. folds in knowledge learned from reviewed store overrides (see ``knowledge``);
  5. reads the store's *current* inventory and turns it into a net available-to-
     sell position, flagging phantom stock (skills.data.inventory[_feed]);
  6. runs the (s, S) core: order only when the position has fallen to/through the
     reorder point s, and only enough to reach the order-up-to level S.

Timing is therefore driven by depletion (不定时) and the quantity varies with the
gap below s (不定量). New replenishment runs use one fixed operational schedule:

  - 今天申请 → 明天到货 → 后天上架   ⇒ L = 2 天

Operational parameters (fill rate, coverage, case pack, MOQ, shelf max) are
resolved per shop+SKU from ``config``; current stock comes from the inventory
feed, never hard-coded. Store staff can edit both the inventory and the final
order quantity afterwards.
"""
from __future__ import annotations
from datetime import datetime, timedelta
import uuid
from skills.algo import core
from skills.soft import factors
from skills.data import demand, inventory
from skills.data import inventory_feed
import config

FRESH = {"水饺/馄饨"}

# Flow B remains internal only so frozen historical snapshots can still be replayed.
FLOW_LEAD = {"A": 2, "B": 1}
FLOW_DESC = {
    "A": "今天申请→明天到货→后天上架",
    "B": "今天申请→明天到货并上架",
}

#: Engine inputs a knowledge directive may address, keyed by the dotted path the
#: knowledge layer stores against each kind. A directive naming anything else
#: raises rather than being skipped: knowledge that silently fails to apply is
#: indistinguishable from knowledge that had no effect, and telling those two
#: apart is the entire point of feeding review outcomes back into the engine.
KNOWLEDGE_TARGETS = frozenset({
    "factor_overrides.season",
    "factor_overrides.holiday",
    "target_daily_demand_delta",
    "params.fill_rate",
    "params.shelf_max",
})


def _apply_knowledge(directives, *, factor_overrides, target_daily_demand_delta,
                     params, fill_rate_pinned):
    """Fold learned knowledge into the engine's inputs; explicit arguments win.

    A caller that pins an input is either replaying a frozen decision or probing
    a counterfactual, and in both cases the pinned value has to survive — so
    knowledge only fills inputs the caller left open. Every directive assigns an
    absolute value rather than a delta, which makes replaying a frozen directive
    list idempotent and lets a past decision be reproduced exactly.

    Returns the merged factor overrides, the resolved demand delta, and the
    applied/skipped directives for the audit trail.
    """
    pinned_factors = set(factor_overrides or {})
    overrides = dict(factor_overrides or {})
    delta = target_daily_demand_delta
    applied: list[dict] = []
    skipped: list[dict] = []

    for directive in directives or []:
        target = str(directive.get("target") or "")
        if target not in KNOWLEDGE_TARGETS:
            raise ValueError(f"engine cannot apply knowledge target {target!r}")
        record = {"target": target, "kind": directive.get("kind"),
                  "knowledge_id": directive.get("knowledge_id"),
                  "value": directive.get("value"), "weight": directive.get("weight")}
        if record["value"] is None:
            skipped.append({**record, "reason": "NO_VALUE"})
            continue
        value = float(record["value"])
        if target.startswith("factor_overrides."):
            key = target.split(".", 1)[1]
            if key in pinned_factors:
                skipped.append({**record, "reason": "CALLER_PINNED"})
                continue
            overrides[key] = value
        elif target == "target_daily_demand_delta":
            if target_daily_demand_delta is not None:
                skipped.append({**record, "reason": "CALLER_PINNED"})
                continue
            delta = value
        elif target == "params.fill_rate":
            if fill_rate_pinned:
                skipped.append({**record, "reason": "CALLER_PINNED"})
                continue
            params["fill_rate"] = config.clamp_fill_rate(value)
        else:  # params.shelf_max
            params["shelf_max"] = max(0, int(round(value)))
        applied.append(record)

    return (overrides or None), delta, applied, skipped


def classify_scenario(category: str, fc: dict, on_promo: bool) -> str:
    if on_promo: return "promo"
    if category in FRESH: return "fresh"
    if fc.get("days", 0) < 30: return "new"
    if fc.get("mean", 0) < 1: return "longtail"
    if fc.get("std", 0) > fc.get("mean", 0) * 1.5: return "fresh"
    return "标品"


def _flow_dates(dt: datetime, flow: str) -> dict:
    """Application / arrival / on-shelf dates for the chosen flow."""
    arrive = dt + timedelta(days=1)
    shelf = dt + timedelta(days=1 if flow == "B" else 2)
    return {"apply_date": dt.strftime("%Y-%m-%d"),
            "arrival_date": arrive.strftime("%Y-%m-%d"),
            "shelf_date": shelf.strftime("%Y-%m-%d")}


def run(shop, sku_info, fc, dt, inventory_snapshot=None, flow="A",
        on_promo=None, params=None, fill_rate=None, factor_overrides=None,
        target_daily_demand_delta=None, knowledge=None):
    cat = sku_info["category"]; goods = sku_info["goods_code"]; trace = []
    flow = "B" if str(flow).upper() == "B" else "A"
    lead_time = FLOW_LEAD[flow]
    on_promo = (fc.get("promo_uplift", 1) > 1.2) if on_promo is None else on_promo

    # Resolve operational parameters: explicit override wins, else per store+SKU config.
    p = dict(config.resolve(shop, goods)) if params is None else dict(params)
    if fill_rate is not None:
        p["fill_rate"] = config.clamp_fill_rate(fill_rate)
    factor_overrides, target_daily_demand_delta, knowledge_applied, knowledge_skipped = (
        _apply_knowledge(knowledge, factor_overrides=factor_overrides,
                         target_daily_demand_delta=target_daily_demand_delta,
                         params=p, fill_rate_pinned=fill_rate is not None))
    p["lead_time"] = lead_time   # driven by the run's flow, not stored config
    fill_rate = p["fill_rate"]
    coverage = int(p.get("coverage", core.COVERAGE))
    dates = _flow_dates(dt, flow)

    # --- Step 1: scenario -----------------------------------------------------
    scen = classify_scenario(cat, fc, on_promo)
    trace.append({"step": 1, "name": "场景识别", "skill": "router", "type": "algo",
                  "delta": 0, "input": f"品类 {cat}", "output": f"场景={scen}"})

    # --- Step 2: reconstruct censored demand (历史信息) ------------------------
    dem = demand.reconstruct_demand(fc.get("mean", 0), fc.get("std", 0),
                                    fc.get("days", 0), on_promo=on_promo)
    trace.append({"step": 2, "name": "历史信息·缺货还原", "skill": "data.reconstruct_demand",
                  "type": "data", "delta": round(dem["uplift"] - 1, 2),
                  "input": f"观测均值 {fc.get('mean', 0)}/标准差 {fc.get('std', 0)}/{fc.get('days', 0)}天",
                  "output": f"真实均值 {dem['true_mean']} ({dem['reason']})",
                  "formula": [
                      f"真实日均 μ₀ = 观测均值 × 缺货还原系数 = {fc.get('mean', 0)} × {dem['uplift']} = {dem['true_mean']}",
                      f"真实标准差 σ₀ = 观测标准差 × √还原系数 = {fc.get('std', 0)} × √{dem['uplift']} = {dem['true_std']}",
                  ]})

    # --- Step 3: knowledge learned from reviewed overrides --------------------
    # Placed ahead of the soft-signal step because that is where most directives
    # land, so the trace reads as "knowledge rewrote the season factor" followed
    # by the soft-signal maths that consumed it.
    if knowledge_applied:
        knowledge_formula = [
            f"{d.get('kind') or d['target']}: {d['target']} = {d['value']}"
            + (f" (权重 {d['weight']})" if d.get("weight") is not None else "")
            for d in knowledge_applied
        ]
    else:
        knowledge_formula = ["无生效知识，沿用引擎默认假设"]
    knowledge_formula += [
        f"跳过: {d.get('kind') or d['target']} ({d['reason']})" for d in knowledge_skipped
    ]
    trace.append({"step": 3, "name": "知识库", "skill": "knowledge.resolve", "type": "data",
                  "delta": 0,
                  "input": f"生效知识 {len(knowledge_applied)} 条"
                           + (f", 跳过 {len(knowledge_skipped)} 条" if knowledge_skipped else ""),
                  "output": ("改写 " + ", ".join(d["target"] for d in knowledge_applied)
                             if knowledge_applied else "无改写"),
                  "formula": knowledge_formula})

    # --- Step 4: soft signals -------------------------------------------------
    soft = factors.combine(cat, dt, fc.get("promo_uplift", 1), on_promo, fc.get("days", 99),
                           factor_overrides=factor_overrides)
    sf = soft["factors"]
    adj = dict(fc)
    if not isinstance(target_daily_demand_delta, (int, float, type(None))):
        raise ValueError("target_daily_demand_delta must be numeric")
    adj["mean"] = round(max(0, dem["true_mean"] * soft["total_delta"] +
                            (target_daily_demand_delta or 0.0)), 2)
    adj["std"] = dem["true_std"]
    trace.append({"step": 4, "name": "软信息Δ", "skill": "soft.combine", "type": "soft",
                  "delta": round(soft["total_delta"] - 1, 2), "input": f"日期 {dt:%Y-%m-%d}",
                  "output": f"总系数 x{soft['total_delta']}",
                  "formula": [
                      f"软信息总系数 = 季节 × 节假日 × 促销 × 新品 = "
                      f"{sf['season']['factor']} × {sf['holiday']['factor']} × "
                      f"{sf['promo']['factor']} × {sf['new']['factor']} = {soft['total_delta']}",
                      f"调整后日均 μ = 真实日均 × 软信息总系数 = "
                      f"{dem['true_mean']} × {soft['total_delta']} = {adj['mean']}",
                  ]})

    # --- Step 5: current inventory -> net available position ------------------
    inv = inventory_snapshot
    if inv is None:
        inv = inventory_feed.get_inventory(shop, goods, fc.get("mean", 0), dates["apply_date"])
    on_hand = max(0, int(round(float(inv.get("on_hand", 0) or 0))))
    in_transit = max(0, int(round(float(inv.get("in_transit", 0) or 0))))
    reserved = max(0, int(round(float(inv.get("reserved", 0) or 0))))
    expiring = max(0, int(round(float(inv.get("expiring", 0) or 0))))
    dte_raw = inv.get("days_to_expiry")
    days_to_expiry = None if dte_raw is None else max(0, int(round(float(dte_raw or 0))))
    recent_zero_days = int(inv.get("recent_zero_days", 0) or 0)

    pos = inventory.inventory_position(on_hand, in_transit, reserved, expiring,
                                       days_to_expiry=days_to_expiry,
                                       daily_demand=adj["mean"])
    phantom = inventory.phantom_check(pos["available"], recent_zero_days, adj["mean"])
    # Phantom stock = shelf empty but system shows on-hand: don't let it block a
    # reorder — treat the physical shelf as depleted (keep only what is in transit).
    policy_position = in_transit if phantom["phantom_suspect"] else pos["available"]
    exp_sellable = pos["expiring_sellable"]
    exp_waste = pos["expiring_waste"]
    if expiring and days_to_expiry is not None:
        step5_inv_formula = [
            f"临期可售 = min(临期, 日均需求 × 剩余效期) = "
            f"min({expiring}, {adj['mean']:g} × {days_to_expiry}天) = {exp_sellable}",
            f"临期损耗 = 临期 − 临期可售 = {expiring} − {exp_sellable} = {exp_waste}",
            f"净可用库存 = 在手 + 在途 − 预占 − 临期损耗 = "
            f"{on_hand} + {in_transit} − {reserved} − {exp_waste} = {pos['available']}",
        ]
    else:
        step5_inv_formula = [
            f"净可用库存 = 在手 + 在途 − 预占 − 临期 = "
            f"{on_hand} + {in_transit} − {reserved} − {expiring} = {pos['available']}",
        ]
    if phantom["phantom_suspect"]:
        step5_inv_formula.append(
            f"疑似虚库存({phantom['reason']})→ 决策库存仅取在途 = {in_transit}")
    else:
        step5_inv_formula.append(f"决策库存 = 净可用 = {policy_position}")
    _exp_in = (f"临期 {expiring}(剩{days_to_expiry}天)"
               if expiring and days_to_expiry is not None else f"临期 {expiring}")
    trace.append({"step": 5, "name": "库存位置", "skill": "data.inventory_position", "type": "data",
                  "delta": 0,
                  "input": f"在手 {on_hand} 在途 {in_transit} 预占 {reserved} {_exp_in}",
                  "output": f"净可用 {pos['available']}"
                            + (f"; {phantom['reason']}" if phantom["phantom_suspect"] else ""),
                  "formula": step5_inv_formula})

    # --- Step 6: (s, S) deterministic sizing ----------------------------------
    res = core.recommend(adj, policy_position, scen, p, lead_time=lead_time)
    trigger_txt = "已触发补货" if res["triggered"] else "未触发(库存高于再补点)"

    cands = res["candidates"]
    chosen = max(cands, key=lambda c: c["qty"]) if scen in ("fresh", "promo") else cands[0]
    qty = chosen["qty"]
    horizon = lead_time + coverage
    exc = res["triggered"] and (qty > max(20, adj["mean"] * horizon * 2))

    # Formula breakdown: every input plugged in so the final qty is fully auditable.
    mu, sigma, z = adj["mean"], adj["std"], res["service_z"]
    analytic_qty = cands[0]["qty"]
    step6_formula = [
        f"安全系数 z = Φ⁻¹(服务水平) = Φ⁻¹({fill_rate:.0%}) = {z}",
        f"安全库存 SS = z·σ·√L = {z} × {sigma} × √{lead_time} = {res['safety_stock']}",
        f"再补点 s = μ·L + z·σ·√L = {mu} × {lead_time} + {res['safety_stock']} = {res['reorder_point']}",
        f"目标库存 S = μ·(L+C) + z·σ·√(L+C) = "
        f"{mu} × {horizon} + {z} × {sigma} × √{horizon} = {res['order_up_to']}"
        f"   (L=提前期{lead_time}天, C=覆盖{coverage}天)",
        f"触发判断: 决策库存 {policy_position} {'≤' if res['triggered'] else '>'} 再补点 {res['reorder_point']}"
        f" → {'触发补货' if res['triggered'] else '不补货, 建议量=0'}",
    ]
    if res["triggered"]:
        step6_formula.append(
            f"原始补货量 = S − 决策库存 = {res['order_up_to']} − {policy_position} = {res['raw_qty']}")
        step6_formula.append(
            f"分析法建议量 = ⌈max(原始量, MOQ) / 箱规⌉ × 箱规 (上限{p['shelf_max']}) = "
            f"⌈max({res['raw_qty']}, {p['moq']}) / {p['case_pack']}⌉ × {p['case_pack']} = {analytic_qty}")
        if chosen["method"] != cands[0]["method"]:
            step6_formula.append(
                f"{scen}场景取 分析法({analytic_qty}) 与 蒙特卡洛P90({chosen['qty']}) 的较大值 → 建议量 = {qty}")
        else:
            step6_formula.append(f"最终建议补货量 = {qty} 件")

    trace.append({"step": 6, "name": "确定性算量(s,S)", "skill": "algo.recommend", "type": "algo",
                  "delta": 0,
                  "input": f"均值 {adj['mean']} 净可用 {policy_position} 服务水平 {fill_rate:.0%}"
                           f"(z={res['service_z']}) 箱规 {p['case_pack']} "
                           f"流程{flow}·提前期 {lead_time}+覆盖 {coverage}天",
                  "output": f"再补点 s={res['reorder_point']} 目标 S={res['order_up_to']} "
                            f"→ {trigger_txt}",
                  "formula": step6_formula})

    expl = (f"{sku_info['goods_name']} 场景[{scen}]; 缺货还原x{dem['uplift']}; 软信息x{soft['total_delta']}; "
            f"服务水平{fill_rate:.0%}; 箱规{p['case_pack']}/MOQ{p['moq']}/上限{p['shelf_max']}; "
            f"净可用{policy_position}(在手{on_hand}); 再补点{res['reorder_point']}/目标{res['order_up_to']}; "
            f"流程{flow}({FLOW_DESC[flow]},L={lead_time}); "
            + (f"建议补货{qty}({chosen['method']}),{dates['shelf_date']}上架"
               if res["triggered"] else "库存充足,今日不补"))

    return {"shop": shop, "sku": goods, "sku_name": sku_info["goods_name"],
            "scenario": scen, "flow": flow, "lead_time": lead_time,
            "apply_date": dates["apply_date"], "arrival_date": dates["arrival_date"],
            "shelf_date": dates["shelf_date"],
            "candidates": cands, "chosen_qty": qty, "final_qty": qty,
            "triggered": res["triggered"], "trigger": res["triggered"],
            "reorder_point": res["reorder_point"], "order_up_to": res["order_up_to"],
            "safety_stock": res["safety_stock"], "target_stock": res["order_up_to"],
            "position": policy_position, "inventory": {
                "on_hand": on_hand, "in_transit": in_transit, "reserved": reserved,
                "expiring": expiring, "days_to_expiry": days_to_expiry,
                "expiring_sellable": exp_sellable, "expiring_waste": exp_waste,
                "recent_zero_days": recent_zero_days,
                "available": pos["available"], "phantom_suspect": phantom["phantom_suspect"],
                "source": inv.get("source", "synthetic"),
                "overridden": inv.get("overridden", [])},
            "demand": dem,
            "knowledge_applied": knowledge_applied,
            "knowledge_skipped": knowledge_skipped,
            "fill_rate": res["fill_rate"], "service_z": res["service_z"], "params": p,
            "explanation": expl, "summary": expl, "exception": exc, "engine": "algo",
            "trace_id": uuid.uuid4().hex[:8], "steps": trace, "trace": trace}
