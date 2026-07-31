"""The return leg of the review loop: knowledge that reaches the engine.

Everything upstream of this file -- mining candidates, recording verdicts,
scoring outcomes -- is bookkeeping until an approved entry changes what the
store is told to order. Before these tests existed the engine contained no
reference to knowledge at all, so an entry could be approved, promoted to
ACTIVE and still leave every future recommendation untouched.

The second property tested here is the counterweight to the first. A frozen
snapshot must replay to what was decided at the time, not to what the engine
would decide now, or the attribution baseline would drift every time the
knowledge base grew and take every candidate calibration with it.
"""
from __future__ import annotations

from datetime import date

import pytest

import engine
from attribution import knowledge as knowledge_math
from attribution.deterministic import replay_engine

SNAPSHOT = {
    "shop": "shop-1",
    "sku_info": {"goods_code": "sku-1", "goods_name": "雪花啤酒", "category": "啤酒"},
    "forecast": {"mean": 3, "std": 1, "days": 60},
    "decision_date": "2026-07-23",
    "inventory_snapshot": {"on_hand": 0, "in_transit": 0, "reserved": 0, "expiring": 0},
    "params": {"fill_rate": 0.95, "coverage": 2, "case_pack": 1, "moq": 0, "shelf_max": 999},
}

RUN_ARGS = ("shop-1", SNAPSHOT["sku_info"], SNAPSHOT["forecast"], date(2026, 7, 23))
RUN_KWARGS = {"inventory_snapshot": SNAPSHOT["inventory_snapshot"],
              "params": SNAPSHOT["params"]}


def _entry(kind: str, proposed: float, *, prior: float = 1.0, weight: float = 1.0) -> dict:
    """One resolved knowledge entry in the shape ``active_knowledge_for`` returns."""
    return {
        "knowledge_id": f"k-{kind}", "kind": kind,
        "prior_value": prior, "proposed_value": proposed,
        "effective_weight": weight,
        "engine_target": knowledge_math.KIND_ENGINE_TARGET[kind],
        "blended_value": knowledge_math.blend(prior, proposed, weight),
    }


def _directives(*entries: dict) -> list[dict]:
    return knowledge_math.engine_directives(list(entries))["directives"]


# --- the vocabulary the two layers share -------------------------------------------

def test_every_target_the_knowledge_layer_emits_is_one_the_engine_implements():
    """Guards the drift that would make an approved entry a silent no-op."""
    assert knowledge_math.ENGINE_APPLICABLE_TARGETS == engine.KNOWLEDGE_TARGETS


def test_a_kind_the_engine_cannot_reach_is_reported_rather_than_dropped():
    resolved = knowledge_math.engine_directives([_entry("SUBSTITUTION_RATE", 0.4)])
    assert resolved["directives"] == []
    assert [item["reason"] for item in resolved["unsupported"]] == ["ENGINE_CANNOT_APPLY"]
    assert resolved["unsupported"][0]["kind"] == "SUBSTITUTION_RATE"


def test_the_engine_refuses_a_directive_it_cannot_apply():
    # Silently ignoring it would look exactly like knowledge that had no effect.
    with pytest.raises(ValueError, match="cannot apply knowledge target"):
        engine.run(*RUN_ARGS, **RUN_KWARGS,
                   knowledge=[{"target": "seed.substitutions.transfer_rate", "value": 0.4}])


# --- knowledge actually moving the recommendation ----------------------------------

def test_active_knowledge_changes_what_the_store_is_told_to_order():
    without = engine.run(*RUN_ARGS, **RUN_KWARGS)
    with_knowledge = engine.run(
        *RUN_ARGS, **RUN_KWARGS,
        knowledge=_directives(_entry("SEASONAL_FACTOR", 2.4, prior=1.25)))
    assert with_knowledge["chosen_qty"] > without["chosen_qty"]
    assert [item["kind"] for item in with_knowledge["knowledge_applied"]] == ["SEASONAL_FACTOR"]
    assert without["knowledge_applied"] == []


