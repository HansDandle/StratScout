"""Trade order recorder + dry-run / paper / live execution.

Three execution modes share one target-computation path:

  - ``dry``   — compute targets, record ``TARGET`` rows. No broker call.
  - ``paper`` — compute targets, diff against the broker's current paper
                positions, place market orders, record ``BUY``/``SELL`` rows
                with status transitioning ``submitted → filled`` (or
                ``rejected``). Alpaca paper account by default.
  - ``live``  — same as paper but routes to the real-money adapter (Schwab).

For now ETF (rotator) strategies are the only kind plumbed through paper /
live — the smallcap engine's daily-signal model needs a separate execution
path. Smallcap can still be dry-run for inspection.

Why a separate module: keeps the API ``app.py`` thin, and the engine layer is
the only place that imports strategies.py + the backtest engine + brokers
together.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from stratscout.engine.settings import db_path
from stratscout.engine.strategies import init_db, get_strategy

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _conn() -> sqlite3.Connection:
    p = db_path("stratscout")
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


# ── Order record ──────────────────────────────────────────────────────────────

@dataclass
class TradeOrder:
    id: int
    strategy_id: int
    ran_at: str
    mode: str
    action: str
    symbol: str
    qty: int | None
    status: str
    message: str
    broker_order_id: str | None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "TradeOrder":
        return cls(
            id=r["id"],
            strategy_id=r["strategy_id"],
            ran_at=r["ran_at"],
            mode=r["mode"],
            action=r["action"],
            symbol=r["symbol"],
            qty=r["qty"],
            status=r["status"],
            message=r["message"],
            broker_order_id=r["broker_order_id"],
        )


def record_order(
    *,
    strategy_id: int,
    mode: str,
    action: str,
    symbol: str,
    qty: int | None,
    status: str,
    message: str = "",
    broker_order_id: str | None = None,
) -> TradeOrder:
    init_db()
    con = _conn()
    cur = con.execute(
        "INSERT INTO trade_orders "
        "(strategy_id, ran_at, mode, action, symbol, qty, status, message, broker_order_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (strategy_id, _now_iso(), mode, action, symbol, qty, status, message, broker_order_id),
    )
    new_id = cur.lastrowid
    con.commit()
    row = con.execute("SELECT * FROM trade_orders WHERE id = ?", (new_id,)).fetchone()
    con.close()
    return TradeOrder.from_row(row)


def list_orders(strategy_id: int, limit: int = 100) -> list[TradeOrder]:
    init_db()
    con = _conn()
    rows = con.execute(
        "SELECT * FROM trade_orders WHERE strategy_id = ? ORDER BY ran_at DESC LIMIT ?",
        (strategy_id, limit),
    ).fetchall()
    con.close()
    return [TradeOrder.from_row(r) for r in rows]


def delete_orders(strategy_id: int) -> int:
    init_db()
    con = _conn()
    cur = con.execute("DELETE FROM trade_orders WHERE strategy_id = ?", (strategy_id,))
    n = cur.rowcount
    con.commit()
    con.close()
    return n


# ── Dry-run target computation ────────────────────────────────────────────────

@dataclass
class DryRunResult:
    strategy_id: int
    ran_at: str
    targets: list[str]
    regime: str
    as_of: str
    note: str
    order_ids: list[int]


def run_strategy_dry(strategy_id: int, note: str = "") -> DryRunResult:
    """Compute the strategy's target tickers as of the latest available bar,
    record one ``TARGET`` order row per target, and return a summary.

    No broker calls. No real orders. Just "if you ran this right now, here's
    what the engine would want to hold."
    """
    s = get_strategy(strategy_id)
    if s is None:
        raise ValueError(f"strategy {strategy_id} not found")

    if s.kind != "etf":
        raise NotImplementedError(
            f"Dry-run not implemented for strategy_kind={s.kind!r}. "
            "ETF rotator is the only kind plumbed through the trader for now."
        )

    targets, regime, as_of = _compute_etf_targets(s.params)
    ran_at = _now_iso()
    msg = f"dry-run · regime={regime} · as_of={as_of}"
    if note:
        msg += f" · {note}"

    ids: list[int] = []
    if not targets:
        rec = record_order(
            strategy_id=strategy_id, mode="dry", action="HOLD", symbol="CASH",
            qty=None, status="recorded", message=msg,
        )
        ids.append(rec.id)
    else:
        for sym in targets:
            rec = record_order(
                strategy_id=strategy_id, mode="dry", action="TARGET", symbol=sym,
                qty=None, status="recorded", message=msg,
            )
            ids.append(rec.id)

    return DryRunResult(
        strategy_id=strategy_id, ran_at=ran_at,
        targets=targets, regime=regime, as_of=as_of,
        note=note, order_ids=ids,
    )


def _compute_etf_targets(params: dict) -> tuple[list[str], str, str]:
    """Run the ETF target selector on the latest local bar.

    Returns ``(targets, regime_label, as_of_date_iso)``. Raises ``RuntimeError``
    if no AGG bars are cached (the user needs to download data first).
    """
    import pandas as pd
    from stratscout.engine.backtest.etf import load_local_histories, choose_targets

    anchors = ["AGG", "BIL", "TLT"]
    pool_keys = ("risk_on_pool", "risk_off_rising_pool", "risk_off_falling_pool")
    pool_syms: list[str] = []
    for k in pool_keys:
        v = params.get(k, [])
        if isinstance(v, list):
            pool_syms.extend(str(x).upper() for x in v)
    if params.get("rising_rate_include_uup", False):
        pool_syms.append("UUP")
    universe = sorted(set(anchors) | set(pool_syms))

    end = datetime.now(timezone.utc).date().isoformat()
    start_dt = pd.Timestamp(end) - pd.Timedelta(days=600)
    start = start_dt.date().isoformat()

    histories = load_local_histories(universe, start, end)
    if not histories or "AGG" not in histories or histories["AGG"].empty:
        raise RuntimeError("No AGG bars in local cache — run the data downloader.")
    as_of = histories["AGG"].index[-1]
    targets = choose_targets(histories, as_of, params)
    risk_on_pool = {str(x).upper() for x in params.get("risk_on_pool", [])}
    regime = "risk-on" if any(t in risk_on_pool for t in targets) else "risk-off"
    return targets, regime, str(as_of.date())


# ── Paper / live execution ───────────────────────────────────────────────────

@dataclass
class ExecutionResult:
    strategy_id: int
    mode: str                         # 'paper' | 'live'
    ran_at: str
    targets: list[str]
    regime: str
    as_of: str
    note: str
    order_ids: list[int]
    placed: int                       # successful broker submissions
    failed: int                       # broker rejections / errors
    fell_back_to_dry: bool = False    # broker missing / no creds


def _adapter_for_mode(mode: str):
    """Returns a connected BrokerAdapter for the given mode, or raises with a
    user-friendly message if credentials are missing."""
    from stratscout.engine import credentials as creds
    if mode == "paper":
        api_key = creds.get("alpaca", "api_key")
        api_secret = creds.get("alpaca", "api_secret")
        if not api_key or not api_secret:
            raise RuntimeError(
                "Alpaca API key + secret are required for paper trading. "
                "Open Settings to connect them.",
            )
        from stratscout.engine.brokers.alpaca import AlpacaAdapter, AlpacaCredentials
        adapter = AlpacaAdapter(AlpacaCredentials(api_key, api_secret, paper=True))
        adapter.connect()
        return adapter
    if mode == "live":
        # Live picks the same Alpaca account in live (non-paper) mode if the
        # user has opted out of paper; Schwab support lives in the legacy
        # CLI for now and will get wired here once OAuth refresh runs server-
        # side.
        api_key = creds.get("alpaca", "api_key")
        api_secret = creds.get("alpaca", "api_secret")
        if not api_key or not api_secret:
            raise RuntimeError(
                "Live execution currently requires Alpaca live keys (Schwab is "
                "still on the CLI auth flow). Connect them in Settings.",
            )
        from stratscout.engine.brokers.alpaca import AlpacaAdapter, AlpacaCredentials
        adapter = AlpacaAdapter(AlpacaCredentials(api_key, api_secret, paper=False))
        adapter.connect()
        return adapter
    raise ValueError(f"Unknown execution mode: {mode}")


def run_strategy(strategy_id: int, mode: str, note: str = "") -> ExecutionResult | DryRunResult:
    """Unified entry point. mode ∈ {'dry','paper','live'}.

    - dry  → ``run_strategy_dry``: target rows only, no broker.
    - paper → diff against the broker's paper positions, place market orders.
    - live  → same but the live account.

    Returns a ``DryRunResult`` for dry mode, ``ExecutionResult`` otherwise.
    """
    if mode == "dry":
        return run_strategy_dry(strategy_id, note=note)
    if mode not in ("paper", "live"):
        raise ValueError(f"mode must be dry/paper/live, got {mode!r}")

    s = get_strategy(strategy_id)
    if s is None:
        raise ValueError(f"strategy {strategy_id} not found")
    if s.kind != "etf":
        raise NotImplementedError(
            f"{mode} execution not implemented for strategy_kind={s.kind!r}. "
            "Smallcap uses a daily-signal model that needs a separate path.",
        )

    targets, regime, as_of = _compute_etf_targets(s.params)
    ran_at = _now_iso()
    base_msg = f"{mode} · regime={regime} · as_of={as_of}"
    if note:
        base_msg += f" · {note}"

    # Open broker; fall back to recording targets as 'dry' if credentials are
    # missing — caller flags this so the UI can prompt the user to fix it.
    try:
        adapter = _adapter_for_mode(mode)
    except RuntimeError as e:
        # Soft-fail — surface the issue but don't lose the target snapshot.
        msg = f"{base_msg} · skipped: {e}"
        order_ids = []
        for sym in targets or ["CASH"]:
            rec = record_order(
                strategy_id=strategy_id, mode=mode,
                action="TARGET" if sym != "CASH" else "HOLD",
                symbol=sym, qty=None, status="rejected", message=msg,
            )
            order_ids.append(rec.id)
        return ExecutionResult(
            strategy_id=strategy_id, mode=mode, ran_at=ran_at,
            targets=targets, regime=regime, as_of=as_of, note=note,
            order_ids=order_ids, placed=0, failed=len(order_ids),
            fell_back_to_dry=True,
        )

    # Read current positions + cash. Compute diff.
    account = adapter.get_account()
    positions = adapter.get_positions()
    current_qty: dict[str, int] = {p.symbol: p.qty for p in positions if p.qty > 0}
    target_set = set(targets)
    to_sell = [sym for sym, qty in current_qty.items() if sym not in target_set and qty > 0]
    to_buy = [sym for sym in targets if current_qty.get(sym, 0) == 0]

    placed = 0
    failed = 0
    order_ids: list[int] = []

    # SELL first so the freed cash funds the new BUYs.
    # Track estimated proceeds from successful sells so buy-sizing doesn't rely
    # on Schwab's stale availableFundsNonMarginableTrade (which won't reflect
    # pending market orders yet).
    estimated_sell_proceeds = Decimal("0")
    for sym in to_sell:
        qty = current_qty[sym]
        oid, status, info = _place_order(adapter, sym, qty, "SELL")
        rec = record_order(
            strategy_id=strategy_id, mode=mode, action="SELL", symbol=sym,
            qty=qty, status=status, message=f"{base_msg} · {info}",
            broker_order_id=oid,
        )
        order_ids.append(rec.id)
        if status == "rejected":
            failed += 1
        else:
            placed += 1
            # Estimate proceeds using last known price so buy-sizing is accurate
            # even before Schwab's balance API reflects the pending sell.
            try:
                q = adapter.get_quote(sym)
                price = q.bid if q.bid > 0 else q.last
                estimated_sell_proceeds += price * qty
            except Exception:
                pass

    # BUY equal-weight from available cash / remaining target slots.
    # Use cash (not equity) so we never spend money that's already in positions
    # or borrow on margin.  Add estimated sell proceeds since Schwab's balance
    # API lags pending market orders by several seconds.
    if to_buy:
        spendable = Decimal(account.cash) + estimated_sell_proceeds
        slot = (spendable / Decimal(len(to_buy))) if to_buy else Decimal("0")
        for sym in to_buy:
            try:
                quote = adapter.get_quote(sym)
                price = quote.ask if quote.ask > 0 else quote.last
                qty = int(slot // price) if price > 0 else 0
            except Exception as e:
                rec = record_order(
                    strategy_id=strategy_id, mode=mode, action="BUY", symbol=sym,
                    qty=None, status="rejected", message=f"{base_msg} · quote failed: {e}",
                )
                order_ids.append(rec.id)
                failed += 1
                continue
            if qty <= 0:
                rec = record_order(
                    strategy_id=strategy_id, mode=mode, action="BUY", symbol=sym,
                    qty=0, status="rejected",
                    message=f"{base_msg} · sized to 0 (slot={slot}, ask={price})",
                )
                order_ids.append(rec.id)
                failed += 1
                continue
            oid, status, info = _place_order(adapter, sym, qty, "BUY")
            rec = record_order(
                strategy_id=strategy_id, mode=mode, action="BUY", symbol=sym,
                qty=qty, status=status, message=f"{base_msg} · {info}",
                broker_order_id=oid,
            )
            order_ids.append(rec.id)
            if status == "rejected":
                failed += 1
            else:
                placed += 1

    # If everything was already in position, log a HOLD row so the audit trail
    # shows the run happened.
    if not to_sell and not to_buy:
        rec = record_order(
            strategy_id=strategy_id, mode=mode, action="HOLD", symbol="ALL",
            qty=None, status="recorded",
            message=f"{base_msg} · already aligned with target ({len(targets)} positions)",
        )
        order_ids.append(rec.id)

    return ExecutionResult(
        strategy_id=strategy_id, mode=mode, ran_at=ran_at,
        targets=targets, regime=regime, as_of=as_of, note=note,
        order_ids=order_ids, placed=placed, failed=failed,
        fell_back_to_dry=False,
    )


def _place_order(adapter, symbol: str, qty: int, side: str) -> tuple[str | None, str, str]:
    """Try to place a market order. Returns (broker_id|None, status, info_string)."""
    try:
        oid = adapter.place_market_order(symbol, qty, side)
        return oid, "submitted", f"{side} {qty} @ market"
    except Exception as e:
        log.warning("broker rejected %s %d %s: %s", side, qty, symbol, e)
        return None, "rejected", f"{side} {qty}: {e}"


# Silence "unused import" warnings — json is reserved for future use.
_ = json
