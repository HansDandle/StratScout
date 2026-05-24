"""
Walk-forward validator for the ETF rotation strategy.

For each month M from --start to --end:
  Train:    fuzz N trials on [M-12mo, M)  split into 3 sub-windows → find best param set
  Validate: run that param set on [M, M+1mo) → record out-of-sample result

With 7 years of ETF data we use a 12-month training window (vs 3 months for options)
for much more stable signal. Walk-forward over 2020-2026 gives ~72 out-of-sample months.

Results saved to walk_forward_etf.db (never wiped — reruns skip completed months).

Usage:
    python walk_forward_etf.py
    python walk_forward_etf.py --start 2020-01-01 --trials 300 --workers 4
    python walk_forward_etf.py --summary   # just print results, no new runs
"""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import os
import sqlite3
import time
from datetime import date
from pathlib import Path

import pandas as pd
from dateutil.relativedelta import relativedelta

DB_PATH = Path("walk_forward_etf.db")

# Worker globals
_histories: dict = {}
_factors: dict = {}  # {name: pd.Series} loaded in _worker_init if factors exist
_no_factors: bool = False  # set True via --no-factors to run pure baseline
_use_calmar: bool = True   # set False via --no-calmar to use raw geo-mean scoring


# ── Scoring: Calmar-based (CAGR / |MaxDD| per sub-window) ────────────────────

def _score_calmar(w1_cagr, w2_cagr, w3_cagr,
                  w1_dd=0.0, w2_dd=0.0, w3_dd=0.0,
                  w1_tr=1, w2_tr=1, w3_tr=1) -> float:
    """Score = weighted average of per-window Calmar ratios (CAGR / |MaxDD|).
    Cash sub-windows (trades=0) are neutral — not penalised — because a
    stop-loss correctly parking in cash should not hurt the score.
    """
    worst_dd = min(w1_dd, w2_dd, w3_dd)
    if worst_dd < -60.0:
        return -999.0
    if max(w1_cagr, w2_cagr, w3_cagr) > 500.0:
        return -999.0

    def _calmar(cagr: float, dd: float, trades: int) -> float:
        if trades == 0:
            return 0.0  # neutral: cash is fine, just not scored
        if cagr <= 0:
            return cagr / 10.0
        return min(cagr / max(abs(dd), 0.5), 200.0)

    c1 = _calmar(w1_cagr, w1_dd, w1_tr)
    c2 = _calmar(w2_cagr, w2_dd, w2_tr)
    c3 = _calmar(w3_cagr, w3_dd, w3_tr)

    weights = [1.0, 1.25, 1.5]
    score = (weights[0]*c1 + weights[1]*c2 + weights[2]*c3) / sum(weights)

    # Consistency bonus for all-positive windows
    n_pos = sum(1 for c in (w1_cagr, w2_cagr, w3_cagr) if c > 0)
    score *= (1.0 + 0.1 * n_pos)
    # Penalty for negative windows
    n_neg = sum(1 for c in (w1_cagr, w2_cagr, w3_cagr) if c < 0)
    if n_neg > 0:
        score *= (0.6 ** n_neg)
    # No idle penalty: cash after a stop-out is the correct outcome
    return score


def _score_raw(w1_cagr, w2_cagr, w3_cagr,
               w1_dd=0.0, w2_dd=0.0, w3_dd=0.0,
               w1_tr=1, w2_tr=1, w3_tr=1) -> float:
    """Original geo-mean score with Calmar-style DD penalty. Used with --no-calmar."""
    worst_dd = min(w1_dd, w2_dd, w3_dd)
    if worst_dd < -60.0:
        return -999.0
    if max(w1_cagr, w2_cagr, w3_cagr) > 500.0:
        return -999.0

    CAP = 500.0
    w1c = max(-100.0, min(CAP, w1_cagr))
    w2c = max(-100.0, min(CAP, w2_cagr))
    w3c = max(-100.0, min(CAP, w3_cagr))

    weights = [1, 1.25, 1.5]
    factors = [1 + c / 100 for c in (w1c, w2c, w3c)]
    if any(f <= 0 for f in factors):
        return min(w1c, w2c, w3c)
    total_w = sum(weights)
    log_avg = sum(w * math.log(f) for w, f in zip(weights, factors)) / total_w
    raw = (math.exp(log_avg) - 1) * 100

    dd_penalty = 1.0 / (1.0 + abs(worst_dd) / 20.0)
    score = raw * dd_penalty

    n_pos = sum(1 for c in (w1c, w2c, w3c) if c > 0)
    score *= (1.0 + 0.15 * n_pos)
    n_neg = sum(1 for c in (w1c, w2c, w3c) if c < 0)
    if n_neg > 0:
        score *= (0.5 ** n_neg)
    n_idle = sum(1 for t in (w1_tr, w2_tr, w3_tr) if t == 0)
    if n_idle > 0:
        score *= (0.7 ** n_idle)
    return score


