"""
GP backtest adapter.

Runs the standard rebalance loop but uses eval_strategy() for target selection
instead of the hardcoded choose_targets() in etf.py.

Reuses: rebalance_positions, value_of_portfolio, compute_performance from core.py.
The caller provides a pre-loaded `histories` dict (from the worker global).
"""
from __future__ import annotations

from decimal import Decimal

import pandas as pd

from stratscout.engine.backtest.core import (
    value_of_portfolio,
    rebalance_positions,
    compute_performance,
    BacktestError,
)
from stratscout.engine.data.universes import ANCHORS
from stratscout.engine.fuzzers.strategy_dsl import eval_strategy


def _symbols_in_strategy(strategy: dict) -> set[str]:
    syms: set[str] = set(ANCHORS)
    for rule in strategy.get("rules", []):
        for key in ("lhs", "rhs"):
            ind = rule.get("condition", {}).get(key, {})
            if "symbol" in ind:
                syms.add(ind["symbol"])
        for sym in rule.get("action", {}).get("pool", []):
            syms.add(sym)
    for sym in strategy.get("default_action", {}).get("pool", []):
        syms.add(sym)
    return syms


def run_gp_backtest(
    strategy: dict,
    histories: dict,
    start: str,
    end: str,
    cash: float = 10_000.0,
    min_hold_days: int = 5,
) -> dict:
    """
    Run a single backtest for a GP strategy tree.

    `histories` must be a pre-loaded dict {symbol: DataFrame} that already
    spans well before `start` (for indicator warmup).

    Returns {"perf": dict, "n_trades": int, "nav_values": list[float]}.
    """
    needed = _symbols_in_strategy(strategy)
    # Work only with symbols we actually have data for
    symbols = sorted(s for s in needed if s in histories)

    anchor_indexes = [set(histories[a].index) for a in ANCHORS if a in histories]
    if not anchor_indexes:
        raise BacktestError("Anchor symbols not found in histories")
    all_common = sorted(set.intersection(*anchor_indexes))

    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts   = pd.Timestamp(end,   tz="UTC")

    live_from = next((i for i, d in enumerate(all_common) if d >= start_ts), None)
    if live_from is None or live_from == 0:
        return {
            "perf": {"total_return_pct": 0.0, "cagr_pct": 0.0, "max_drawdown_pct": 0.0},
            "n_trades": 0, "nav_values": [],
        }

    positions: dict[str, int] = {sym: 0 for sym in symbols}
    portfolio_cash = Decimal(str(cash))
    nav_dates: list = []
    nav_vals: list[float] = []
    n_trades = 0
    last_rebalance_idx = live_from - min_hold_days

    for idx in range(live_from, len(all_common)):
        today = all_common[idx]
        if today > end_ts:
            break
        yesterday = all_common[idx - 1]

        try:
            targets = eval_strategy(strategy, histories, yesterday)
            if targets:
                current_held = {sym for sym, qty in positions.items() if qty > 0}
                held_long_enough = (idx - last_rebalance_idx) >= min_hold_days
                if set(targets) != current_held and held_long_enough:
                    positions, portfolio_cash, trades = rebalance_positions(
                        histories, positions, portfolio_cash, targets, today
                    )
                    n_trades += len(trades)
                    last_rebalance_idx = idx
        except Exception:
            pass

        port_value = float(value_of_portfolio(histories, positions, portfolio_cash, today))
        nav_dates.append(today)
        nav_vals.append(port_value)

    if not nav_vals:
        return {
            "perf": {"total_return_pct": 0.0, "cagr_pct": 0.0, "max_drawdown_pct": 0.0},
            "n_trades": 0, "nav_values": [],
        }

    nav_series = pd.Series(nav_vals, index=pd.DatetimeIndex(nav_dates))
    perf = compute_performance(nav_series)
    return {"perf": perf, "n_trades": n_trades, "nav_values": nav_vals}
