"""Tests for broker adapters — constructor + Protocol compliance, no network."""
from __future__ import annotations

from decimal import Decimal


def test_alpaca_constructs_and_satisfies_protocol():
    from stratscout.engine.brokers import AlpacaAdapter, AlpacaCredentials, BrokerAdapter
    a = AlpacaAdapter(AlpacaCredentials(api_key="x", api_secret="y", paper=True))
    assert a.name == "alpaca"
    assert a.paper is True
    # Confirms Protocol structurally
    _: BrokerAdapter = a  # noqa


def test_schwab_constructs_and_satisfies_protocol():
    from stratscout.engine.brokers import SchwabAdapter, SchwabCredentials, BrokerAdapter
    s = SchwabAdapter(SchwabCredentials(
        app_key="k", app_secret="s", refresh_token="r", account_number="1234",
    ))
    assert s.name == "schwab"
    assert s.paper is False
    _: BrokerAdapter = s  # noqa


def test_make_broker_factory():
    from stratscout.engine.brokers import make_broker, AlpacaAdapter, SchwabAdapter

    a = make_broker("alpaca", api_key="x", api_secret="y", paper=True)
    assert isinstance(a, AlpacaAdapter)

    s = make_broker("schwab", app_key="k", app_secret="s", refresh_token="r", account_number="0")
    assert isinstance(s, SchwabAdapter)


def test_make_broker_rejects_unknown():
    from stratscout.engine.brokers import make_broker
    import pytest
    with pytest.raises(ValueError):
        make_broker("robinhood")


def test_quote_mid_calculation():
    from stratscout.engine.brokers import Quote
    q = Quote(symbol="SPY", bid=Decimal("100.00"), ask=Decimal("100.10"), last=Decimal("100.05"))
    assert q.mid == Decimal("100.05")


def test_alpaca_market_order_rejects_zero_qty():
    from stratscout.engine.brokers import AlpacaAdapter, AlpacaCredentials, BrokerError
    a = AlpacaAdapter(AlpacaCredentials(api_key="x", api_secret="y", paper=True))
    import pytest
    with pytest.raises(BrokerError):
        a.place_market_order("SPY", 0, "BUY")


def test_alpaca_market_order_rejects_bad_side():
    from stratscout.engine.brokers import AlpacaAdapter, AlpacaCredentials, BrokerError
    a = AlpacaAdapter(AlpacaCredentials(api_key="x", api_secret="y", paper=True))
    import pytest
    with pytest.raises(BrokerError):
        a.place_market_order("SPY", 1, "HODL")