def _combined_score(w1_cagr, w2_cagr, w3_cagr,
                    w1_dd=0.0, w2_dd=0.0, w3_dd=0.0,
                    w1_tr=1, w2_tr=1, w3_tr=1) -> float:
    if _use_calmar:
        return _score_calmar(w1_cagr, w2_cagr, w3_cagr, w1_dd, w2_dd, w3_dd, w1_tr, w2_tr, w3_tr)
    return _score_raw(w1_cagr, w2_cagr, w3_cagr, w1_dd, w2_dd, w3_dd, w1_tr, w2_tr, w3_tr)


# ── Worker ────────────────────────────────────────────────────────────────────

def _worker_init(no_factors: bool = False):
    global _histories, _factors
    from stratscout.engine.backtest.etf import load_local_histories
    from stratscout.engine.data.universes import ALL_SYMBOLS
    _histories = load_local_histories(ALL_SYMBOLS, "2018-01-01", date.today().isoformat())
    if no_factors:
        _factors = {}
    else:
        try:
            from stratscout.engine.data.factors import load_local_factors
            _factors = load_local_factors()
        except Exception:
            _factors = {}
    label = "no-factors (baseline)" if no_factors else f"{len(_factors)} factors"
    print(f"  [worker {os.getpid()}] ready - {len(_histories)} symbols, {label}", flush=True)


def _run_backtest(params: dict, start: str, end: str) -> tuple:
    """Returns (total_return_pct, cagr_pct, max_dd_pct, n_trades)."""
    from stratscout.engine.backtest.etf import run_etf_backtest
    try:
        r = run_etf_backtest(params, start, end, cash=10_000.0)
        p = r["perf"]
        trade_df = r.get("trade_df")
        return (
            p.get("total_return_pct", 0),
            p.get("cagr_pct", 0),
            p.get("max_drawdown_pct", 0),
            len(trade_df) if trade_df is not None else 0,
        )
    except Exception:
        return (0.0, 0.0, 0.0, 0)


def _make_train_params(t_start: str, t_mid1: str, t_mid2: str, t_end: str,
                       n_trials: int, exclude: list[str]) -> tuple[dict | None, float]:
    """Fuzz n_trials on 3 training sub-windows, return (best_params, best_score)."""
    import random
    from stratscout.engine.backtest.etf import random_params, refine_params

    best_score = -999.0
    best_params = None
    top: list[dict] = []

    for _ in range(n_trials):
        if not top or random.random() < 0.5:
            p = random_params(exclude=exclude)
        else:
            base = random.choice(top)
            p = refine_params(base, strength=random.uniform(0.1, 0.4), exclude=exclude)

        r1 = _run_backtest(p, t_start, t_mid1)
        r2 = _run_backtest(p, t_mid1,  t_mid2)
        r3 = _run_backtest(p, t_mid2,  t_end)

        score = _combined_score(
            r1[1], r2[1], r3[1],
            r1[2], r2[2], r3[2],
            r1[3], r2[3], r3[3],
        )

        if score > best_score:
            best_score = score
            best_params = p.copy()

        if score > 0:
            p["__score__"] = score
            top.append(p)
            if len(top) > 20:
                top.sort(key=lambda x: x.get("__score__", 0), reverse=True)
                top = top[:20]

    return best_params, best_score


