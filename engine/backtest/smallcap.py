"""
Small-cap volume anomaly backtester.

Strategy:
  - Each day, scan the universe for stocks where today's volume >= VOL_SURGE_MULT
    times the trailing VOL_LOOKBACK-day average volume.
  - Buy at next open, hold for HOLD_DAYS trading days, sell at open.
  - Max MAX_POSITIONS concurrent positions, equal weight.
  - No shorting, no leverage.

Usage:
    python smallcap_backtest.py
    python smallcap_backtest.py --mult 5 --hold 5 --lookback 20
    python smallcap_backtest.py --download   # re-download price data first
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

from stratscout.engine.data.universes import smallcap_universe as _smallcap_universe
from stratscout.engine.settings import smallcap_dir as _smallcap_dir

SMALLCAP_UNIVERSE = _smallcap_universe()
DATA_DIR = _smallcap_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── Data ─────────────────────────────────────────────────────────────────────

def download_data(symbols: list[str], start: str = "2024-10-01", end: str = "2026-05-03"):
    """Download daily OHLCV for all symbols, cache as feather files."""
    print(f"Downloading {len(symbols)} symbols ({start} to {end})...")
    failed = []
    for i, sym in enumerate(symbols):
        path = DATA_DIR / f"{sym}.feather"
        if path.exists():
            continue
        try:
            df = yf.download(sym, start=start, end=end, progress=False, auto_adjust=True)
            if df.empty or len(df) < 30:
                failed.append(sym)
                continue
            df = df.reset_index()
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
            df = df.rename(columns={"Date": "date", "Open": "open", "High": "high",
                                    "Low": "low", "Close": "close", "Volume": "volume"})
            df["date"] = pd.to_datetime(df["date"])
            df[["date","open","high","low","close","volume"]].to_feather(path)
        except Exception as e:
            failed.append(sym)
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(symbols)} done, {len(failed)} failed so far")
    print(f"Done. {len(failed)} symbols failed: {failed[:10]}{'...' if len(failed)>10 else ''}")


def load_data(symbols: list[str]) -> dict[str, pd.DataFrame]:
    data = {}
    for sym in symbols:
        path = DATA_DIR / f"{sym}.feather"
        if path.exists():
            df = pd.read_feather(path)
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            if len(df) >= 30:
                data[sym] = df
    return data


# ── Signal ───────────────────────────────────────────────────────────────────

def find_signals(data: dict[str, pd.DataFrame], vol_lookback: int, vol_mult: float,
                 start: str, end: str, require_green: bool = False) -> pd.DataFrame:
    """Return a DataFrame of (date, symbol) pairs where volume anomaly fired.

    require_green: if True, only fire when close >= open on the signal day.
    """
    signals = []
    start_ts = pd.Timestamp(start)
    end_ts   = pd.Timestamp(end)

    for sym, df in data.items():
        if len(df) < vol_lookback + 5:
            continue
        avg_vol = df["volume"].rolling(vol_lookback).mean().shift(1)
        surge   = df["volume"] / avg_vol
        mask    = (surge >= vol_mult) & (df.index >= start_ts) & (df.index <= end_ts)
        if require_green:
            mask &= df["close"] >= df["open"]
        fired = df[mask]
        for date in fired.index:
            signals.append({"date": date, "symbol": sym, "surge": surge[date]})

    return pd.DataFrame(signals).sort_values("date").reset_index(drop=True)


# ── Backtest ──────────────────────────────────────────────────────────────────

def run_backtest(data: dict[str, pd.DataFrame], signals: pd.DataFrame,
                 hold_days: int, max_positions: int,
                 start_cash: float = 100_000) -> dict:
    """
    Simulate buying at next open after signal, selling hold_days later at open.
    Returns perf dict and trade log DataFrame.
    """
    # Build sorted list of all trading dates across universe
    all_dates = sorted(set(
        d for df in data.values() for d in df.index
    ))
    date_idx = {d: i for i, d in enumerate(all_dates)}

    # Group signals by date
    sig_by_date: dict = signals.groupby("date")["symbol"].apply(list).to_dict()

    cash = start_cash
    # positions: {symbol: (qty, sell_date_idx, buy_price)}
    positions: dict[str, tuple] = {}
    nav_series: list[dict] = []
    trades: list[dict] = []

    for today in all_dates:
        idx = date_idx[today]

        # Close positions due today
        to_close = [sym for sym, (qty, sell_idx, _) in positions.items() if sell_idx <= idx]
        for sym in to_close:
            qty, sell_idx, buy_price = positions.pop(sym)
            df = data[sym]
            # sell at today's open if available, else close
            if today in df.index:
                sell_price = float(df.loc[today, "open"])
                proceeds = qty * sell_price
                cash += proceeds
                trades.append({
                    "date": today, "symbol": sym, "action": "sell",
                    "qty": qty, "price": sell_price,
                    "buy_price": buy_price,
                    "pnl_pct": (sell_price - buy_price) / buy_price * 100,
                })

        # Open new positions from today's signals (buy at tomorrow's open)
        if today in sig_by_date:
            candidates = sig_by_date[today]
            # skip anything already held
            candidates = [s for s in candidates if s not in positions]
            # limit by available slots
            slots = max_positions - len(positions)
            candidates = candidates[:slots]

            for sym in candidates:
                # find next trading day
                next_idx = idx + 1
                if next_idx >= len(all_dates):
                    continue
                next_day = all_dates[next_idx]
                df = data[sym]
                if next_day not in df.index:
                    continue
                buy_price = float(df.loc[next_day, "open"])
                if buy_price <= 0:
                    continue
                # equal-weight across max_positions
                alloc = cash / max(1, slots)
                alloc = min(alloc, cash)
                qty = int(alloc // buy_price)
                if qty <= 0:
                    continue
                cost = qty * buy_price
                cash -= cost
                sell_idx = next_idx + hold_days
                positions[sym] = (qty, sell_idx, buy_price)
                trades.append({
                    "date": next_day, "symbol": sym, "action": "buy",
                    "qty": qty, "price": buy_price,
                    "buy_price": buy_price, "pnl_pct": None,
                })

        # NAV = cash + mark-to-market of open positions
        port_value = cash
        for sym, (qty, _, _) in positions.items():
            df = data[sym]
            price = float(df.loc[today, "close"]) if today in df.index else 0
            port_value += qty * price
        nav_series.append({"date": today, "nav": port_value})

    nav_df = pd.DataFrame(nav_series).set_index("date")
    trade_df = pd.DataFrame(trades)

    # Performance
    nav = nav_df["nav"]
    total_return = (nav.iloc[-1] / nav.iloc[0] - 1) * 100
    days = (nav.index[-1] - nav.index[0]).days
    cagr = ((nav.iloc[-1] / nav.iloc[0]) ** (365 / max(days, 1)) - 1) * 100
    roll_max = nav.cummax()
    drawdown = ((nav - roll_max) / roll_max * 100)
    max_dd = float(drawdown.min())

    sells = trade_df[trade_df["action"] == "sell"] if not trade_df.empty else pd.DataFrame()
    win_rate = float((sells["pnl_pct"] > 0).mean() * 100) if not sells.empty else 0
    avg_win  = float(sells[sells["pnl_pct"] > 0]["pnl_pct"].mean()) if not sells.empty else 0
    avg_loss = float(sells[sells["pnl_pct"] <= 0]["pnl_pct"].mean()) if not sells.empty else 0

    return {
        "nav_df": nav_df,
        "trade_df": trade_df,
        "total_return_pct": total_return,
        "cagr_pct": cagr,
        "max_drawdown_pct": max_dd,
        "n_trades": len(sells),
        "win_rate_pct": win_rate,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download",  action="store_true", help="Re-download price data")
    parser.add_argument("--mult",      type=float, default=5.0,  help="Volume surge multiplier")
    parser.add_argument("--lookback",  type=int,   default=20,   help="Avg volume lookback days")
    parser.add_argument("--hold",      type=int,   default=5,    help="Hold period in trading days")
    parser.add_argument("--positions", type=int,   default=10,   help="Max concurrent positions")
    parser.add_argument("--cash",      type=float, default=100_000)
    parser.add_argument("--start",     default="2025-01-01")
    parser.add_argument("--end",       default="2026-05-03")
    args = parser.parse_args()

    if args.download or not any(DATA_DIR.glob("*.feather")):
        download_data(SMALLCAP_UNIVERSE, start="2024-10-01", end=args.end)

    print(f"\nLoading data...")
    data = load_data(SMALLCAP_UNIVERSE)
    print(f"Loaded {len(data)} symbols with sufficient history")

    print(f"\nScanning for volume anomalies (>{args.mult}x avg over {args.lookback}d)...")
    signals = find_signals(data, args.lookback, args.mult, args.start, args.end)
    print(f"Found {len(signals)} signals across {signals['symbol'].nunique()} symbols")

    if signals.empty:
        print("No signals found — try a lower --mult value")
        return

    print(f"\nRunning backtest (hold={args.hold}d, max_positions={args.positions})...")
    result = run_backtest(data, signals, args.hold, args.positions, args.cash)

    print(f"\n{'='*50}")
    print(f"  Total return:   {result['total_return_pct']:+.1f}%")
    print(f"  CAGR:           {result['cagr_pct']:+.1f}%")
    print(f"  Max drawdown:   {result['max_drawdown_pct']:.1f}%")
    print(f"  Trades:         {result['n_trades']}")
    print(f"  Win rate:       {result['win_rate_pct']:.1f}%")
    print(f"  Avg win:        {result['avg_win_pct']:+.1f}%")
    print(f"  Avg loss:       {result['avg_loss_pct']:+.1f}%")
    print(f"{'='*50}\n")

    # Top winning and losing trades
    sells = result["trade_df"][result["trade_df"]["action"] == "sell"].copy()
    if not sells.empty:
        sells = sells.sort_values("pnl_pct", ascending=False)
        print("Top 10 wins:")
        print(sells[["date","symbol","pnl_pct"]].head(10).to_string(index=False))
        print("\nTop 10 losses:")
        print(sells[["date","symbol","pnl_pct"]].tail(10).to_string(index=False))

        # Most frequently triggered symbols
        print("\nMost triggered symbols:")
        print(signals["symbol"].value_counts().head(15).to_string())


if __name__ == "__main__":
    main()
