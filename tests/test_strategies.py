"""Tests for strategies storage + preflight."""
from __future__ import annotations

import pytest


@pytest.fixture
def fresh_strategy():
    """Create a strategy, yield it, then delete in teardown."""
    from stratscout.engine.strategies import save_strategy, delete_strategy
    s = save_strategy(
        name="pytest strategy",
        kind="etf",
        params={"risk_on_pool": ["SPY", "QQQ"], "agg_bil_lookback": 60},
        notes="",
    )
    yield s
    delete_strategy(s.id)


def test_save_and_get_round_trip(fresh_strategy):
    from stratscout.engine.strategies import get_strategy
    got = get_strategy(fresh_strategy.id)
    assert got is not None
    assert got.name == "pytest strategy"
    assert got.kind == "etf"
    assert got.trade_mode == "off"
    assert got.params["risk_on_pool"] == ["SPY", "QQQ"]


def test_update_strategy_changes_trade_mode(fresh_strategy):
    from stratscout.engine.strategies import update_strategy, get_strategy
    update_strategy(fresh_strategy.id, trade_mode="paper")
    got = get_strategy(fresh_strategy.id)
    assert got.trade_mode == "paper"


def test_update_strategy_rejects_invalid_trade_mode(fresh_strategy):
    from stratscout.engine.strategies import update_strategy
    with pytest.raises(ValueError):
        update_strategy(fresh_strategy.id, trade_mode="rocket-launch")


def test_list_strategies_excludes_archived_by_default(fresh_strategy):
    from stratscout.engine.strategies import update_strategy, list_strategies
    update_strategy(fresh_strategy.id, archived=True)
    ids = [s.id for s in list_strategies(include_archived=False)]
    assert fresh_strategy.id not in ids
    ids = [s.id for s in list_strategies(include_archived=True)]
    assert fresh_strategy.id in ids


def test_preflight_fails_without_walk_forward(fresh_strategy):
    from stratscout.engine.preflight import evaluate
    report = evaluate(fresh_strategy.id)
    assert report is not None
    assert report.passed is False
    # Should have a check for missing walk-forward
    ids = {c.id for c in report.checks}
    assert "walk_forward_present" in ids
    assert "risk_acknowledged" in ids


def test_preflight_risk_check_toggles_with_notes(fresh_strategy):
    from stratscout.engine.preflight import evaluate
    from stratscout.engine.strategies import update_strategy

    # Initially not acknowledged
    r = evaluate(fresh_strategy.id)
    risk = next(c for c in r.checks if c.id == "risk_acknowledged")
    assert risk.passed is False

    # Add ACK_RISK token to notes
    update_strategy(fresh_strategy.id, notes="ACK_RISK read it understood it")
    r = evaluate(fresh_strategy.id)
    risk = next(c for c in r.checks if c.id == "risk_acknowledged")
    assert risk.passed is True


def test_preflight_returns_none_for_missing_strategy():
    from stratscout.engine.preflight import evaluate
    assert evaluate(99999999) is None