def _make_train_params_fast(t_start: str, t_end: str,
                            n_trials: int, exclude: list[str]) -> tuple[dict | None, float]:
    """Single-window version: 3× faster, slightly less overfit-resistant."""
    import random
    from stratscout.engine.backtest.etf import random_params, refine_params

    best_score = -999.0
    best_params = None
    top: list[dict] = []

    for _ in range(n_trials):
        if not top or random.random() < 0.5:
            p = random_params(exclude=exclude)
        else:
            base = random.choice(top)
            p = refine_params(base, strength=random.uniform(0.1, 0.4), exclude=exclude)

        r = _run_backtest(p, t_start, t_end)
        # Mirror the 3-window scoring using the single window for all three slots
        score = _combined_score(r[1], r[1], r[1], r[2], r[2], r[2], r[3], r[3], r[3])

        if score > best_score:
            best_score = score
            best_params = p.copy()

        if score > 0:
            p["__score__"] = score
            top.append(p)
            if len(top) > 20:
                top.sort(key=lambda x: x.get("__score__", 0), reverse=True)
                top = top[:20]

    return best_params, best_score


def _build_optuna_params(trial, exclude: list[str]) -> dict:
    """Define ETF param space for an optuna trial."""
    from stratscout.engine.data.universes import (
        RISK_ON_POOL, RISK_OFF_RISING_POOL, RISK_OFF_FALLING_POOL,
        MIN_RISK_ON, MIN_RISK_OFF_RISING, MIN_RISK_OFF_FALLING,
    )
    excl = set(exclude or [])
    on_universe      = [s for s in RISK_ON_POOL        if s not in excl]
    rising_universe  = [s for s in RISK_OFF_RISING_POOL  if s not in excl]
    falling_universe = [s for s in RISK_OFF_FALLING_POOL if s not in excl]

    # Pool membership — binary inclusion per symbol, enforce minimums
    on_pool = [s for s in on_universe if trial.suggest_categorical(f"on_{s}", [True, False])]
    if len(on_pool) < MIN_RISK_ON:
        on_pool = on_universe[:MIN_RISK_ON]

    rising_pool = [s for s in rising_universe if trial.suggest_categorical(f"rise_{s}", [True, False])]
    if len(rising_pool) < MIN_RISK_OFF_RISING:
        rising_pool = rising_universe[:MIN_RISK_OFF_RISING]

    falling_pool = [s for s in falling_universe if trial.suggest_categorical(f"fall_{s}", [True, False])]
    if len(falling_pool) < MIN_RISK_OFF_FALLING:
        falling_pool = falling_universe[:MIN_RISK_OFF_FALLING]

    include_uup = trial.suggest_categorical("rising_rate_include_uup", [True, False])
    n_risk_on = trial.suggest_int("n_risk_on", MIN_RISK_ON, min(3, len(on_pool)))
    n_rising  = trial.suggest_int("n_risk_off_rising", MIN_RISK_OFF_RISING, min(3, len(rising_pool)))
    n_falling = trial.suggest_int("n_risk_off_falling", MIN_RISK_OFF_FALLING, min(5, len(falling_pool)))

    return {
        "agg_bil_lookback":      trial.suggest_int("agg_bil_lookback", 70, 110),
        "tlt_bil_lookback":      trial.suggest_int("tlt_bil_lookback", 5, 20),
        "risk_on_rsi_window":    trial.suggest_int("risk_on_rsi_window", 15, 30),
        "risk_off_rsi_window":   trial.suggest_int("risk_off_rsi_window", 5, 25),
        "risk_on_rsi_direction": trial.suggest_categorical("risk_on_rsi_direction", ["lowest", "highest"]),
        "risk_off_rsi_direction":trial.suggest_categorical("risk_off_rsi_direction", ["lowest", "highest"]),
        "n_risk_on":             n_risk_on,
        "n_risk_off_rising":     n_rising + (1 if include_uup else 0),
        "n_risk_off_falling":    n_falling,
        "risk_on_pool":          on_pool,
        "risk_off_rising_pool":  rising_pool,
        "risk_off_falling_pool": falling_pool,
        "rising_rate_include_uup": include_uup,
        "min_hold_days":         trial.suggest_int("min_hold_days", 4, 12),
        "vol_weight_window":     trial.suggest_categorical("vol_weight_window", [0, 0, 0, 0, 5, 10]),
        "vol_score_weight":      trial.suggest_categorical("vol_score_weight", [0.0, 0.0, 0.0, 0.1, 0.2]),
        "vol_score_window":      trial.suggest_categorical("vol_score_window", [10, 15, 20, 30]),
        "vol_surge_cap":         trial.suggest_categorical("vol_surge_cap", [2.0, 3.0, 4.0, 5.0]),
        "ema_weight":            trial.suggest_categorical("ema_weight", [0.0, 0.0, 0.0, 0.0, 0.1, 0.2, 0.3, 0.5]),
        "ema_fast":              trial.suggest_categorical("ema_fast", [5, 8, 10, 12, 15, 20]),
        "ema_slow":              trial.suggest_categorical("ema_slow", [20, 30, 40, 50, 60, 80, 100]),
        "sector_diverse":        False,
        # Combo weighting: momentum × (1/vol), normalized
        "combo_momentum_lookback": trial.suggest_int("combo_momentum_lookback", 5, 63),
        "combo_vol_lookback":      trial.suggest_int("combo_vol_lookback", 5, 42),
        "combo_alpha":             trial.suggest_float("combo_alpha", 0.0, 1.0),
        "combo_max_weight":        trial.suggest_float("combo_max_weight", 0.3, 1.0),
        # Stop-loss: exit to cash if portfolio drops this % from entry (0 = disabled)
        "stop_loss_pct":           trial.suggest_float("stop_loss_pct", 4.0, 20.0),
        "stop_loss_lockout_days":  trial.suggest_int("stop_loss_lockout_days", 15, 30),
        "vol_target_pct":          trial.suggest_float("vol_target_pct", 0.0, 15.0),
        "vol_target_lookback":     trial.suggest_int("vol_target_lookback", 10, 30),
    }


