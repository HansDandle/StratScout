"""Tests for the trade-orders bookkeeping path (dry-run + activity log)."""
from __future__ import annotations

import pytest


@pytest.fixture
def strategy_for_trader():
    from stratscout.engine.strategies import save_strategy, delete_strategy
    s = save_strategy(
        name="pytest trader strategy",
        kind="etf",
        params={
            "agg_bil_lookback": 60, "tlt_bil_lookback": 20,
            "risk_on_rsi_window": 10, "risk_off_rsi_window": 20,
            "risk_on_rsi_direction": "lowest", "risk_off_rsi_direction": "lowest",
            "n_risk_on": 2, "n_risk_off_rising": 2, "n_risk_off_falling": 2,
            "min_hold_days": 2,
            "rising_rate_include_uup": True,
            "sector_diverse": True,
            "risk_on_pool": ["SOXL", "TQQQ"],
            "risk_off_rising_pool": ["QID"],
            "risk_off_falling_pool": ["GLD", "TLT"],
        },
    )
    yield s
    delete_strategy(s.id)


def test_record_order_round_trip(strategy_for_trader):
    from stratscout.engine.trader import record_order, list_orders, delete_orders
    delete_orders(strategy_for_trader.id)  # start from a clean slate
    record_order(
        strategy_id=strategy_for_trader.id,
        mode="dry", action="TARGET", symbol="SPY",
        qty=None, status="recorded", message="manual",
    )
    rows = list_orders(strategy_for_trader.id)
    assert len(rows) == 1
    o = rows[0]
    assert o.action == "TARGET"
    assert o.symbol == "SPY"
    assert o.mode == "dry"
    assert o.status == "recorded"


def test_orders_endpoint_returns_404_for_missing_strategy():
    from fastapi.testclient import TestClient
    from stratscout.api.app import app
    client = TestClient(app)
    r = client.get("/strategies/99999999/orders")
    assert r.status_code == 404


def test_run_now_endpoint_404s_for_missing_strategy():
    from fastapi.testclient import TestClient
    from stratscout.api.app import app
    client = TestClient(app)
    r = client.post("/strategies/99999999/run-now")
    assert r.status_code == 404


def test_run_now_endpoint_rejects_unknown_mode(strategy_for_trader):
    from fastapi.testclient import TestClient
    from stratscout.api.app import app
    client = TestClient(app)
    r = client.post(f"/strategies/{strategy_for_trader.id}/run-now?mode=rocket")
    assert r.status_code == 400


def test_run_strategy_smallcap_paper_returns_not_implemented():
    """Smallcap doesn't have a paper execution path yet — should 400 cleanly,
    not 500."""
    from fastapi.testclient import TestClient
    from stratscout.api.app import app
    from stratscout.engine.strategies import save_strategy, delete_strategy

    s = save_strategy(
        name="pytest smallcap strategy",
        kind="smallcap",
        params={"vol_lookback": 20, "vol_mult": 5.0, "hold_days": 5, "max_positions": 4, "require_green": False},
    )
    try:
        client = TestClient(app)
        r = client.post(f"/strategies/{s.id}/run-now?mode=paper")
        assert r.status_code == 400
        assert "smallcap" in r.json()["detail"].lower()
    finally:
        delete_strategy(s.id)


def test_run_strategy_paper_falls_back_without_creds(monkeypatch, strategy_for_trader):
    """When Alpaca keys are absent, paper run records rejected rows and sets
    fell_back_to_dry=True instead of crashing."""
    from stratscout.engine import trader

    # Force compute_etf_targets to return a known list so we don't depend on
    # whatever feathers happen to be cached locally.
    monkeypatch.setattr(
        trader, "_compute_etf_targets",
        lambda params: (["SOXL", "TQQQ"], "risk-on", "2026-05-01"),
    )
    # Pretend no Alpaca credentials exist.
    from stratscout.engine import credentials as creds
    monkeypatch.setattr(creds, "get", lambda provider, field: None)

    r = trader.run_strategy(strategy_for_trader.id, mode="paper", note="pytest")
    assert isinstance(r, trader.ExecutionResult)
    assert r.fell_back_to_dry is True
    assert r.placed == 0
    assert r.failed == len(r.order_ids) > 0


def test_run_strategy_paper_dispatches_diff(monkeypatch, strategy_for_trader):
    """When credentials are present, paper run reads positions, places SELL
    for excess holdings + BUY for new targets, and records each."""
    from decimal import Decimal
    from stratscout.engine import trader
    from stratscout.engine.brokers.base import Account, Position, Quote

    monkeypatch.setattr(
        trader, "_compute_etf_targets",
        lambda params: (["SOXL", "TQQQ"], "risk-on", "2026-05-01"),
    )

    class FakeAdapter:
        """Minimal stub: in TQQQ + an unwanted SPY → expect SELL SPY, BUY SOXL."""
        name = "fake"
        paper = True
        placed: list[tuple[str, int, str]] = []

        def connect(self): pass
        def get_account(self):
            return Account(account_id="X", cash=Decimal("10000"), equity=Decimal("10000"))
        def get_positions(self):
            return [Position(symbol="SPY", qty=5, avg_price=Decimal("400")),
                    Position(symbol="TQQQ", qty=10, avg_price=Decimal("50"))]
        def get_quote(self, symbol):
            return Quote(symbol=symbol, bid=Decimal("50"), ask=Decimal("50"), last=Decimal("50"))
        def place_market_order(self, symbol, qty, side):
            FakeAdapter.placed.append((symbol, qty, side))
            return f"order-{symbol}-{side}"

    FakeAdapter.placed = []
    monkeypatch.setattr(trader, "_adapter_for_mode", lambda mode: FakeAdapter())

    r = trader.run_strategy(strategy_for_trader.id, mode="paper", note="pytest")
    assert isinstance(r, trader.ExecutionResult)
    assert r.fell_back_to_dry is False
    assert r.failed == 0
    # Expected: SELL SPY (not in target) and BUY SOXL (new target). TQQQ stays.
    actions = {(sym, side) for sym, _, side in FakeAdapter.placed}
    assert ("SPY", "SELL") in actions
    assert ("SOXL", "BUY") in actions
    assert ("TQQQ", "BUY") not in actions  # already held — no churn
