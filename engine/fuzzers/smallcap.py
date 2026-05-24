"""
Small-cap volume anomaly strategy fuzzer.

Runs backtests in parallel across all CPU cores, saving results to SQLite
so the dashboard leaderboard stays live while this runs.

Usage:
    python smallcap_fuzz.py                        # 1000 runs, default dates
    python smallcap_fuzz.py --runs 5000
    python smallcap_fuzz.py --explore 0.5          # 50% random, 50% refine
    python smallcap_fuzz.py --workers 4
    python smallcap_fuzz.py --seed-id 42           # refine around one DB row
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import random
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

DB_PATH  = Path("smallcap_results.db")
DATA_DIR = Path("data/smallcap")

# ── Worker globals (set once per process via initializer) ─────────────────────

_data: dict = {}
_train_start: str = ""
_train_end:   str = ""
_fwd_start:   str = ""
_fwd_end:     str = ""
_recent_start: str = ""
_recent_end:   str = ""


def _worker_init(train_start, train_end, fwd_start, fwd_end, recent_start, recent_end):
    global _data, _train_start, _train_end, _fwd_start, _fwd_end, _recent_start, _recent_end
    _train_start  = train_start
    _train_end    = train_end
    _fwd_start    = fwd_start
    _fwd_end      = fwd_end
    _recent_start = recent_start
    _recent_end   = recent_end

    from stratscout.engine.backtest.smallcap import load_data
    from stratscout.engine.data.universes import smallcap_universe
    _data = load_data(smallcap_universe())


def _run_window(params: dict, start: str, end: str) -> dict | None:
    from stratscout.engine.backtest.smallcap import find_signals, run_backtest
    try:
        sigs = find_signals(_data, params["vol_lookback"], params["vol_mult"], start, end,
                            require_green=params.get("require_green", False))
        if sigs.empty:
            return None
        return run_backtest(_data, sigs, params["hold_days"], params["max_positions"])
    except Exception:
        return None


def _run_one(params: dict) -> dict | None:
    train_r  = _run_window(params, _train_start, _train_end)
    if train_r is None or train_r["n_trades"] < 10:
        return None
    fwd_r    = _run_window(params, _fwd_start,    _fwd_end)
    recent_r = _run_window(params, _recent_start, _recent_end)

    train_cagr = train_r["cagr_pct"]
    fwd_cagr   = fwd_r["cagr_pct"] if fwd_r else None
    wr_factor  = (train_r["win_rate_pct"] / 100) / 0.5

    if fwd_cagr is not None:
        t = 1 + train_cagr / 100
        f = 1 + fwd_cagr / 100
        score = (((t * f) ** 0.5 - 1) * 100 * wr_factor) if t > 0 and f > 0 else min(train_cagr, fwd_cagr) * wr_factor
    else:
        score = train_cagr * wr_factor

    return {
        "score":          score,
        "total_return":   train_r["total_return_pct"],
        "cagr":           train_r["cagr_pct"],
        "max_dd":         train_r["max_drawdown_pct"],
        "n_trades":       train_r["n_trades"],
        "win_rate":       train_r["win_rate_pct"],
        "avg_win":        train_r["avg_win_pct"],
        "avg_loss":       train_r["avg_loss_pct"],
        "fwd_return":     fwd_r["total_return_pct"]   if fwd_r    else None,
        "fwd_cagr":       fwd_r["cagr_pct"]           if fwd_r    else None,
        "fwd_max_dd":     fwd_r["max_drawdown_pct"]   if fwd_r    else None,
        "fwd_win_rate":   fwd_r["win_rate_pct"]       if fwd_r    else None,
        "recent_return":  recent_r["total_return_pct"] if recent_r else None,
        "recent_cagr":    recent_r["cagr_pct"]         if recent_r else None,
        "params":         params,
    }


# ── SQLite ────────────────────────────────────────────────────────────────────

def _init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              TEXT,
            score           REAL,
            total_return    REAL,
            cagr            REAL,
            max_dd          REAL,
            n_trades        INTEGER,
            win_rate        REAL,
            avg_win         REAL,
            avg_loss        REAL,
            fwd_return      REAL,
            fwd_cagr        REAL,
            fwd_max_dd      REAL,
            fwd_win_rate    REAL,
            recent_return   REAL,
            recent_cagr     REAL,
            params          TEXT,
            note            TEXT
        )
    """)
    existing = {r[1] for r in con.execute("PRAGMA table_info(results)")}
    for col, typ in [("fwd_return","REAL"),("fwd_cagr","REAL"),("fwd_max_dd","REAL"),
                     ("fwd_win_rate","REAL"),("recent_return","REAL"),("recent_cagr","REAL")]:
        if col not in existing:
            con.execute(f"ALTER TABLE results ADD COLUMN {col} {typ}")
    con.commit()
    con.close()


def _save_batch(rows: list[dict]):
    con = sqlite3.connect(DB_PATH)
    con.executemany(
        "INSERT INTO results (ts,score,total_return,cagr,max_dd,n_trades,win_rate,avg_win,avg_loss,"
        "fwd_return,fwd_cagr,fwd_max_dd,fwd_win_rate,recent_return,recent_cagr,params) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(
            datetime.utcnow().isoformat(),
            r["score"], r["total_return"], r["cagr"], r["max_dd"], r["n_trades"],
            r["win_rate"], r["avg_win"], r["avg_loss"],
            r["fwd_return"], r["fwd_cagr"], r["fwd_max_dd"], r["fwd_win_rate"],
            r["recent_return"], r["recent_cagr"],
            json.dumps(r["params"]),
        ) for r in rows],
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


