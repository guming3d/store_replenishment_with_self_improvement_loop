"""Tests for the ground-truth and knowledge-confidence half of the loop."""
from __future__ import annotations

import pytest

from attribution import knowledge, outcomes


# ---- Judgement window ----

def test_window_opens_the_day_after_the_decision():
    # Demand on the decision day is served by the position already measured, so
    # counting it would charge the order for demand it was never sized to cover.
    assert outcomes.demand_window("2026-07-23", 5) == ("2026-07-24", "2026-07-28")


def test_window_length_matches_the_horizon():
    start, end = outcomes.demand_window("2026-07-23", 3)
    assert outcomes.window_dates(start, end) == [
        "2026-07-24", "2026-07-25", "2026-07-26"]


def test_horizon_comes_from_the_frozen_snapshot_not_todays_config():
    assert outcomes.horizon_days_from_snapshot({"flow": "A", "params": {"coverage": 3}}) == 5
    assert outcomes.horizon_days_from_snapshot({"flow": "B", "params": {"coverage": 3}}) == 4
    # A snapshot predating the field must not silently borrow current settings.
    assert outcomes.horizon_days_from_snapshot({}, default=7) == 7


# ---- Realised demand ----

def test_lost_sales_count_as_demand():
    """An empty shelf is unmet demand, not low demand."""
    sold = {"2026-07-24": 4.0, "2026-07-25": 0.0}
    lost = {"2026-07-25": 6.0}
    demand = outcomes.realised_demand(sold, "2026-07-24", "2026-07-25", lost_sales=lost)
    assert demand["units_sold"] == 4.0
    assert demand["actual_demand"] == 10.0
    assert demand["complete"] is True


def test_missing_days_are_reported_rather_than_assumed_zero():
    demand = outcomes.realised_demand({"2026-07-24": 4.0}, "2026-07-24", "2026-07-26")
    assert demand["complete"] is False
    assert demand["missing_days"] == ["2026-07-25", "2026-07-26"]


# ---- Scoring ----

def _demand(units: float, days: int = 2) -> dict:
    daily = {f"2026-07-{24 + offset}": units / days for offset in range(days)}
    return outcomes.realised_demand(daily, "2026-07-24", f"2026-07-{23 + days}")


def test_open_window_is_pending_and_never_a_tie():
    inputs = outcomes.OutcomeInputs(recommended_qty=10, ordered_qty=15,
                                    opening_position=0, horizon_days=3, case_pack=1)
    scored = outcomes.score_outcome(inputs, outcomes.realised_demand(
        {"2026-07-24": 5.0}, "2026-07-24", "2026-07-26"))
    assert scored["status"] == "PARTIAL"
    assert scored["verdict"] == "PENDING"
    assert scored["ideal_qty"] is None


def test_human_wins_when_the_override_was_closer():
    inputs = outcomes.OutcomeInputs(recommended_qty=10, ordered_qty=20,
                                    opening_position=0, horizon_days=2, case_pack=1)
    scored = outcomes.score_outcome(inputs, _demand(20))
    assert scored["status"] == "COMPLETE"
    assert scored["ideal_qty"] == 20
    assert scored["verdict"] == "HUMAN_BETTER"
    assert scored["stockout_units"] == 0


def test_engine_wins_when_the_override_overshot():
    inputs = outcomes.OutcomeInputs(recommended_qty=20, ordered_qty=40,
                                    opening_position=0, horizon_days=2, case_pack=1)
    scored = outcomes.score_outcome(inputs, _demand(20))
    assert scored["verdict"] == "ENGINE_BETTER"
    assert scored["overstock_units"] == 20


def test_case_pack_granularity_is_a_tie_not_a_win():
    """Neither side controls case-pack rounding, so it cannot decide the verdict."""
    inputs = outcomes.OutcomeInputs(recommended_qty=20, ordered_qty=24,
                                    opening_position=0, horizon_days=2, case_pack=12)
    scored = outcomes.score_outcome(inputs, _demand(22))
    assert scored["verdict"] == "TIE"


def test_opening_position_is_credited_against_demand():
    inputs = outcomes.OutcomeInputs(recommended_qty=10, ordered_qty=0,
                                    opening_position=30, horizon_days=2, case_pack=1)
    scored = outcomes.score_outcome(inputs, _demand(20))
    # Stock on hand already covered the window, so ordering nothing was right.
    assert scored["ideal_qty"] == 0
    assert scored["verdict"] == "HUMAN_BETTER"


def test_ideal_quantity_never_goes_negative():
    assert outcomes.hindsight_optimal_qty(5, 40) == 0


# ---- Accuracy board ----

