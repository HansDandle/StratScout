"""Alpaca broker adapter.

Auth: API key + secret pair (no OAuth). Free paper accounts available.
Docs: https://docs.alpaca.markets/reference/

Endpoints used:
  GET  /v2/account
  GET  /v2/positions
  GET  /v2/stocks/{symbol}/quotes/latest    (market data API)
  POST /v2/orders
  GET  /v2/orders?status=open
  DELETE /v2/orders/{id}
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import requests

from stratscout.engine.brokers.base import (
    Account, BrokerAdapter, BrokerError, Position, Quote,
)


@dataclass
class AlpacaCredentials:
    api_key: str
    api_secret: str
    paper: bool = True


class AlpacaAdapter:
    """Implements BrokerAdapter for Alpaca. Paper + live."""

    name = "alpaca"

    def __init__(self, creds: AlpacaCredentials):
        self._creds = creds
        self.paper = creds.paper
        self._trading_base = (
            "https://paper-api.alpaca.markets" if creds.paper
            else "https://api.alpaca.markets"
        )
        self._data_base = "https://data.alpaca.markets"
        self._session = requests.Session()
        self._session.headers.update({
            "APCA-API-KEY-ID":     creds.api_key,
            "APCA-API-SECRET-KEY": creds.api_secret,
            "accept":              "application/json",
        })

    def connect(self) -> None:
        # Alpaca uses static keys, so "connect" is just a healthcheck.
        try:
            self.get_account()
        except Exception as e:
            raise BrokerError(f"Alpaca auth failed: {e}") from e

    def _get(self, base: str, path: str, **params: Any) -> Any:
        r = self._session.get(f"{base}{path}", params=params, timeout=10)
        if r.status_code >= 400:
            raise BrokerError(f"Alpaca GET {path} → {r.status_code}: {r.text[:200]}")
        return r.json()

    def _post(self, path: str, payload: dict) -> Any:
        r = self._session.post(f"{self._trading_base}{path}", json=payload, timeout=10)
        if r.status_code >= 400:
            raise BrokerError(f"Alpaca POST {path} → {r.status_code}: {r.text[:200]}")
        return r.json()

    def _delete(self, path: str) -> None:
        r = self._session.delete(f"{self._trading_base}{path}", timeout=10)
        if r.status_code >= 400:
            raise BrokerError(f"Alpaca DELETE {path} → {r.status_code}: {r.text[:200]}")

    def get_account(self) -> Account:
        j = self._get(self._trading_base, "/v2/account")
        return Account(
            account_id=j["account_number"],
            cash=Decimal(j["cash"]),
            equity=Decimal(j["equity"]),
        )

    def get_positions(self) -> list[Position]:
        j = self._get(self._trading_base, "/v2/positions")
        return [
            Position(
                symbol=p["symbol"],
                qty=int(float(p["qty"])),
                avg_price=Decimal(p["avg_entry_price"]),
            )
            for p in j
        ]

    def get_quote(self, symbol: str) -> Quote:
        j = self._get(self._data_base, f"/v2/stocks/{symbol}/quotes/latest")
        q = j["quote"]
        return Quote(
            symbol=symbol,
            bid=Decimal(str(q["bp"])),
            ask=Decimal(str(q["ap"])),
            last=Decimal(str(q.get("ap", q["bp"]))),
        )

    def place_market_order(self, symbol: str, qty: int, side: str) -> str:
        if qty <= 0:
            raise BrokerError(f"qty must be positive, got {qty}")
        side_l = side.lower()
        if side_l not in ("buy", "sell"):
            raise BrokerError(f"side must be BUY or SELL, got {side}")
        j = self._post("/v2/orders", {
            "symbol":        symbol,
            "qty":           str(qty),
            "side":          side_l,
            "type":          "market",
            "time_in_force": "day",
        })
        return j["id"]

    def place_limit_order(self, symbol: str, qty: int, side: str, limit_price: Decimal,
                          duration: str = "DAY") -> str:
        if qty <= 0:
            raise BrokerError(f"qty must be positive, got {qty}")
        j = self._post("/v2/orders", {
            "symbol":        symbol,
            "qty":           str(qty),
            "side":          side.lower(),
            "type":          "limit",
            "limit_price":   str(limit_price),
            "time_in_force": duration.lower(),
        })
        return j["id"]

    def cancel_order(self, order_id: str) -> None:
        self._delete(f"/v2/orders/{order_id}")

    def list_open_orders(self) -> list[dict]:
        return self._get(self._trading_base, "/v2/orders", status="open")


# Quick assertion that we satisfy the Protocol at module-load time.
_: BrokerAdapter = AlpacaAdapter(AlpacaCredentials("x", "y"))  # type: ignore[assignment]
del _
