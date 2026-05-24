"""Schwab broker adapter.

Wraps the existing live_trader.py order-placement logic behind the BrokerAdapter
interface. OAuth refresh is handled by SchwabCredentials.refresh_token() —
that flow lives in stratscout.engine.brokers.oauth (Phase 3 web mode) or is
delegated to the legacy schwab_auth.py during desktop mode.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import requests

from stratscout.engine.brokers.base import (
    Account, BrokerAdapter, BrokerError, Position, Quote,
)

log = logging.getLogger(__name__)
BASE_URL = "https://api.schwabapi.com"


@dataclass
class SchwabCredentials:
    app_key: str
    app_secret: str
    refresh_token: str
    account_number: str  # The account this adapter operates against
    paper: bool = False  # Schwab doesn't offer paper accounts — always False
    _access_token: str | None = None
    _access_token_expires_at: datetime | None = None
    _account_hash: str | None = None

    def access_token(self) -> str:
        """Return a fresh access token, refreshing if expired."""
        if (self._access_token and self._access_token_expires_at
                and self._access_token_expires_at > datetime.now(timezone.utc) + timedelta(seconds=30)):
            return self._access_token
        return self._refresh()

    def _refresh(self) -> str:
        creds_b64 = base64.b64encode(f"{self.app_key}:{self.app_secret}".encode()).decode()
        r = requests.post(
            f"{BASE_URL}/v1/oauth/token",
            headers={
                "Authorization": f"Basic {creds_b64}",
                "Content-Type":  "application/x-www-form-urlencoded",
            },
            data={"grant_type": "refresh_token", "refresh_token": self.refresh_token},
            timeout=10,
        )
        if r.status_code != 200:
            raise BrokerError(f"Schwab token refresh failed: {r.status_code} {r.text[:200]}")
        data = r.json()
        self._access_token = data["access_token"]
        self._access_token_expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=int(data.get("expires_in", 1800)))
        )
        # Some refresh responses return a new refresh_token too — store it.
        if "refresh_token" in data:
            self.refresh_token = data["refresh_token"]
        return self._access_token


class SchwabAdapter:
    name = "schwab"

    def __init__(self, creds: SchwabCredentials):
        self._creds = creds
        self.paper = False

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._creds.access_token()}",
            "Accept":        "application/json",
        }

    def _resolve_account_hash(self) -> str:
        if self._creds._account_hash:
            return self._creds._account_hash
        r = requests.get(f"{BASE_URL}/trader/v1/accounts/accountNumbers",
                         headers=self._headers(), timeout=10)
        if r.status_code != 200:
            raise BrokerError(f"Schwab accountNumbers → {r.status_code}: {r.text[:200]}")
        for a in r.json():
            if a["accountNumber"] == self._creds.account_number:
                self._creds._account_hash = a["hashValue"]
                return a["hashValue"]
        raise BrokerError(
            f"Account {self._creds.account_number} not found. "
            f"Available: {[a['accountNumber'] for a in r.json()]}"
        )

    def connect(self) -> None:
        self._creds.access_token()           # forces a refresh if needed
        self._resolve_account_hash()

    def get_account(self) -> Account:
        h = self._resolve_account_hash()
        r = requests.get(f"{BASE_URL}/trader/v1/accounts/{h}", headers=self._headers(), timeout=10)
        if r.status_code != 200:
            raise BrokerError(f"Schwab account → {r.status_code}: {r.text[:200]}")
        sa = r.json()["securitiesAccount"]
        bal = sa["currentBalances"]
        return Account(
            account_id=self._creds.account_number,
            cash=Decimal(str(bal["availableFundsNonMarginableTrade"])),
            equity=Decimal(str(bal.get("liquidationValue", bal["availableFundsNonMarginableTrade"]))),
        )

    def get_positions(self) -> list[Position]:
        h = self._resolve_account_hash()
        r = requests.get(f"{BASE_URL}/trader/v1/accounts/{h}",
                         headers=self._headers(), params={"fields": "positions"}, timeout=10)
        if r.status_code != 200:
            raise BrokerError(f"Schwab positions → {r.status_code}: {r.text[:200]}")
        out: list[Position] = []
        equity_types = {"EQUITY", "ETF"}
        for p in r.json()["securitiesAccount"].get("positions", []):
            inst = p["instrument"]
            if inst.get("assetType") not in equity_types:
                continue
            qty = int(float(p["longQuantity"]))
            if qty > 0:
                out.append(Position(
                    symbol=inst["symbol"],
                    qty=qty,
                    avg_price=Decimal(str(p.get("averagePrice", "0"))),
                ))
        return out

    def get_quote(self, symbol: str) -> Quote:
        r = requests.get(
            f"{BASE_URL}/marketdata/v1/quotes",
            headers=self._headers(),
            params={"symbols": symbol, "fields": "quote"},
            timeout=10,
        )
        if r.status_code != 200:
            raise BrokerError(f"Schwab quote → {r.status_code}: {r.text[:200]}")
        data = r.json().get(symbol, {}).get("quote", {})
        bid = Decimal(str(data.get("bidPrice", 0)))
        ask = Decimal(str(data.get("askPrice", 0)))
        last = Decimal(str(data.get("lastPrice", ask or bid)))
        return Quote(symbol=symbol, bid=bid, ask=ask, last=last)

    def _post_order(self, payload: dict, label: str) -> str:
        h = self._resolve_account_hash()
        r = requests.post(
            f"{BASE_URL}/trader/v1/accounts/{h}/orders",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
        if not (200 <= r.status_code < 300):
            raise BrokerError(f"Schwab order failed ({label}): {r.status_code} {r.text[:200]}")
        order_id = r.headers.get("Location", "").split("/")[-1]
        log.info("Schwab order placed: %s (%s)", order_id, label)
        return order_id

    def place_market_order(self, symbol: str, qty: int, side: str) -> str:
        if qty <= 0:
            raise BrokerError(f"qty must be positive, got {qty}")
        instruction = side.upper()
        if instruction not in ("BUY", "SELL"):
            raise BrokerError(f"side must be BUY or SELL, got {side}")
        payload = {
            "session": "NORMAL",
            "duration": "DAY",
            "orderType": "MARKET",
            "complexOrderStrategyType": "NONE",
            "quantity": qty,
            "orderLegCollection": [{
                "orderLegType": "EQUITY",
                "legId": 1,
                "instrument": {"assetType": "EQUITY", "symbol": symbol},
                "instruction": instruction,
                "quantity": qty,
            }],
            "orderStrategyType": "SINGLE",
        }
        return self._post_order(payload, f"MARKET {instruction} {qty} × {symbol}")

    def place_limit_order(self, symbol: str, qty: int, side: str, limit_price: Decimal,
                          duration: str = "DAY") -> str:
        if qty <= 0:
            raise BrokerError(f"qty must be positive, got {qty}")
        payload = {
            "session": "NORMAL",
            "duration": duration.upper(),
            "orderType": "LIMIT",
            "price": str(limit_price),
            "complexOrderStrategyType": "NONE",
            "quantity": qty,
            "orderLegCollection": [{
                "orderLegType": "EQUITY",
                "legId": 1,
                "instrument": {"assetType": "EQUITY", "symbol": symbol},
                "instruction": side.upper(),
                "quantity": qty,
            }],
            "orderStrategyType": "SINGLE",
        }
        return self._post_order(payload, f"LIMIT {side.upper()} {qty} × {symbol} @ ${limit_price}")

    def cancel_order(self, order_id: str) -> None:
        h = self._resolve_account_hash()
        r = requests.delete(
            f"{BASE_URL}/trader/v1/accounts/{h}/orders/{order_id}",
            headers=self._headers(),
            timeout=10,
        )
        if not (200 <= r.status_code < 300):
            raise BrokerError(f"Schwab cancel → {r.status_code}: {r.text[:200]}")

    def list_open_orders(self) -> list[dict]:
        h = self._resolve_account_hash()
        now  = datetime.now(timezone.utc)
        past = now - timedelta(days=1)
        r = requests.get(
            f"{BASE_URL}/trader/v1/accounts/{h}/orders",
            headers=self._headers(),
            params={
                "fromEnteredTime": past.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "toEnteredTime":   now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            },
            timeout=10,
        )
        if r.status_code != 200:
            raise BrokerError(f"Schwab orders → {r.status_code}: {r.text[:200]}")
        return [o for o in r.json() if o.get("status") in ("WORKING", "QUEUED", "PENDING_ACTIVATION")]


_: BrokerAdapter = SchwabAdapter(SchwabCredentials("x", "y", "z", "0"))  # type: ignore[assignment]
del _
