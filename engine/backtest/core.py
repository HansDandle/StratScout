from __future__ import annotations

import argparse
import os
from datetime import datetime
from decimal import Decimal
from typing import Any

import pandas as pd
import requests


SYMBOLS = [
    "AGG", "BIL", "SOXL", "TQQQ", "UPRO", "TECL",
    "TLT", "QID", "TBF", "UUP", "UGL", "TMF", "BTAL", "XLP",
]

RISK_ON_SYMBOLS = ["SOXL", "TQQQ", "UPRO", "TECL"]
RISK_OFF_RISING_SYMBOLS = ["QID", "TBF"]
RISK_OFF_FALLING_SYMBOLS = ["UGL", "TMF", "BTAL", "XLP"]

# Massive is the rebranded Polygon API. The legacy Polygon endpoint continues to work,
# but api.massive.com is the current primary host.
POLYGON_BASE_URL = "https://api.polygon.io/v2/aggs/ticker"
MASSIVE_BASE_URL = "https://api.massive.com/v2/aggs/ticker"


class BacktestError(Exception):
    pass


def polygon_ticker(symbol: str) -> str:
    # Polygon can accept ETF tickers either raw or with an exchange prefix.
    return symbol


def fetch_polygon_history(symbol: str, start: str, end: str, api_key: str) -> pd.DataFrame:
    url = f"{POLYGON_BASE_URL}/{polygon_ticker(symbol)}/range/1/day/{start}/{end}"
    params = {
        "adjusted": "true",
        "sort": "asc",
        "limit": 50000,
        "apiKey": api_key,
    }
    resp = requests.get(url, params=params, timeout=30)
    if resp.status_code == 404:
        raise BacktestError(f"Symbol not found in Polygon: {symbol}")
    resp.raise_for_status()
    data = resp.json()
    if "results" not in data:
        raise BacktestError(f"Polygon returned no results for {symbol}: {data}")
    df = pd.DataFrame(data["results"])
    if df.empty:
        raise BacktestError(f"No historical data for {symbol} in range {start} to {end}")
    df = df.rename(columns={"t": "date", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df["date"] = pd.to_datetime(df["date"], unit="ms", utc=True)
    df = df[["date", "open", "high", "low", "close", "volume"]]
    df = df.set_index("date").sort_index()
    return df


def fetch_massive_history(symbol: str, start: str, end: str, api_key: str) -> pd.DataFrame:
    url = f"{MASSIVE_BASE_URL}/{symbol}/range/1/day/{start}/{end}"
    params = {
        "adjusted": "true",
        "sort": "asc",
        "limit": 50000,
        "apiKey": api_key,
    }
    resp = requests.get(url, params=params, timeout=30)
    if resp.status_code == 404:
        raise BacktestError(f"Symbol not found in Massive: {symbol}")
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "OK" or "results" not in data:
        raise BacktestError(f"Massive returned no results for {symbol}: {data}")
    df = pd.DataFrame(data["results"])
    if df.empty:
        raise BacktestError(f"No historical data for {symbol} in range {start} to {end}")
    df = df.rename(columns={"t": "date", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df["date"] = pd.to_datetime(df["date"], unit="ms", utc=True)
    df = df[["date", "open", "high", "low", "close", "volume"]]
    df = df.set_index("date").sort_index()
    return df


def _normalize_yfinance_columns(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [" ".join([str(c) for c in col if c]).strip() for col in df.columns.values]

    suffix = f" {symbol}"
    renamed = {}
    for col in df.columns:
        if col.endswith(suffix):
            renamed[col] = col[: -len(suffix)]
    if renamed:
        df = df.rename(columns=renamed)

    if "Adj Close" in df.columns and "Close" not in df.columns:
        df["Close"] = df["Adj Close"]

    if "date" not in df.columns:
        df = df.reset_index()

    rename_map = {
        "Date": "date",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    }
    df = df.rename(columns=rename_map)
    return df


def fetch_yfinance_history(symbol: str, start: str, end: str) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise BacktestError(
            "yfinance is not installed. Install it with `pip install yfinance` "
            "to use the Yahoo Finance fallback."
        ) from exc

    df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=False)
    if df.empty:
        raise BacktestError(f"Yahoo Finance returned no data for {symbol}")

    df = _normalize_yfinance_columns(df, symbol)
    required = ["date", "open", "high", "low", "close", "volume"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise BacktestError(
            f"Yahoo Finance missing required columns for {symbol}: {missing}. "
            f"Available columns: {list(df.columns)}"
        )

    df["date"] = pd.to_datetime(df["date"], utc=True)
    df = df[["date", "open", "high", "low", "close", "volume"]]
    df = df.dropna(subset=["open", "high", "low", "close"])
    if df.empty:
        raise BacktestError(f"Yahoo Finance returned no usable data for {symbol}")
    return df.set_index("date").sort_index()


def load_histories(symbols: list[str], start: str, end: str, massive_key: str | None, polygon_key: str | None) -> dict[str, pd.DataFrame]:
    import time

    histories: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        df = None
        if massive_key:
            for attempt in range(3):
                try:
                    df = fetch_massive_history(symbol, start, end, massive_key)
                    break
                except Exception as exc:
                    if "429" in str(exc) and attempt < 2:
                        time.sleep(2 ** attempt)
                    else:
                        print(f"Massive failed for {symbol}: {exc}. Trying yfinance.")
                        break
        if df is None and polygon_key:
            try:
                df = fetch_polygon_history(symbol, start, end, polygon_key)
            except Exception as exc:
                print(f"Polygon failed for {symbol}: {exc}. Falling back to Yahoo Finance.")
        if df is None:
            try:
                df = fetch_yfinance_history(symbol, start, end)
            except Exception as exc:
                raise BacktestError(f"Yahoo Finance failed for {symbol}: {exc}") from exc
        histories[symbol] = df
        time.sleep(0.12)  # stay under 5 req/s rate limit
    return histories


def intersection_dates(histories: dict[str, pd.DataFrame]) -> list[datetime]:
    decision_indexes = [set(histories[sym].index) for sym in ("AGG", "BIL", "TLT")]
    common = sorted(set.intersection(*decision_indexes))
    return common


def value_at_or_before(series: pd.Series, as_of: datetime) -> float:
    if as_of in series.index:
        return float(series.at[as_of])
    prior = series.loc[:as_of]
    if prior.empty:
        raise BacktestError(f"No historical price available for {series.name} on or before {as_of}")
    return float(prior.iloc[-1])


def rolling_rsi(close: pd.Series, window: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(window=window, min_periods=window).mean()
    avg_loss = loss.rolling(window=window, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(method="ffill").fillna(50)


def cumulative_return(close: pd.Series, lookback: int, as_of: datetime) -> float:
    idx = close.index.get_loc(as_of)
    if idx - lookback < 0:
        raise BacktestError(f"Not enough history to compute {lookback}-day return for {as_of.date()}")
    start_price = close.iloc[idx - lookback]
    end_price = close.iloc[idx]
    return float((end_price / start_price) - 1)


def choose_target_symbols(histories: dict[str, pd.DataFrame], as_of: datetime) -> list[str]:
    if as_of not in histories["AGG"].index:
        raise BacktestError(f"No AGG data for {as_of}")

    agg_close = histories["AGG"]["close"]
    bil_close = histories["BIL"]["close"]
    tlt_close = histories["TLT"]["close"]

    agg_bil_return = cumulative_return(agg_close, 60, as_of)
    bil_60 = cumulative_return(bil_close, 60, as_of)

    if agg_bil_return > bil_60:
        rsi_inputs = {}
        for sym in RISK_ON_SYMBOLS:
            rsi_inputs[sym] = value_at_or_before(rolling_rsi(histories[sym]["close"], 10), as_of)
        sorted_by_rsi = sorted(rsi_inputs.items(), key=lambda item: item[1])
        return [sorted_by_rsi[0][0], sorted_by_rsi[1][0]]

    tlt_bil_return = cumulative_return(tlt_close, 20, as_of)
    bil_20 = cumulative_return(bil_close, 20, as_of)

    if tlt_bil_return < bil_20:
        rsi_inputs = {}
        for sym in RISK_OFF_RISING_SYMBOLS:
            rsi_inputs[sym] = value_at_or_before(rolling_rsi(histories[sym]["close"], 20), as_of)
        bottom_symbol = min(rsi_inputs, key=rsi_inputs.get)
        return ["UUP", bottom_symbol]

    return RISK_OFF_FALLING_SYMBOLS.copy()


def get_price(histories: dict[str, pd.DataFrame], symbol: str, as_of: datetime, field: str = "close") -> float:
    series = histories[symbol][field]
    return value_at_or_before(series, as_of)


def value_of_portfolio(histories: dict[str, pd.DataFrame], positions: dict[str, int], cash: Decimal, as_of: datetime) -> Decimal:
    value = cash
    for symbol, qty in positions.items():
        if qty == 0:
            continue
        price = Decimal(str(get_price(histories, symbol, as_of, field="close")))
        value += price * qty
    return value


def rebalance_positions(
    histories: dict[str, pd.DataFrame],
    positions: dict[str, int],
    cash: Decimal,
    targets: list[str],
    trade_date: datetime,
    max_cash_use: Decimal | None = None,
    slippage_pct: float = 0.0005,
    weights: dict[str, float] | None = None,  # {sym: fraction}, sums to 1; None = equal weight
) -> tuple[dict[str, int], Decimal, list[dict[str, Any]]]:
    slip = Decimal(str(1 + slippage_pct))
    slip_inv = Decimal(str(1 - slippage_pct))

    # Normalise weights; fall back to equal weight if missing
    if weights and targets:
        total_w = sum(weights.get(s, 0) for s in targets) or 1
        _w = {s: Decimal(str(weights.get(s, 0) / total_w)) for s in targets}
    else:
        eq = Decimal(1) / Decimal(len(targets)) if targets else Decimal(1)
        _w = {s: eq for s in targets}

    open_prices = {sym: Decimal(str(get_price(histories, sym, trade_date, field="open"))) for sym in targets}
    if any(price <= 0 for price in open_prices.values()):
        raise BacktestError(f"Invalid open price on {trade_date.date()}")

    portfolio_value = value_of_portfolio(histories, positions, cash, trade_date)
    target_cash = portfolio_value if max_cash_use is None else min(portfolio_value, max_cash_use)

    new_positions = positions.copy()
    trades: list[dict[str, Any]] = []

    # Sell everything not in the current target basket first.
    for symbol in list(new_positions.keys()):
        if symbol not in targets and new_positions[symbol] > 0:
            qty = new_positions[symbol]
            raw_price = Decimal(str(get_price(histories, symbol, trade_date, field="open")))
            sell_price = raw_price * slip_inv  # sell slightly below open
            cash += sell_price * qty
            trades.append({"date": trade_date, "symbol": symbol, "action": "sell", "qty": qty, "price": float(sell_price)})
            new_positions[symbol] = 0

    # Compute target quantities using per-symbol weights.
    for symbol in targets:
        price = open_prices[symbol] * slip  # buy slightly above open
        target_positions_qty = int((target_cash * _w[symbol]) // price)

        current_qty = new_positions.get(symbol, 0)
        delta = target_positions_qty - current_qty
        if delta > 0:
            buy_price = open_prices[symbol] * slip
            cost = buy_price * delta
            if cost > cash:
                affordable = int(cash // buy_price)
                delta = affordable
                cost = buy_price * affordable
            if delta > 0:
                cash -= cost
                new_positions[symbol] = current_qty + delta
                trades.append({"date": trade_date, "symbol": symbol, "action": "buy", "qty": delta, "price": float(buy_price)})
        elif delta < 0:
            qty = -delta
            sell_price = open_prices[symbol] * slip_inv
            cash += sell_price * qty
            new_positions[symbol] = current_qty - qty
            trades.append({"date": trade_date, "symbol": symbol, "action": "sell", "qty": qty, "price": float(sell_price)})

    # Initialize any missing target symbols with zero quantity.
    for symbol in targets:
        new_positions.setdefault(symbol, 0)

    return new_positions, cash, trades


def compute_performance(nav: pd.Series) -> dict[str, float]:
    nav = nav.dropna()
    if nav.empty:
        return {}
    returns = nav.pct_change().fillna(0)
    total_return = float(nav.iloc[-1] / nav.iloc[0] - 1)
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    cagr = float((nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1) if years > 0 else 0.0
    peak = nav.cummax()
    drawdown = (nav / peak) - 1
    max_dd = float(drawdown.min())
    return {
        "total_return_pct": total_return * 100,
        "cagr_pct": cagr * 100,
        "max_drawdown_pct": max_dd * 100,
    }


def run_backtest(start: str, end: str, cash: Decimal, massive_key: str | None, polygon_key: str | None) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    histories = load_histories(SYMBOLS, start, end, massive_key, polygon_key)
    common_dates = intersection_dates(histories)
    if len(common_dates) < 2:
        raise BacktestError("Not enough shared historical trading days for AGG/BIL/TLT. Need at least 2 trading days.")

    positions: dict[str, int] = {symbol: 0 for symbol in SYMBOLS}
    portfolio_cash = cash
    nav = []
    trade_history: list[dict[str, Any]] = []

    for idx in range(1, len(common_dates)):
        today = common_dates[idx]
        yesterday = common_dates[idx - 1]

        if idx < 70:
            nav.append({"date": today, "value": float(value_of_portfolio(histories, positions, portfolio_cash, today))})
            continue

        try:
            targets = choose_target_symbols(histories, yesterday)
            positions, portfolio_cash, trades = rebalance_positions(histories, positions, portfolio_cash, targets, today)
        except BacktestError as exc:
            # Skip trading days if the chosen target basket is missing data on this date.
            nav.append({"date": today, "value": float(value_of_portfolio(histories, positions, portfolio_cash, today))})
            continue

        trade_history.extend(trades)
        nav_value = float(value_of_portfolio(histories, positions, portfolio_cash, today))
        nav.append({"date": today, "value": nav_value, "targets": ",".join(targets)})

    nav_df = pd.DataFrame(nav).set_index("date")
    perf = compute_performance(nav_df["value"])

    print("Backtest summary")
    print(f"Start: {start}")
    print(f"End:   {end}")
    print(f"Initial cash: ${cash}")
    print(f"Final NAV: ${nav_df['value'].iloc[-1]:,.2f}")
    print(f"Total return: {perf['total_return_pct']:.2f}%")
    print(f"CAGR: {perf['cagr_pct']:.2f}%")
    print(f"Max drawdown: {perf['max_drawdown_pct']:.2f}%")
    print(f"Trades executed: {len(trade_history)}")

    nav_df.to_csv("backtest_nav.csv")
    trade_df = pd.DataFrame(trade_history)
    trade_df.to_csv("backtest_trades.csv", index=False)
    print("Saved backtest_nav.csv and backtest_trades.csv")
    return nav_df, trade_df, perf


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest the Schwab ETF strategy with Massive (Polygon rebrand) data fallback and yfinance fallback.")
    parser.add_argument("--start", default="2022-01-01", help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2025-01-01", help="Backtest end date (YYYY-MM-DD)")
    parser.add_argument("--cash", type=float, default=100000.0, help="Starting cash for backtest")
    parser.add_argument("--massive-key", default=os.getenv("MASSIVE_API_KEY"), help="Massive API key (Polygon keys still work)")
    parser.add_argument("--polygon-key", default=os.getenv("POLYGON_API_KEY"), help="Legacy Polygon API key")
    args = parser.parse_args()

    try:
        run_backtest(args.start, args.end, Decimal(str(args.cash)), args.massive_key, args.polygon_key)
    except Exception as exc:
        print(f"Backtest failed: {exc}")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
