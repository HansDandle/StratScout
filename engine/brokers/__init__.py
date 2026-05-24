"""Broker adapters. All implement stratscout.engine.brokers.base.BrokerAdapter."""
from stratscout.engine.brokers.base import (
    Account, BrokerAdapter, BrokerError, Position, Quote,
)
from stratscout.engine.brokers.alpaca import AlpacaAdapter, AlpacaCredentials
from stratscout.engine.brokers.schwab import SchwabAdapter, SchwabCredentials

__all__ = [
    "Account", "BrokerAdapter", "BrokerError", "Position", "Quote",
    "AlpacaAdapter", "AlpacaCredentials",
    "SchwabAdapter", "SchwabCredentials",
]


def make_broker(kind: str, **kwargs) -> BrokerAdapter:
    """Factory. Returns the right adapter given a 'kind' string ('alpaca'|'schwab')."""
    if kind == "alpaca":
        return AlpacaAdapter(AlpacaCredentials(**kwargs))
    if kind == "schwab":
        return SchwabAdapter(SchwabCredentials(**kwargs))
    raise ValueError(f"Unknown broker: {kind}")
