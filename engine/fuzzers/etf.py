"""
High-throughput ETF strategy fuzzer.

Runs N backtests in parallel, mixing random exploration with refinement
of the current best results. Saves everything to etf_results.db so the
dashboard leaderboard stays live while this runs.

Usage:
    python etf_fuzz.py                        # 30,000 runs, default dates
    python etf_fuzz.py --runs 5000
    python etf_fuzz.py --start 2020-01-01 --end 2024-12-31
    python etf_fuzz.py --workers 4            # override CPU count
    python etf_fuzz.py --explore 0.5          # 50% random, 50% refine
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import queue
import random
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd

# ── worker-process globals (set once per worker via initializer) ──────────────
_histories: dict = {}
_common: list = []
_start: str = ""
_train_end: str = ""   # inclusive cutoff for train window (e.g. 2024-12-31)
_fwd_start: str = ""
_holdout_start: str = ""  # data after this date is NEVER used for scoring/selection


def _worker_init(start: str, train_end: str, data_end: str, fwd_start: str, holdout_start: str = ""):
    """Load histories into RAM once per worker process."""
    global _histories, _common, _start, _train_end, _fwd_start, _holdout_start
    _start = start
    _train_end = train_end
    _fwd_start = fwd_start
    _holdout_start = holdout_start

    import pandas as pd
    from stratscout.engine.data.universes import ALL_SYMBOLS, ANCHORS
    from stratscout.engine.backtest.etf import load_local_histories

    # Load up to data_end (today) so forward window has 2025 data.
    # Use 300-day buffer before start to prime lookbacks.
    pre_start = (pd.Timestamp(start) - pd.Timedelta(days=300)).strftime("%Y-%m-%d")
    _histories = load_local_histories(ALL_SYMBOLS, pre_start, data_end)

    anchor_indexes = [set(_histories[a].index) for a in ANCHORS if a in _histories]
    _common = sorted(set.intersection(*anchor_indexes))


def _run_one(params: dict) -> dict | None:
    """Run a single backtest. Runs inside a worker process."""
    from decimal import Decimal
    import pandas as pd
    from stratscout.engine.backtest.etf import choose_targets, precompute_rsi_cache, _buy_and_hold_nav, compute_vol_weights
    from stratscout.engine.data.universes import ANCHORS
    from stratscout.engine.backtest.core import value_of_portfolio, rebalance_positions, compute_performance, BacktestError

    try:
        needed = set(ANCHORS)
        needed.update(params["risk_on_pool"])
        needed.update(params["risk_off_rising_pool"])
        needed.update(params["risk_off_falling_pool"])
        if params.get("rising_rate_include_uup"):
            needed.add("UUP")
        h = {k: v for k, v in _histories.items() if k in needed}
        rsi_cache = precompute_rsi_cache(h, params)

        warmup = max(
            params["agg_bil_lookback"], params["tlt_bil_lookback"],
            params["risk_on_rsi_window"], params["risk_off_rsi_window"],
        ) + 5

        start_ts     = pd.Timestamp(_start,     tz="UTC")
        train_end_ts = pd.Timestamp(_train_end, tz="UTC")
        live_from = next((i for i, d in enumerate(_common) if d >= start_ts), warmup)
        if live_from < warmup:
            live_from = warmup

        positions = {sym: 0 for sym in h}
        cash = Decimal("100000")
        nav = []
        bnh_symbols: list[str] = []
        first_live = False
        n_rebalances = 0
        min_hold = params.get("min_hold_days", 1)
        last_rebalance_idx = -min_hold

        for idx in range(1, len(_common)):
            today = _common[idx]
            yesterday = _common[idx - 1]

            if idx < live_from:
                continue
            if today > train_end_ts:   # stop train window at args.end
                break

            try:
                targets = choose_targets(h, yesterday, params, rsi_cache)
                if not first_live:
                    bnh_symbols = targets[:]
                    first_live = True
                held_long_enough = (idx - last_rebalance_idx) >= min_hold
                if {s for s, q in positions.items() if q > 0} != set(targets) and held_long_enough:
                    vww = params.get("vol_weight_window", 0)
                    vol_w = compute_vol_weights(h, targets, yesterday, vww) if vww > 0 else None
                    positions, cash, trades = rebalance_positions(h, positions, cash, targets, today, weights=vol_w)
                    n_rebalances += len(trades)
                    last_rebalance_idx = idx
            except Exception:
                pass

            nav.append({"date": today, "value": float(value_of_portfolio(h, positions, cash, today))})

        if not nav:
            return None

        nav_df = pd.DataFrame(nav).set_index("date")
        train_perf = compute_performance(nav_df["value"])

        # ── Forward window (reuse cached histories, run fresh sim) ────────────
        fwd_start_ts = pd.Timestamp(_fwd_start, tz="UTC")
        # Cap forward window at holdout boundary so holdout data never influences scoring.
        fwd_end_ts = pd.Timestamp(_holdout_start, tz="UTC") if _holdout_start else None
        fwd_live_from = next((i for i, d in enumerate(_common) if d >= fwd_start_ts), None)
        fwd_nav = []
        if fwd_live_from is not None:
            fwd_positions = {sym: 0 for sym in h}
            fwd_cash = Decimal("100000")
            fwd_last_reb = -min_hold
            for idx in range(fwd_live_from, len(_common)):
                today = _common[idx]
                if fwd_end_ts is not None and today >= fwd_end_ts:
                    break
                yesterday = _common[idx - 1]
                try:
                    targets = choose_targets(h, yesterday, params, rsi_cache)
                    held = (idx - fwd_last_reb) >= min_hold
                    if {s for s, q in fwd_positions.items() if q > 0} != set(targets) and held:
                        vww = params.get("vol_weight_window", 0)
                        vol_w = compute_vol_weights(h, targets, yesterday, vww) if vww > 0 else None
                        fwd_positions, fwd_cash, _ = rebalance_positions(h, fwd_positions, fwd_cash, targets, today, weights=vol_w)
                        fwd_last_reb = idx
                except Exception:
                    pass
                fwd_nav.append({"date": today, "value": float(value_of_portfolio(h, fwd_positions, fwd_cash, today))})

        if fwd_nav:
            fwd_df = pd.DataFrame(fwd_nav).set_index("date")
            fwd_perf = compute_performance(fwd_df["value"])
        else:
            fwd_perf = {"total_return_pct": 0, "cagr_pct": 0, "max_drawdown_pct": 0}

        # Friction penalty: asymmetric 0.10% entry + 0.20% exit per rebalance event.
        # Each rebalance event = one round-trip (entry into new positions, exit old).
        total_years = (nav_df.index[-1] - nav_df.index[0]).days / 365.25
        if fwd_nav:
            total_years += (fwd_df.index[-1] - fwd_df.index[0]).days / 365.25
        annual_trades = n_rebalances / max(total_years, 0.01)
        friction_penalty = annual_trades * 0.0030 * 100  # 0.30% round-trip, in % pts/yr

        # Consistency penalty: penalize high year-to-year return variance.
        # Spiky superstar strategies rank lower than boring-but-consistent ones.
        all_nav = nav_df.copy()
        if fwd_nav:
            all_nav = pd.concat([all_nav, fwd_df])
        annual_returns = all_nav["value"].resample("YE").last().pct_change().dropna() * 100
        consistency_penalty = float(annual_returns.std()) * 0.05 if len(annual_returns) >= 2 else 0.0

        combined = _combined_score(
            train_perf.get("cagr_pct", 0), fwd_perf.get("cagr_pct", 0),
            train_perf.get("max_drawdown_pct", 0), fwd_perf.get("max_drawdown_pct", 0),
        ) - friction_penalty - consistency_penalty

        return {
            "score": combined,
            "train_return_pct": train_perf.get("total_return_pct", 0),
            "train_cagr_pct":   train_perf.get("cagr_pct", 0),
            "train_dd_pct":     train_perf.get("max_drawdown_pct", 0),
            "fwd_return_pct":   fwd_perf.get("total_return_pct", 0),
            "fwd_cagr_pct":     fwd_perf.get("cagr_pct", 0),
            "fwd_dd_pct":       fwd_perf.get("max_drawdown_pct", 0),
            "n_trades": n_rebalances,
            "params": params,
        }
    except Exception:
        return None


# ── SQLite helpers ────────────────────────────────────────────────────────────

DB_PATH = Path("etf_results.db")  # overridden by --db arg at startup


def _init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            score REAL,
            train_return_pct REAL,
            train_cagr_pct REAL,
            train_dd_pct REAL,
            fwd_return_pct REAL,
            fwd_cagr_pct REAL,
            fwd_dd_pct REAL,
            n_trades INTEGER,
            params TEXT
        )
    """)
    con.commit()
    con.close()