def test_a_half_weighted_entry_lands_between_the_prior_and_the_proposal():
    """Weight is how an entry is phased in, so it has to reach the engine's input."""
    half = _entry("SEASONAL_FACTOR", 2.4, prior=1.25, weight=0.5)
    directive = _directives(half)[0]
    assert directive["value"] == pytest.approx(1.825)
    assert 1.25 < directive["value"] < 2.4

    full = engine.run(*RUN_ARGS, **RUN_KWARGS,
                      knowledge=_directives(_entry("SEASONAL_FACTOR", 2.4, prior=1.25)))
    partial = engine.run(*RUN_ARGS, **RUN_KWARGS, knowledge=[directive])
    baseline = engine.run(*RUN_ARGS, **RUN_KWARGS)
    assert baseline["chosen_qty"] < partial["chosen_qty"] < full["chosen_qty"]


def test_knowledge_is_recorded_as_its_own_auditable_step():
    result = engine.run(*RUN_ARGS, **RUN_KWARGS,
                        knowledge=_directives(_entry("SEASONAL_FACTOR", 2.4, prior=1.25)))
    step = next(item for item in result["trace"] if item["skill"] == "knowledge.resolve")
    assert "factor_overrides.season" in step["output"]
    assert any("SEASONAL_FACTOR" in line for line in step["formula"])
    # The step is emitted either way, so step numbers do not shift between runs.
    plain = engine.run(*RUN_ARGS, **RUN_KWARGS)
    assert [item["step"] for item in plain["trace"]] == [item["step"] for item in result["trace"]]


def test_fill_rate_and_shelf_max_knowledge_reaches_the_resolved_parameters():
    result = engine.run(*RUN_ARGS, **RUN_KWARGS,
                        knowledge=_directives(_entry("SHELF_MAX", 4, prior=999)))
    assert result["params"]["shelf_max"] == 4
    assert result["chosen_qty"] <= 4


# --- what knowledge is not allowed to overrule --------------------------------------

def test_an_input_the_caller_pinned_survives_contradicting_knowledge():
    """Counterfactual replay pins factors; knowledge must not undo the probe."""
    result = engine.run(*RUN_ARGS, **RUN_KWARGS, factor_overrides={"season": 1.0},
                        knowledge=_directives(_entry("SEASONAL_FACTOR", 2.4, prior=1.25)))
    neutral = engine.run(*RUN_ARGS, **RUN_KWARGS, factor_overrides={"season": 1.0})
    assert result["chosen_qty"] == neutral["chosen_qty"]
    assert result["knowledge_applied"] == []
    assert [item["reason"] for item in result["knowledge_skipped"]] == ["CALLER_PINNED"]


def test_knowledge_for_one_input_still_applies_when_another_is_pinned():
    result = engine.run(
        *RUN_ARGS, **RUN_KWARGS, factor_overrides={"season": 1.0},
        knowledge=_directives(_entry("SEASONAL_FACTOR", 2.4, prior=1.25),
                              _entry("SHELF_MAX", 4, prior=999)))
    assert [item["kind"] for item in result["knowledge_applied"]] == ["SHELF_MAX"]
    assert [item["kind"] for item in result["knowledge_skipped"]] == ["SEASONAL_FACTOR"]


# --- the frozen past stays frozen ----------------------------------------------------

def test_a_replay_reproduces_the_knowledge_frozen_into_the_snapshot():
    frozen = dict(SNAPSHOT,
                  knowledge_applied=_directives(_entry("SEASONAL_FACTOR", 2.4, prior=1.25)))
    assert replay_engine(frozen)["final_qty"] == engine.run(
        *RUN_ARGS, **RUN_KWARGS,
        knowledge=_directives(_entry("SEASONAL_FACTOR", 2.4, prior=1.25)))["final_qty"]


def test_a_snapshot_taken_before_an_entry_existed_never_inherits_it():
    """Knowledge approved after the fact must not rewrite the past baseline."""
    assert "knowledge_applied" not in SNAPSHOT
    assert replay_engine(SNAPSHOT)["final_qty"] == engine.run(
        *RUN_ARGS, **RUN_KWARGS)["final_qty"]


def test_replaying_a_frozen_directive_twice_is_idempotent():
    """Directives assign absolute values, which is what makes a replay faithful."""
    directives = _directives(_entry("SEASONAL_FACTOR", 2.4, prior=1.25))
    once = engine.run(*RUN_ARGS, **RUN_KWARGS, knowledge=directives)
    twice = engine.run(*RUN_ARGS, **dict(RUN_KWARGS, params=once["params"]),
                       knowledge=directives)
    assert once["chosen_qty"] == twice["chosen_qty"]
