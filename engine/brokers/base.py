"""Broker adapter interface.

Every broker integration implements this Protocol so the rest of the engine
(rebalancer, live job, dashboard) can target any broker through the same calls.

Method contract:
  - All money quantities are Decimal, not float.
  - All quantities (shares) are int. Fractional shares postponed to v0.3.
  - place_market_order / place_limit_buy raise BrokerError on rejection; UI/log
    decides whether to retry, abort, or surface to user.
  - Network failures should propagate as BrokerError, not return success silently.

The adapter holds its own state (auth token, account hash) so callers don't have
to thread credentials through every call.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


class BrokerError(Exception):
    """Broker-side failure: rejected order, auth failure, rate-limit, etc."""


@dataclass
class Quote:
    symbol: str
    bid: Decimal
    ask: Decimal
    last: Decimal

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2


@dataclass
class Position:
    symbol: str
    qty: int
    avg_price: Decimal


@dataclass
class Account:
    account_id: str
    cash: Decimal
    equity: Decimal


class BrokerAdapter(Protocol):
    """All broker integrations conform to this interface."""

    name: str  # "schwab" | "alpaca" | ...
    paper: bool  # True for paper accounts

    def connect(self) -> None:
        """Establish session. Refreshes tokens if needed. Raises BrokerError on failure."""
        ...

    def get_account(self) -> Account:
        """Return cash + equity for the connected account."""
        ...

    def get_positions(self) -> list[Position]:
        """Return currently-held positions."""
        ...

    def get_quote(self, symbol: str) -> Quote:
        """Real-time quote for a single symbol."""
        ...

    def place_market_order(self, symbol: str, qty: int, side: str) -> str:
        """Place a market order. side='BUY'|'SELL'. Returns broker order_id."""
        ...

    def place_limit_order(self, symbol: str, qty: int, side: str, limit_price: Decimal,
                          duration: str = "DAY") -> str:
        """Place a limit order. Returns broker order_id."""
        ...

    def cancel_order(self, order_id: str) -> None:
        """Cancel an open order."""
        ...

    def list_open_orders(self) -> list[dict]:
        """Return broker-native open-order representations (for the UI / rebalancer)."""
        ...