def _combined_score(train_cagr: float, fwd_cagr: float,
                    train_dd: float = 0.0, fwd_dd: float = 0.0) -> float:
    """Geometric mean of train and forward CAGR.

    Caller subtracts a friction penalty (0.15% per trade/year) after calling this,
    so the final score already accounts for turnover cost.
    DD params retained for signature compatibility.
    """
    t = 1 + train_cagr / 100
    f = 1 + fwd_cagr / 100
    if t <= 0 or f <= 0:
        return min(train_cagr, fwd_cagr)
    return ((t * f) ** 0.5 - 1) * 100


def _auto_note(params: dict) -> str:
    """Short tag describing active signals — used for filtering/comparing results later."""
    parts = []
    if params.get("ema_weight", 0) > 0:
        parts.append(f"ema{params.get('ema_fast', 10)}/{params.get('ema_slow', 40)}x{params['ema_weight']}")
    if params.get("vol_score_weight", 0) > 0:
        parts.append(f"vol{params['vol_score_weight']}")
    if not parts:
        parts.append("rsi")
    return ",".join(parts)


def _save_batch(rows: list[dict]):
    con = sqlite3.connect(DB_PATH)
    con.executemany(
        "INSERT INTO results "
        "(ts, score, train_return_pct, train_cagr_pct, train_dd_pct, "
        " fwd_return_pct, fwd_cagr_pct, fwd_dd_pct, n_trades, params, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                datetime.utcnow().isoformat(),
                r["score"],
                r["train_return_pct"],
                r["train_cagr_pct"],
                r["train_dd_pct"],
                r["fwd_return_pct"],
                r["fwd_cagr_pct"],
                r["fwd_dd_pct"],
                r["n_trades"],
                json.dumps(r["params"]),
                _auto_note(r["params"]),
            )
            for r in rows
        ],
    )
    con.commit()
    con.close()