def _load_seed_params(seed_id: int) -> dict | None:
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT params FROM results WHERE id=?", (seed_id,)).fetchone()
    con.close()
    return json.loads(row[0]) if row else None


# ── Param generator ───────────────────────────────────────────────────────────

def _random_params() -> dict:
    return {
        "vol_mult":      round(random.uniform(2.0, 12.0), 1),
        "vol_lookback":  random.randint(5, 40),
        "hold_days":     random.randint(1, 15),
        "max_positions": random.randint(3, 25),
        "require_green": random.choice([True, False]),
    }


def _refine_params(base: dict, strength: float = 0.3) -> dict:
    def jitter(val, lo, hi, is_int=False):
        spread = max(1 if is_int else 0.1, (hi - lo) * strength * 0.3)
        new = val + random.uniform(-spread, spread)
        new = max(lo, min(hi, new))
        return int(round(new)) if is_int else round(new, 1)
    return {
        "vol_mult":      jitter(base["vol_mult"],      2.0, 12.0),
        "vol_lookback":  jitter(base["vol_lookback"],  5,   40,  is_int=True),
        "hold_days":     jitter(base["hold_days"],     1,   15,  is_int=True),
        "max_positions": jitter(base["max_positions"], 3,   25,  is_int=True),
        "require_green": base.get("require_green", False),
    }


def _next_params(top_params: list[dict], explore: float) -> dict:
    if not top_params or random.random() < explore:
        return _random_params()
    return _refine_params(random.choice(top_params), strength=random.uniform(0.1, 0.4))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs",       type=int,   default=1_000)
    parser.add_argument("--train-start", default="2025-01-01")
    parser.add_argument("--train-end",   default="2026-01-31")
    parser.add_argument("--fwd-start",   default="2026-02-01")
    parser.add_argument("--fwd-end",     default="2026-05-03")
    parser.add_argument("--workers",    type=int,   default=max(1, os.cpu_count() - 1))
    parser.add_argument("--explore",    type=float, default=0.33)
    parser.add_argument("--batch",      type=int,   default=50)
    parser.add_argument("--seed-id",    type=int,   default=None,
                        help="Refine exclusively around this DB row ID")
    args = parser.parse_args()

    _init_db()

    # 90-day recent window
    recent_end   = args.fwd_end
    recent_start = (pd.Timestamp(recent_end) - pd.Timedelta(days=90)).strftime("%Y-%m-%d")

    if args.seed_id is not None:
        seed = _load_seed_params(args.seed_id)
        if not seed:
            print(f"ERROR: ID {args.seed_id} not found in DB.")
            return
        top_params = [seed]
        explore    = 0.0
        print(f"  Seed: ID {args.seed_id} (pure refinement)")
    else:
        top_params = _load_top_params(10)
        explore    = args.explore

    print(f"\nSmall-Cap Volume Anomaly Fuzzer")
    print(f"  Runs:    {args.runs:,}")
    print(f"  Workers: {args.workers}")
    print(f"  Explore: {explore:.0%} random / {1-explore:.0%} refine")
    print(f"  Train:   {args.train_start} to {args.train_end}")
    print(f"  Forward: {args.fwd_start} to {args.fwd_end}")
    print(f"  Recent:  {recent_start} to {recent_end}")
    print(f"  DB:      {DB_PATH}\n")

    t_start    = time.perf_counter()
    completed  = 0
    saved      = 0
    best_score = -999.0
    pending:   list[dict] = []
    top_refresh_every = 200

    with mp.Pool(
        processes=args.workers,
        initializer=_worker_init,
        initargs=(args.train_start, args.train_end,
                  args.fwd_start,   args.fwd_end,
                  recent_start,     recent_end),
    ) as pool:
        def param_gen():
            nonlocal top_params
            for i in range(args.runs):
                if args.seed_id is None and i > 0 and i % top_refresh_every == 0:
                    top_params = _load_top_params(10)
                yield _next_params(top_params, explore)

        for result in pool.imap_unordered(_run_one, param_gen(), chunksize=4):
            completed += 1
            if result is not None:
                pending.append(result)
                if result["score"] > best_score:
                    best_score = result["score"]

            if len(pending) >= args.batch:
                _save_batch(pending)
                saved  += len(pending)
                pending.clear()

            elapsed = time.perf_counter() - t_start
            rate    = completed / elapsed
            eta_s   = (args.runs - completed) / rate if rate > 0 else 0
            print(
                f"\r  {completed:>6,}/{args.runs:,}  "
                f"{rate:4.1f}/s  ETA {timedelta(seconds=int(eta_s))}  "
                f"best {best_score:+.1f}",
                end="", flush=True,
            )

        if pending:
            _save_batch(pending)
            saved += len(pending)

    elapsed = time.perf_counter() - t_start
    print(f"\n\nDone in {timedelta(seconds=int(elapsed))}. Saved {saved:,} results.")


if __name__ == "__main__":
    mp.freeze_support()
    main()