def test_accuracy_summary_ignores_open_windows():
    rows = [
        {"status": "COMPLETE", "verdict": "HUMAN_BETTER", "engine_error": -10.0,
         "human_error": 0.0, "ideal_qty": 20.0, "stockout_units": 10.0, "overstock_units": 0.0},
        {"status": "PARTIAL", "verdict": "PENDING", "engine_error": None,
         "human_error": None, "ideal_qty": None},
    ]
    summary = outcomes.accuracy_summary(rows)
    assert summary["sample_size"] == 1
    assert summary["pending_count"] == 1
    assert summary["engine_mae"] == 10.0
    assert summary["human_mae"] == 0.0
    # A positive gain means the humans were closer, which is the signal worth learning.
    assert summary["accuracy_gain_units"] == 10.0


def test_accuracy_summary_drops_zero_ideal_rows_from_mape():
    rows = [
        {"status": "COMPLETE", "verdict": "TIE", "engine_error": 1.0, "human_error": 1.0,
         "ideal_qty": 0.0, "stockout_units": 0.0, "overstock_units": 1.0},
    ]
    summary = outcomes.accuracy_summary(rows)
    assert summary["engine_mae"] == 1.0
    assert summary["engine_mape"] is None


# ---- Knowledge confidence ----

def test_no_evidence_earns_no_weight():
    assert knowledge.effective_weight(0, 0) == 0.0


def test_a_couple_of_lucky_cases_do_not_promote_knowledge():
    """Two hits out of two is an anecdote; a point estimate would score it 1.0."""
    assert knowledge.wilson_lower_bound(2, 2) < 0.5
    assert knowledge.effective_weight(2, 0) == 0.0


def test_weight_rises_as_evidence_accumulates():
    weak = knowledge.effective_weight(8, 2)
    strong = knowledge.effective_weight(80, 20)
    assert 0 < weak < strong < 1


def test_contradicted_knowledge_loses_its_weight():
    assert knowledge.effective_weight(3, 12) == 0.0


def test_promotion_requires_shadow_evaluation_first():
    assert knowledge.next_status("CANDIDATE", sample_size=3, weight=1.0) == "CANDIDATE"
    assert knowledge.next_status("SHADOW", sample_size=10, weight=0.4) == "ACTIVE"


def test_active_knowledge_retires_when_evidence_turns():
    assert knowledge.next_status("ACTIVE", sample_size=20, weight=0.0) == "RETIRED"


def test_retirement_is_terminal():
    assert knowledge.next_status("RETIRED", sample_size=100, weight=1.0) == "RETIRED"


def test_apply_outcome_folds_one_result_into_the_posterior():
    entry = {"posterior": knowledge.posterior(0, 0), "status": "CANDIDATE"}
    for _ in range(6):
        entry.update(knowledge.apply_outcome(entry, improved=True))
    assert entry["posterior"]["hit_count"] == 6
    assert entry["status"] in {"SHADOW", "ACTIVE"}


def test_blend_phases_knowledge_in_rather_than_switching_it_on():
    assert knowledge.blend(1.0, 2.0, 0.0) == 1.0
    assert knowledge.blend(1.0, 2.0, 0.5) == 1.5
    assert knowledge.blend(1.0, 2.0, 1.0) == 2.0


def test_every_kind_maps_to_an_engine_input():
    """Knowledge the engine cannot replay could never be verified or promoted."""
    for kind in knowledge.KNOWLEDGE_KINDS:
        assert knowledge.KIND_ENGINE_TARGET[kind]


# ---- Scope resolution ----

def test_store_wide_knowledge_does_not_leak_across_stores():
    scope = {"shop_code": "1011", "goods_code": None}
    assert knowledge.scope_matches(scope, shop_code="1011", goods_code="653269")
    assert not knowledge.scope_matches(scope, shop_code="1012", goods_code="653269")


def test_sku_knowledge_does_not_leak_to_neighbouring_skus():
    scope = {"shop_code": "1011", "goods_code": "653269"}
    assert not knowledge.scope_matches(scope, shop_code="1011", goods_code="653270")


def test_knowledge_respects_its_date_range():
    scope = {"shop_code": "1011", "applies_from": "2026-06-01", "applies_to": "2026-08-31"}
    assert knowledge.scope_matches(scope, shop_code="1011", goods_code="x", on_date="2026-07-23")
    assert not knowledge.scope_matches(
        scope, shop_code="1011", goods_code="x", on_date="2026-09-01")


def test_narrower_scope_outranks_broader_scope():
    store_wide = {"shop_code": "1011"}
    sku_level = {"shop_code": "1011", "goods_code": "653269"}
    assert knowledge.scope_specificity(sku_level) > knowledge.scope_specificity(store_wide)


@pytest.mark.parametrize("pack,expected", [(None, 1.0), (1, 1.0), (12, 6.0), (2, 1.0)])
def test_tolerance_is_half_a_case_floored_at_one_unit(pack, expected):
    assert outcomes.tolerance_units(pack) == expected