def _load_top_params(n: int = 10) -> list[dict]:
    if not DB_PATH.exists():
        return []
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT params FROM results ORDER BY score DESC LIMIT ?", (n,)
    ).fetchall()
    con.close()
    return [json.loads(r[0]) for r in rows]


# ── Param generator ───────────────────────────────────────────────────────────

def _next_params(top_params: list[dict], explore_ratio: float, exclude: list[str] | None = None) -> dict:
    from stratscout.engine.backtest.etf import random_params, refine_params
    if not top_params or random.random() < explore_ratio:
        return random_params(exclude=exclude)
    base = random.choice(top_params)
    return refine_params(base, strength=random.uniform(0.1, 0.4), exclude=exclude)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs",      type=int,   default=30_000)
    parser.add_argument("--start",     default="2018-01-01")
    parser.add_argument("--end",       default="2022-12-31")
    parser.add_argument("--fwd-start", default="2023-01-01",
                        help="Start of out-of-sample forward window")
    parser.add_argument("--workers",   type=int,   default=max(1, os.cpu_count() - 1))
    parser.add_argument("--explore",  type=float, default=0.33,
                        help="Fraction of runs that are fully random (rest refine top results)")
    parser.add_argument("--batch",    type=int,   default=50,
                        help="Save results to DB every N completions")
    parser.add_argument("--seed-id",  type=int,   default=None,
                        help="Refine exclusively around this DB row ID (sets explore=0)")
    parser.add_argument("--db", default=None,
                        help="Path to SQLite DB file (default: etf_results.db)")
    parser.add_argument("--exclude", nargs="+", default=[],
                        help="Symbols to exclude from all pools (e.g. --exclude SOXL TECL)")
    parser.add_argument("--holdout-start", default=None,
                        help="Date from which data is excluded from scoring (true blind holdout). "
                             "Recommended: 2025-01-01. Only used for reporting, never selection.")
    args = parser.parse_args()

    global DB_PATH
    if args.db:
        DB_PATH = Path(args.db)

    _init_db()

    n_workers = args.workers
    total = args.runs
    explore = args.explore
    batch_size = args.batch

    print(f"\nETF Strategy Fuzzer")
    print(f"  Runs:    {total:,}")
    print(f"  Workers: {n_workers}")
    print(f"  Explore: {explore:.0%} random / {1-explore:.0%} refine")
    print(f"  Train:   {args.start} to {args.end}")
    if args.exclude:
        print(f"  Exclude: {', '.join(args.exclude)}")
    fwd_end_label = args.holdout_start or "today"
    print(f"  Forward: {args.fwd_start} to {fwd_end_label}")
    if args.holdout_start:
        print(f"  Holdout: {args.holdout_start} onward (excluded from scoring)")
    print(f"  DB:      {DB_PATH}\n")

    # Pre-generate all param sets so we can mix explore/refine based on
    # top results updated every batch
    t_start = time.perf_counter()
    completed = 0
    saved = 0
    best_score = -999.0
    best_params: dict | None = None
    pending_save: list[dict] = []

    # If --seed-id given, lock the seed to that single row and disable exploration
    if args.seed_id is not None:
        seed_rows = _load_top_params(10000)  # load all, then filter
        con = sqlite3.connect(DB_PATH)
        row = con.execute("SELECT params FROM results WHERE id=?", (args.seed_id,)).fetchone()
        con.close()
        if not row:
            print(f"ERROR: ID {args.seed_id} not found in DB.")
            return
        seed_params = json.loads(row[0])
        top_params = [seed_params]
        explore = 0.0
        print(f"  Seed:    ID {args.seed_id} (explore=0, pure refinement)")
    else:
        top_params = _load_top_params(10)

    # Refresh top params from DB every this many completions
    top_refresh_every = 200

    from datetime import date as _date
    data_end = _date.today().strftime("%Y-%m-%d")

    with mp.Pool(
        processes=n_workers,
        initializer=_worker_init,
        initargs=(args.start, args.end, data_end, args.fwd_start, args.holdout_start or ""),
    ) as pool:
        # Feed params lazily so we can update the pool after each batch
        def param_gen():
            nonlocal top_params
            for i in range(total):
                if args.seed_id is None and i > 0 and i % top_refresh_every == 0:
                    top_params = _load_top_params(10)
                yield _next_params(top_params, explore, exclude=args.exclude or None)

        for result in pool.imap_unordered(_run_one, param_gen(), chunksize=4):
            completed += 1

            if result is not None:
                pending_save.append(result)
                if result["score"] > best_score:
                    best_score = result["score"]
                    best_params = result["params"]

            if len(pending_save) >= batch_size:
                _save_batch(pending_save)
                saved += len(pending_save)
                pending_save.clear()

            # Progress line
            elapsed = time.perf_counter() - t_start
            rate = completed / elapsed
            eta_s = (total - completed) / rate if rate > 0 else 0
            eta = str(timedelta(seconds=int(eta_s)))
            pct = completed / total * 100
            print(
                f"\r  {completed:>6,}/{total:,} ({pct:4.1f}%)  "
                f"{rate:4.1f} runs/s  ETA {eta}  "
                f"best {best_score:+.1f}%",
                end="", flush=True,
            )

        # Flush remainder
        if pending_save:
            _save_batch(pending_save)
            saved += len(pending_save)

    elapsed = time.perf_counter() - t_start
    print(f"\n\nDone in {timedelta(seconds=int(elapsed))}.")
    print(f"Saved {saved:,} valid results to {DB_PATH}.")
    if best_params:
        print(f"Best score: {best_score:+.1f}% total return")
        print(f"Best params: {json.dumps(best_params, indent=2)}")


if __name__ == "__main__":
    mp.freeze_support()
    main()