def _apply_factor_overrides(params: dict, month_start: str) -> dict:
    """Factor overrides disabled — pure price signal outperforms all tested overrides."""
    return params


# Scalar param keys that map directly to Optuna suggest_* names — used for HoF seeding.
_OPTUNA_SCALAR_KEYS = {
    "agg_bil_lookback", "tlt_bil_lookback", "risk_on_rsi_window", "risk_off_rsi_window",
    "risk_on_rsi_direction", "risk_off_rsi_direction", "n_risk_on", "n_risk_off_rising",
    "n_risk_off_falling", "rising_rate_include_uup", "min_hold_days",
    "vol_weight_window", "vol_score_weight", "vol_score_window", "vol_surge_cap",
    "ema_weight", "ema_fast", "ema_slow",
    "fg_fear_threshold", "fg_greed_threshold", "fomc_caution_days",
    "opex_caution_days", "layoffs_caution_zscore",
    "combo_momentum_lookback", "combo_vol_lookback", "combo_alpha", "combo_max_weight",
    "stop_loss_pct", "stop_loss_lockout_days", "vol_target_pct", "vol_target_lookback",
}


def _run_month_optuna(args_tuple) -> dict:
    """Bayesian (TPE) variant of _run_month. Uses optuna for sample-efficient search.

    args_tuple positions:
      0  month_start, 1  month_end, 2  t_start, 3  t_mid1, 4  t_mid2,
      5  n_trials,    6  exclude,   7  fast_mode, 8  workers,
      9  seed_list (optional list[dict] from HoF)
    """
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    month_start = args_tuple[0]
    month_end   = args_tuple[1]
    t_start     = args_tuple[2]
    t_mid1      = args_tuple[3]
    t_mid2      = args_tuple[4]
    n_trials    = args_tuple[5]
    exclude     = args_tuple[6]
    fast_mode   = args_tuple[7] if len(args_tuple) > 7 else False
    workers     = args_tuple[8] if len(args_tuple) > 8 else 1
    seed_list   = args_tuple[9] if len(args_tuple) > 9 else []

    def objective(trial):
        p = _build_optuna_params(trial, exclude)
        if fast_mode:
            r = _run_backtest(p, t_start, month_start)
            return _combined_score(r[1], r[1], r[1], r[2], r[2], r[2], r[3], r[3], r[3])
        else:
            r1 = _run_backtest(p, t_start, t_mid1)
            r2 = _run_backtest(p, t_mid1, t_mid2)
            r3 = _run_backtest(p, t_mid2, month_start)
            return _combined_score(r1[1], r2[1], r3[1], r1[2], r2[2], r3[2], r1[3], r2[3], r3[3])

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(n_startup_trials=min(20, n_trials // 3)),
    )

    # Enqueue HoF seeds — evaluated first, then Optuna explores from there.
    n_seeded = 0
    for seed_params in (seed_list or []):
        seed_scalar = {k: v for k, v in seed_params.items() if k in _OPTUNA_SCALAR_KEYS}
        if seed_scalar:
            try:
                study.enqueue_trial(seed_scalar)
                n_seeded += 1
            except Exception:
                pass

    import os as _os, time as _time
    seed_note = f" ({n_seeded} seeds)" if n_seeded else ""
    print(f"  [Bayesian pid={_os.getpid()}] {month_start} — {n_trials} trials, {workers} jobs{seed_note}", flush=True)
    _t0 = _time.monotonic()
    study.optimize(objective, n_trials=n_trials, n_jobs=workers, show_progress_bar=False)
    print(f"  [Bayesian pid={_os.getpid()}] {month_start} done in {_time.monotonic()-_t0:.0f}s best={study.best_value:.3f}", flush=True)

    best = study.best_trial
    best_params = _build_optuna_params(best, exclude) if best.params else None
    # Reconstruct params from best trial's suggest values
    class _FakeTrial:
        def __init__(self, params): self._p = params
        def suggest_int(self, name, *a, **kw): return self._p.get(name, a[0])
        def suggest_float(self, name, *a, **kw): return self._p.get(name, a[0])
        def suggest_categorical(self, name, choices): return self._p.get(name, choices[0])
    best_params = _build_optuna_params(_FakeTrial(best.params), exclude)
    train_score = best.value if best.value is not None else -999.0

    if best_params is None:
        return {
            "month": month_start, "train_score": -999.0,
            "val_return": 0.0, "val_cagr": 0.0, "val_dd": 0.0,
            "val_trades": 0, "positive": False, "params": None,
        }

    best_params = _apply_factor_overrides(best_params, month_start)
    val = _run_backtest(best_params, month_start, month_end)
    return {
        "month":       month_start,
        "train_score": train_score,
        "val_return":  val[0],
        "val_cagr":    val[1],
        "val_dd":      val[2],
        "val_trades":  val[3],
        "positive":    val[0] > 0,
        "params":      json.dumps(best_params, sort_keys=True, default=str),
    }


def _run_gp_backtest(strategy: dict, start: str, end: str) -> tuple:
    """Returns (total_return_pct, cagr_pct, max_dd_pct, n_trades) for a GP strategy."""
    from stratscout.engine.fuzzers.gp_backtest import run_gp_backtest
    try:
        r = run_gp_backtest(strategy, _histories, start, end, cash=10_000.0)
        p = r["perf"]
        return (
            p.get("total_return_pct", 0),
            p.get("cagr_pct", 0),
            p.get("max_drawdown_pct", 0),
            r["n_trades"],
        )
    except Exception:
        return (0.0, 0.0, 0.0, 0)


def _run_month_gp(args_tuple) -> dict:
    """GP-evolution variant of _run_month. args: (month_start, month_end, t_start,
    t_mid1, t_mid2, population_size, n_generations, exclude)."""
    month_start, month_end, t_start, t_mid1, t_mid2 = args_tuple[:5]
    population_size = int(args_tuple[5]) if len(args_tuple) > 5 else 100
    n_generations   = int(args_tuple[6]) if len(args_tuple) > 6 else 30

    from stratscout.engine.fuzzers.gp_evolution import evolve_in_worker
    best_strategy, train_score = evolve_in_worker(
        t_start, t_mid1, t_mid2, month_start,
        population_size=population_size,
        n_generations=n_generations,
    )

    if best_strategy is None:
        return {
            "month": month_start, "train_score": -999.0,
            "val_return": 0.0, "val_cagr": 0.0, "val_dd": 0.0,
            "val_trades": 0, "positive": False, "params": None,
        }

    val = _run_gp_backtest(best_strategy, month_start, month_end)
    return {
        "month":       month_start,
        "train_score": train_score,
        "val_return":  val[0],
        "val_cagr":    val[1],
        "val_dd":      val[2],
        "val_trades":  val[3],
        "positive":    val[0] > 0,
        "params":      json.dumps(best_strategy, sort_keys=True, default=str),
    }


def _run_month(args_tuple) -> dict:
    import os as _os, time as _time
    fast_mode = args_tuple[7] if len(args_tuple) > 7 else False
    month_start, month_end, t_start, t_mid1, t_mid2, n_trials, exclude = args_tuple[:7]
    print(f"  [Random pid={_os.getpid()}] {month_start} — {n_trials} trials", flush=True)
    _t0 = _time.monotonic()

    if fast_mode:
        best_params, train_score = _make_train_params_fast(
            t_start, month_start, n_trials, exclude
        )
    else:
        best_params, train_score = _make_train_params(
            t_start, t_mid1, t_mid2, month_start, n_trials, exclude
        )

    if best_params is None:
        return {
            "month": month_start, "train_score": -999.0,
            "val_return": 0.0, "val_cagr": 0.0, "val_dd": 0.0,
            "val_trades": 0, "positive": False, "params": None,
        }

    val = _run_backtest(best_params, month_start, month_end)
    return {
        "month":       month_start,
        "train_score": train_score,
        "val_return":  val[0],
        "val_cagr":    val[1],
        "val_dd":      val[2],
        "val_trades":  val[3],
        "positive":    val[0] > 0,
        "params":      json.dumps(best_params, sort_keys=True, default=str),
    }


# ── DB ────────────────────────────────────────────────────────────────────────

def _init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            month       TEXT UNIQUE,
            train_score REAL,
            val_return  REAL,
            val_cagr    REAL,
            val_dd      REAL,
            val_trades  INTEGER,
            positive    INTEGER,
            params      TEXT
        )
    """)
    con.commit()
    con.close()


def _save(row: dict):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT OR REPLACE INTO results "
        "(month, train_score, val_return, val_cagr, val_dd, val_trades, positive, params) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (row["month"], row["train_score"], row["val_return"], row["val_cagr"],
         row["val_dd"], row["val_trades"], int(row["positive"]), row["params"])
    )
    con.commit()
    con.close()


def _spy_return(month_start: str, month_end: str) -> float:
    try:
        spy = pd.read_feather("data/daily/SPY.feather")
        spy["date"] = pd.to_datetime(spy["date"], utc=True)
        spy = spy.set_index("date").sort_index()
        s = pd.Timestamp(month_start, tz="UTC")
        e = pd.Timestamp(month_end,   tz="UTC")
        sub = spy.loc[s:e, "close"]
        if len(sub) < 2:
            return 0.0
        return (sub.iloc[-1] / sub.iloc[0] - 1) * 100
    except Exception:
        return 0.0


def _print_summary():
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT month, train_score, val_return, val_cagr, val_dd, val_trades, positive "
        "FROM results ORDER BY month"
    ).fetchall()
    con.close()
    if not rows:
        print("No results yet.")
        return

    print(f"\n{'Month':<12} {'SPY%':>6} {'TrainScore':>10} {'ValReturn':>10} {'ValDD':>8} {'Trades':>7} {'Verdict':>12}")
    print("-" * 80)

    hits = active = missed_up = correct_out = losses = 0
    monthly_returns = []

    for r in rows:
        month_end = (date.fromisoformat(r[0]) + relativedelta(months=1)).isoformat()
        spy_ret   = _spy_return(r[0], month_end)
        market_up = spy_ret > 1.0

        if r[5] == 0:
            verdict = "MISSED-UP" if market_up else "cash-ok"
            if market_up:
                missed_up += 1
            else:
                correct_out += 1
            monthly_returns.append(0.0)
        elif r[6]:
            verdict = "HIT"
            hits += 1; active += 1
            monthly_returns.append(r[2])
        else:
            verdict = "LOSS"
            losses += 1; active += 1
            monthly_returns.append(r[2])

        print(f"{r[0]:<12} {spy_ret:>+6.1f}% {r[1]:>+10.1f} {r[2]:>+10.1f}% {r[4]:>+8.1f}% {r[5]:>7} {verdict:>12}")

    total = len(rows)
    avg_ret = sum(monthly_returns) / len(monthly_returns) if monthly_returns else 0

    print(f"\nSummary ({total} months):")
    print(f"  HIT (traded, positive):            {hits}")
    print(f"  LOSS (traded, negative):           {losses}")
    print(f"  MISSED-UP (no trade, mkt up):      {missed_up}")
    print(f"  Cash-OK (no trade, mkt flat/down): {correct_out}")
    print(f"  Avg monthly return (all months):   {avg_ret:+.2f}%")

    if active > 0:
        print(f"\n  Active win rate: {hits}/{active} = {hits/active*100:.0f}%")
    up_months = hits + losses + missed_up
    if up_months > 0:
        print(f"  Up-month capture: {hits}/{up_months} = {hits/up_months*100:.0f}%")

    # Compound the monthly returns to show equity curve
    equity = 10_000.0
    for ret in monthly_returns:
        equity *= (1 + ret / 100)
    print(f"\n  $10,000 → ${equity:,.0f} out-of-sample ({total} months)")
    spy_equity = 10_000.0
    for r in rows:
        month_end = (date.fromisoformat(r[0]) + relativedelta(months=1)).isoformat()
        spy_equity *= (1 + _spy_return(r[0], month_end) / 100)
    print(f"  SPY buy-hold →  ${spy_equity:,.0f} same period")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start",   default="2021-01-01",
                        help="First validation month (needs 12mo train data before this)")
    parser.add_argument("--end",     default=None,
                        help="Last validation month start (default: current month)")
    parser.add_argument("--trials",  type=int, default=300,
                        help="Fuzzing trials per month (default 300)")
    parser.add_argument("--train-months", type=int, default=12,
                        help="Training window in months (default 12)")
    parser.add_argument("--workers", type=int, default=max(1, os.cpu_count() - 1))
    parser.add_argument("--exclude", nargs="+", default=[])
    parser.add_argument("--db",         default=None)
    parser.add_argument("--summary",    action="store_true",
                        help="Just print summary of existing results, no new runs")
    parser.add_argument("--no-factors", action="store_true",
                        help="Disable factor loading (baseline comparison)")
    parser.add_argument("--no-calmar", action="store_true",
                        help="Use raw geo-mean score instead of Calmar (stop-loss still active)")
    parser.add_argument("--optuna",     action="store_true",
                        help="Use Bayesian (TPE) search instead of random search")
    parser.add_argument("--fast",       action="store_true",
                        help="Single training window (3x faster, less overfit-resistant)")
    args = parser.parse_args()

    global DB_PATH, _factors, _use_calmar
    if args.db:
        DB_PATH = Path(args.db)
    _use_calmar = not args.no_calmar

    _init_db()

    if args.summary:
        _print_summary()
        return

    use_optuna  = args.optuna
    no_factors  = args.no_factors
    fast_mode   = args.fast

    val_start = date.fromisoformat(args.start)
    val_end   = date.fromisoformat(args.end) if args.end else date.today().replace(day=1)
    tm        = args.train_months

    # HoF setup — initialise the shared DB so it exists before workers start
    try:
        from stratscout.engine.data.params_hof import (
            init_hof, compute_month_features, find_similar_seeds, save_to_hof,
        )
        init_hof()
        hof_available = True
    except Exception:
        hof_available = False

    # Build month list with per-month features and HoF seeds (optuna path only)
    months = []
    m = val_start
    while m < val_end:
        m_end   = m + relativedelta(months=1)
        t_mid2  = m - relativedelta(months=tm // 3)
        t_mid1  = m - relativedelta(months=(tm * 2) // 3)
        t_start = m - relativedelta(months=tm)
        if use_optuna:
            seeds = []
            if hof_available:
                feats = compute_month_features(m.isoformat())
                seeds = find_similar_seeds(feats, k_similar=5, top_global=3)
            months.append((
                m.isoformat(), m_end.isoformat(),
                t_start.isoformat(), t_mid1.isoformat(), t_mid2.isoformat(),
                args.trials, args.exclude, fast_mode, args.workers, seeds,
            ))
        else:
            months.append((
                m.isoformat(), m_end.isoformat(),
                t_start.isoformat(), t_mid1.isoformat(), t_mid2.isoformat(),
                args.trials, args.exclude, fast_mode,
            ))
        m = m_end

    # Skip already-completed months
    con = sqlite3.connect(DB_PATH)
    done = {r[0] for r in con.execute("SELECT month FROM results").fetchall()}
    # Also load features for already-done months so we can save them to HoF
    done_rows = {
        r[0]: r for r in con.execute(
            "SELECT month, train_score, val_return, val_cagr, val_dd, val_trades, params "
            "FROM results"
        ).fetchall()
    }
    con.close()

    # Backfill HoF with any already-completed months not yet in HoF
    if hof_available:
        for month_str, row in done_rows.items():
            try:
                feats = compute_month_features(month_str)
                params = json.loads(row[6]) if row[6] else {}
                save_to_hof(
                    str(DB_PATH), month_str,
                    row[1], row[2], row[3], row[4], row[5],
                    params, feats,
                )
            except Exception:
                pass

    remaining = [m for m in months if m[0] not in done]

    mode_label = "Bayesian/TPE" if use_optuna else "Random"
    factor_label = "DISABLED (baseline)" if no_factors else "enabled"
    hof_label = "enabled" if hof_available else "unavailable"
    score_label = "Calmar (CAGR/DD)" if _use_calmar else "Raw geo-mean + DD penalty"
    print(f"\nWalk-Forward ETF Validator")
    print(f"  Validation months: {len(months)} total, {len(done)} done, {len(remaining)} remaining")
    print(f"  Training window:   {tm} months split into 3 sub-windows")
    print(f"  Trials per month:  {args.trials}  ({mode_label})")
    print(f"  Score objective:   {score_label}")
    print(f"  Workers:           {args.workers}")
    print(f"  Factors:           {factor_label}")
    print(f"  Hall of Fame:      {hof_label}")
    print(f"  DB:                {DB_PATH}")

    if not remaining:
        print("\nAll months already computed.")
        _print_summary()
        return

    t0 = time.perf_counter()
    run_fn = _run_month_optuna if use_optuna else _run_month

    # Optuna path: run months sequentially so each can use updated HoF seeds.
    # Parallelism is within each month (n_jobs=workers passed in args tuple).
    # Random path: parallel pool across months as before.
    if use_optuna:
        _worker_init(no_factors)
        prev_val_return: float | None = None  # tracks prior month's OOS result
        for i, month_args in enumerate(remaining):
            # Refresh seeds from HoF just before each month runs, including
            # prev_val_return so KNN finds months that recovered from similar outcomes
            if hof_available:
                month_str = month_args[0]
                feats = compute_month_features(month_str) or {}
                feats["prev_val_return"] = prev_val_return
                seeds = find_similar_seeds(feats, k_similar=5, top_global=3)
                month_args = month_args[:9] + (seeds,)
            result = run_fn(month_args)
            _save(result)
            # Write result to shared HoF immediately
            if hof_available and result.get("params"):
                try:
                    feats = compute_month_features(result["month"]) or {}
                    feats["prev_val_return"] = prev_val_return
                    params = json.loads(result["params"])
                    save_to_hof(
                        str(DB_PATH), result["month"],
                        result["train_score"], result["val_return"],
                        result.get("val_cagr", 0), result.get("val_dd", 0),
                        result.get("val_trades", 0), params, feats,
                        prev_val_return=prev_val_return,
                    )
                except Exception:
                    pass
            prev_val_return = result["val_return"]
            elapsed = time.perf_counter() - t0
            hit = "HIT " if result["positive"] else "miss"
            print(
                f"  [{i+1}/{len(remaining)}] {result['month']}  "
                f"train={result['train_score']:+.1f}  "
                f"val={result['val_return']:+.2f}%  "
                f"{hit}  ({elapsed/60:.1f}min)"
            )
    else:
        with mp.Pool(
            processes=args.workers,
            initializer=_worker_init,
            initargs=(no_factors,),
        ) as pool:
            for i, result in enumerate(pool.imap_unordered(run_fn, remaining)):
                _save(result)
                elapsed = time.perf_counter() - t0
                hit = "HIT " if result["positive"] else "miss"
                print(
                    f"  [{i+1}/{len(remaining)}] {result['month']}  "
                    f"train={result['train_score']:+.1f}  "
                    f"val={result['val_return']:+.2f}%  "
                    f"{hit}  ({elapsed/60:.1f}min)"
                )

    _print_summary()


if __name__ == "__main__":
    mp.freeze_support()
    try:
        from dateutil.relativedelta import relativedelta
    except ImportError:
        raise SystemExit("pip install python-dateutil")
    main()
