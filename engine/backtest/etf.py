"""
Parameterized ETF rotation backtest engine.

The strategy is a regime-based rotation:
  - Compare AGG 60-day return vs BIL → risk-on or risk-off
  - If risk-off: compare TLT 20-day return vs BIL → rising or falling rates

All thresholds, lookback windows, RSI windows, ETF pools, and position counts
are passed in a `params` dict so the fuzzer can vary them.
"""

from __future__ import annotations

import random
from decimal import Decimal
from typing import Any

import pandas as pd

from pathlib import Path

from stratscout.engine.backtest.core import (
    value_of_portfolio,
    rebalance_positions,
    compute_performance,
    BacktestError,
)
from stratscout.engine.settings import daily_dir as _daily_dir

DAILY_DIR = _daily_dir()


def load_local_histories(symbols: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    """Load daily candles from local feather files. Much faster than API calls."""
    histories = {}
    missing = []
    for sym in symbols:
        path = DAILY_DIR / f"{sym}.feather"
        if not path.exists():
            missing.append(sym)
            continue
        df = pd.read_feather(path)
        df["date"] = pd.to_datetime(df["date"], utc=True)
        df = df.sort_values("date")
        if start:
            df = df[df["date"] >= pd.Timestamp(start, tz="UTC")]
        if end:
            df = df[df["date"] <= pd.Timestamp(end, tz="UTC")]
        df = df.set_index("date")
        histories[sym] = df
    if missing:
        raise BacktestError(f"Missing local data for: {', '.join(missing)}. Run download_data.py first.")
    return histories
from stratscout.engine.data.universes import (
    ANCHORS,
    RISK_ON_POOL,
    RISK_OFF_RISING_POOL,
    RISK_OFF_FALLING_POOL,
    ALL_SYMBOLS,
    MIN_RISK_ON,
    MIN_RISK_OFF_RISING,
    MIN_RISK_OFF_FALLING,
    SECTOR_BUCKETS,
)


# ── Default / baseline params ─────────────────────────────────────────────────

DEFAULT_PARAMS: dict[str, Any] = {
    # Regime detection lookbacks
    "agg_bil_lookback": 60,
    "tlt_bil_lookback": 20,
    # RSI windows
    "risk_on_rsi_window": 10,
    "risk_off_rsi_window": 20,
    # RSI selection direction: "lowest" picks oversold (current behavior), "highest" picks momentum
    "risk_on_rsi_direction": "lowest",
    "risk_off_rsi_direction": "lowest",
    # How many positions to hold in each regime
    "n_risk_on": 2,
    "n_risk_off_rising": 2,       # includes UUP slot — total positions in rising-rate regime
    "n_risk_off_falling": 4,
    # ETF pools (subsets of the full pools defined in etf_universe.py)
    "risk_on_pool": ["SOXL", "TQQQ", "UPRO", "TECL"],
    "risk_off_rising_pool": ["QID", "TBF"],
    "risk_off_falling_pool": ["UGL", "TMF", "BTAL", "XLP"],
    # Optional: add UUP as a fixed anchor in rising-rate regime
    "rising_rate_include_uup": True,
    # Minimum trading days to hold before rebalancing again
    "min_hold_days": 3,
    # Inverse-volatility position sizing: window in days (0 = equal weight)
    "vol_weight_window": 20,
    # Volume surge signal: blend fraction (0=pure RSI, 1=pure volume surge)
    "vol_score_weight": 0.0,
    # Lookback window (days) for average volume baseline
    "vol_score_window": 20,
    # Cap on volume surge ratio before scoring (prevents single-day spikes dominating)
    "vol_surge_cap": 3.0,
    # EMA cross signal: blend fraction (0=pure RSI, 1=pure EMA cross, in-between=blend)
    # When ema_weight > 0, asset scores = (1-vsw-emaw)*RSI + vsw*vol + emaw*EMA_cross
    "ema_weight": 0.0,
    # Fast EMA window in days (must be < ema_slow)
    "ema_fast": 10,
    # Slow EMA window in days
    "ema_slow": 40,
    # Enforce at most one pick per sector bucket (prevents SOXL+TECL double-up)
    "sector_diverse": False,
    # Combo weighting: momentum × (1/vol), normalized across selected symbols.
    # combo_alpha=0 → pure inv-vol, 1 → pure momentum, 0.5 → equal blend.
    # combo_max_weight caps any single position (1.0 = no cap).
    # When combo_alpha is None the old vol_weight_window path is used.
    "combo_momentum_lookback": 21,
    "combo_vol_lookback":      21,
    "combo_alpha":             0.5,
    "combo_max_weight":        1.0,
    # Exit to cash if portfolio drops this % from most recent entry (0 = disabled)
    "stop_loss_pct":           0.0,
    # Trading days to stay in cash after a stop-out (~22 = 1 calendar month)
    "stop_loss_lockout_days":  22,
    # Volatility targeting: scale exposure so realized vol ≈ this % annualized.
    # 0 = disabled. Typical values 8–15. Replaces stop-loss when > 0.
    "vol_target_pct":          0.0,
    # Lookback window (trading days) for realized vol computation
    "vol_target_lookback":     21,
}


# ── Random param generation ───────────────────────────────────────────────────

def random_params(exclude: list[str] | None = None) -> dict[str, Any]:
    """Generate a randomized parameter set biased toward what the meta-analysis found works.

    Key findings from 43k-run analysis:
    - agg_bil_lookback ~90 (long), tlt_bil_lookback ~10 (short)
    - risk_on_rsi_window ~24, vol_weight_window ~0, min_hold_days ~7
    - SOXL in 100% of top-500 pools; SOXL+TECL pairing works well with sector_diverse=False
    - sector_diverse forced False so SOXL+TECL is a valid 2-pick combination
    """
    excl = set(exclude or [])
    on_universe      = [s for s in RISK_ON_POOL        if s not in excl]
    rising_universe  = [s for s in RISK_OFF_RISING_POOL  if s not in excl]
    falling_universe = [s for s in RISK_OFF_FALLING_POOL if s not in excl]

    n_risk_on = random.randint(2, 3)
    # SOXL anchors unless excluded; fall back to random anchor
    anchor = "SOXL" if "SOXL" in on_universe else (on_universe[0] if on_universe else None)
    other_on = [s for s in on_universe if s != anchor]
    extra = random.randint(n_risk_on - 1, min(len(other_on), n_risk_on + 2))
    on_pool = ([anchor] if anchor else []) + random.sample(other_on, min(extra, len(other_on)))

    n_rising = random.randint(MIN_RISK_OFF_RISING, min(3, len(rising_universe)))
    rising_pool = random.sample(rising_universe, random.randint(n_rising, len(rising_universe)))

    n_falling = random.randint(MIN_RISK_OFF_FALLING, min(5, len(falling_universe)))
    falling_pool = random.sample(falling_universe, random.randint(n_falling, min(len(falling_universe), n_falling + 2)))

    include_uup = random.random() > 0.3
    total_rising = n_rising + (1 if include_uup else 0)

    return {
        "agg_bil_lookback": random.randint(70, 110),
        "tlt_bil_lookback": random.randint(5, 20),
        "risk_on_rsi_window": random.randint(15, 30),
        "risk_off_rsi_window": random.randint(5, 25),
        "risk_on_rsi_direction": random.choice(["lowest", "highest"]),
        "risk_off_rsi_direction": random.choice(["lowest", "highest"]),
        "n_risk_on": n_risk_on,
        "n_risk_off_rising": total_rising,
        "n_risk_off_falling": n_falling,
        "risk_on_pool": on_pool,
        "risk_off_rising_pool": rising_pool,
        "risk_off_falling_pool": falling_pool,
        "rising_rate_include_uup": include_uup,
        "min_hold_days": random.randint(4, 12),
        "vol_weight_window": random.choice([0, 0, 0, 0, 5, 10]),  # strongly bias toward equal weight
        "vol_score_weight": random.choice([0.0, 0.0, 0.0, 0.1, 0.2]),
        "vol_score_window": random.choice([10, 15, 20, 30]),
        "vol_surge_cap": random.choice([2.0, 3.0, 4.0, 5.0]),
        # EMA cross: strongly bias toward 0 so most runs stay RSI-only (comparable to old results)
        # Non-zero runs explore the EMA blend territory
        "ema_weight": random.choice([0.0, 0.0, 0.0, 0.0, 0.1, 0.2, 0.3, 0.5]),
        "ema_fast":   random.choice([5, 8, 10, 12, 15, 20]),
        "ema_slow":   random.choice([20, 30, 40, 50, 60, 80, 100]),
        "sector_diverse": False,  # off — allows SOXL+TECL pairing which outperforms
        # Combo weighting
        "combo_momentum_lookback": random.choice([5, 10, 21, 42, 63]),
        "combo_vol_lookback":      random.choice([5, 10, 21, 42]),
        "combo_alpha":             round(random.uniform(0.0, 1.0), 2),
        "combo_max_weight":        round(random.uniform(0.3, 1.0), 2),
        # Stop-loss always active (floored at 4% so it's always meaningful)
        "stop_loss_pct":           round(random.uniform(4.0, 18.0), 1),
        "stop_loss_lockout_days":  random.choice([15, 18, 22, 25, 30]),
        # Vol targeting disabled by default in random search; Optuna tunes it
        "vol_target_pct":          0.0,
        "vol_target_lookback":     21,
    }


def refine_params(base: dict[str, Any], strength: float = 0.25, exclude: list[str] | None = None) -> dict[str, Any]:
    """Mutate a known-good param set slightly. strength in [0,1]."""
    excl = set(exclude or [])
    p = base.copy()
    # Strip excluded symbols from all pools before mutating
    for pool_key in ("risk_on_pool", "risk_off_rising_pool", "risk_off_falling_pool"):
        p[pool_key] = [s for s in p.get(pool_key, []) if s not in excl]

    def jitter_int(val: int, lo: int, hi: int, scale: float = 0.3) -> int:
        spread = max(1, int((hi - lo) * scale * strength))
        return max(lo, min(hi, val + random.randint(-spread, spread)))

    p["agg_bil_lookback"] = jitter_int(p["agg_bil_lookback"], 70, 110)
    p["tlt_bil_lookback"] = jitter_int(p["tlt_bil_lookback"], 5, 20)
    p["risk_on_rsi_window"] = jitter_int(p["risk_on_rsi_window"], 15, 30)
    p["risk_off_rsi_window"] = jitter_int(p["risk_off_rsi_window"], 5, 25)
    p["min_hold_days"] = jitter_int(p.get("min_hold_days", 7), 4, 12)
    p["sector_diverse"] = False  # off — allows SOXL+TECL pairing which outperforms

    # Combo weighting params
    if random.random() < 0.3 * strength:
        p["combo_momentum_lookback"] = random.choice([5, 10, 21, 42, 63])
    if random.random() < 0.3 * strength:
        p["combo_vol_lookback"] = random.choice([5, 10, 21, 42])
    alpha = p.get("combo_alpha", 0.5)
    p["combo_alpha"] = round(max(0.0, min(1.0, alpha + random.uniform(-0.2, 0.2) * strength)), 2)
    mw = p.get("combo_max_weight", 1.0)
    p["combo_max_weight"] = round(max(0.3, min(1.0, mw + random.uniform(-0.15, 0.15) * strength)), 2)
    slp = p.get("stop_loss_pct", 8.0)
    slp = max(4.0, min(20.0, slp + random.uniform(-3.0, 3.0) * strength))
    p["stop_loss_pct"] = round(slp, 1)
    sll = p.get("stop_loss_lockout_days", 22)
    if random.random() < 0.3 * strength:
        sll = random.choice([15, 18, 22, 25, 30])
    p["stop_loss_lockout_days"] = sll
    vtp = p.get("vol_target_pct", 0.0)
    if vtp > 0:
        vtp = round(max(5.0, min(20.0, vtp + random.uniform(-2.0, 2.0) * strength)), 1)
        p["vol_target_pct"] = vtp
    vtl = p.get("vol_target_lookback", 21)
    if random.random() < 0.2 * strength:
        vtl = random.choice([10, 15, 21, 30])
    p["vol_target_lookback"] = vtl

    # Occasionally flip vol-weighting on/off or jitter the window
    vww = p.get("vol_weight_window", 0)
    if random.random() < 0.2 * strength:
        vww = 0 if vww > 0 else random.choice([10, 15, 20, 30])
    elif vww > 0:
        vww = jitter_int(vww, 5, 40)
    p["vol_weight_window"] = vww

    # Volume surge signal
    vsw = p.get("vol_score_weight", 0.0)
    if random.random() < 0.2 * strength:
        vsw = 0.0 if vsw > 0 else random.choice([0.1, 0.2, 0.3, 0.5])
    elif vsw > 0:
        vsw = max(0.0, min(1.0, vsw + random.uniform(-0.15, 0.15) * strength))
    p["vol_score_weight"] = round(vsw, 2)
    if random.random() < 0.2 * strength:
        p["vol_score_window"] = random.choice([5, 10, 15, 20, 30])
    if random.random() < 0.2 * strength:
        p["vol_surge_cap"] = random.choice([1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.0, 10.0])

    # EMA cross signal — jitter weight and windows
    emaw = p.get("ema_weight", 0.0)
    if random.random() < 0.2 * strength:
        emaw = 0.0 if emaw > 0 else random.choice([0.1, 0.2, 0.3, 0.5])
    elif emaw > 0:
        emaw = max(0.0, min(1.0, emaw + random.uniform(-0.1, 0.1) * strength))
    p["ema_weight"] = round(emaw, 2)
    # Always ensure fast/slow are set when ema_weight > 0 (guards against old param sets)
    if emaw > 0:
        if p.get("ema_fast") is None:
            p["ema_fast"] = random.choice([5, 8, 10, 12, 15, 20])
        if p.get("ema_slow") is None:
            p["ema_slow"] = random.choice([20, 30, 40, 50, 60, 80, 100])
        if random.random() < 0.3 * strength:
            p["ema_fast"] = random.choice([5, 8, 10, 12, 15, 20])
            p["ema_slow"] = random.choice([20, 30, 40, 50, 60, 80, 100])
    # Ensure fast < slow
    if p.get("ema_fast", 10) >= p.get("ema_slow", 40):
        p["ema_slow"] = p["ema_fast"] * random.randint(3, 6)

    # Occasionally flip direction
    if random.random() < 0.15 * strength:
        p["risk_on_rsi_direction"] = "highest" if p["risk_on_rsi_direction"] == "lowest" else "lowest"
    if random.random() < 0.15 * strength:
        p["risk_off_rsi_direction"] = "highest" if p["risk_off_rsi_direction"] == "lowest" else "lowest"

    # Occasionally swap one ETF in each pool
    for pool_key, full_pool, min_size in [
        ("risk_on_pool", RISK_ON_POOL, MIN_RISK_ON),
        ("risk_off_rising_pool", RISK_OFF_RISING_POOL, MIN_RISK_OFF_RISING),
        ("risk_off_falling_pool", RISK_OFF_FALLING_POOL, MIN_RISK_OFF_FALLING),
    ]:
        if random.random() < 0.4 * strength:
            current = list(p[pool_key])
            outside = [x for x in full_pool if x not in current and x not in excl]
            if outside and len(current) > min_size and random.random() > 0.5:
                # remove one
                current.pop(random.randrange(len(current)))
            elif outside:
                # add one
                current.append(random.choice(outside))
            p[pool_key] = current

    # Clamp n-values so they never exceed their pool size
    p["n_risk_on"] = max(MIN_RISK_ON, min(p["n_risk_on"], len(p["risk_on_pool"])))
    uup_slot = 1 if p.get("rising_rate_include_uup") else 0
    p["n_risk_off_rising"] = max(MIN_RISK_OFF_RISING, min(p["n_risk_off_rising"], len(p["risk_off_rising_pool"]) + uup_slot))
    p["n_risk_off_falling"] = max(MIN_RISK_OFF_FALLING, min(p["n_risk_off_falling"], len(p["risk_off_falling_pool"])))

    return p


# ── Volatility-scaled position sizing ────────────────────────────────────────

def compute_vol_weights(histories: dict, symbols: list, as_of, window: int = 20) -> dict:
    """Inverse-volatility weights so high-vol ETFs get smaller allocations."""
    inv_vols = {}
    for sym in symbols:
        if sym not in histories:
            inv_vols[sym] = 1.0
            continue
        closes = histories[sym]["close"]
        hist = closes.loc[:as_of]
        if len(hist) < window + 2:
            inv_vols[sym] = 1.0
            continue
        vol = hist.iloc[-(window+1):].pct_change().dropna().std()
        inv_vols[sym] = 1.0 / vol if vol > 1e-8 else 1.0
    total = sum(inv_vols.values()) or 1
    return {sym: v / total for sym, v in inv_vols.items()}


# ── Combo position sizing: momentum × (1/vol) ────────────────────────────────

def compute_combo_weights(
    histories: dict,
    symbols: list[str],
    as_of,
    momentum_lookback: int = 21,
    vol_lookback: int = 21,
    alpha: float = 0.5,
    max_weight: float = 1.0,
) -> dict[str, float]:
    """
    Weight each symbol by: alpha * momentum_norm + (1-alpha) * inv_vol_norm,
    normalized to sum to 1. max_weight caps any single position (iterative redistribution).

    alpha=0 → pure inverse-volatility (low-vol gets most capital)
    alpha=1 → pure momentum (highest recent return gets most capital)
    alpha=0.5 → classic minimum-variance-meets-momentum blend
    """
    raw_mom, raw_invvol = {}, {}
    for sym in symbols:
        if sym not in histories:
            raw_mom[sym] = 0.0
            raw_invvol[sym] = 1.0
            continue
        close = histories[sym]["close"].loc[:as_of]
        # Momentum: total return over lookback
        if len(close) >= momentum_lookback + 1:
            raw_mom[sym] = float(close.iloc[-1] / close.iloc[-momentum_lookback - 1] - 1)
        else:
            raw_mom[sym] = 0.0
        # Inverse volatility
        if len(close) >= vol_lookback + 2:
            vol = close.iloc[-(vol_lookback + 1):].pct_change().dropna().std()
            raw_invvol[sym] = 1.0 / vol if vol > 1e-8 else 1.0
        else:
            raw_invvol[sym] = 1.0

    # Normalize each component to [0, 1] across the pool
    def _minmax_norm(d: dict) -> dict:
        lo, hi = min(d.values()), max(d.values())
        rng = hi - lo
        if rng < 1e-10:
            return {k: 0.5 for k in d}
        return {k: (v - lo) / rng for k, v in d.items()}

    mom_n    = _minmax_norm(raw_mom)
    invvol_n = _minmax_norm(raw_invvol)

    combo = {sym: alpha * mom_n[sym] + (1 - alpha) * invvol_n[sym] for sym in symbols}

    # Ensure non-negative (can be 0 if all scores identical)
    combo = {sym: max(v, 1e-6) for sym, v in combo.items()}

    # Normalize to sum to 1
    total = sum(combo.values())
    weights = {sym: v / total for sym, v in combo.items()}

    # Cap max weight and redistribute excess iteratively
    if max_weight < 1.0:
        for _ in range(20):
            excess = sum(max(0, w - max_weight) for w in weights.values())
            if excess < 1e-8:
                break
            under = {sym: w for sym, w in weights.items() if w < max_weight}
            under_total = sum(under.values())
            capped = {sym: min(w, max_weight) for sym, w in weights.items()}
            if under_total > 0:
                for sym in under:
                    capped[sym] += excess * (under[sym] / under_total)
            total = sum(capped.values())
            weights = {sym: v / total for sym, v in capped.items()}

    return weights


# ── Volume surge scoring ─────────────────────────────────────────────────────

def _vol_surge_score(histories: dict, sym: str, as_of, window: int = 20, cap: float = 3.0) -> float:
    """Return current_volume / avg_volume, capped at `cap`. 1.0 = average."""
    if sym not in histories or "volume" not in histories[sym].columns:
        return 1.0
    vol_series = histories[sym]["volume"].loc[:as_of]
    if len(vol_series) < window + 1:
        return 1.0
    recent = float(vol_series.iloc[-1])
    avg = float(vol_series.iloc[-(window+1):-1].mean())
    if avg < 1:
        return 1.0
    return min(recent / avg, cap)


# ── RSI computation (no TA-Lib dependency) ───────────────────────────────────

def _rolling_rsi(close: pd.Series, window: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window, min_periods=window).mean()
    loss = (-delta).clip(lower=0).rolling(window, min_periods=window).mean()
    rs = gain / loss.replace(0, float("nan"))
    return (100 - 100 / (1 + rs)).fillna(50)


# ── EMA cross signal ─────────────────────────────────────────────────────────

def _ema_cross_score(close: pd.Series, as_of, fast: int, slow: int) -> float:
    """
    EMA cross signal strength: (fast_ema - slow_ema) / slow_ema, as of a given date.

    Returns a value in roughly (-1, +1):
      > 0  → fast above slow (bullish momentum)
      < 0  → fast below slow (bearish momentum)
      0    → at crossover

    Normalized to [0, 1] via sigmoid so it can be blended with RSI rank.
    Higher = stronger bullish signal = better pick when direction='highest'.
    """
    hist = close.loc[:as_of]
    if len(hist) < slow + 1:
        return 0.5  # neutral when insufficient history
    fast_ema = float(hist.ewm(span=fast, adjust=False).mean().iloc[-1])
    slow_ema = float(hist.ewm(span=slow, adjust=False).mean().iloc[-1])
    if slow_ema == 0:
        return 0.5
    raw = (fast_ema - slow_ema) / slow_ema  # typically -0.1 to +0.1
    # Sigmoid to map to [0, 1]; scale by 50 so ±2% cross = ±0.73 normalized
    import math
    return 1.0 / (1.0 + math.exp(-50 * raw))


def _cumret(close: pd.Series, lookback: int, as_of) -> float:
    idx = close.index.get_loc(as_of)
    if idx < lookback:
        raise BacktestError(f"Not enough history for {lookback}-day return")
    return float(close.iloc[idx] / close.iloc[idx - lookback] - 1)


# ── Parameterized regime selection ───────────────────────────────────────────

def precompute_rsi_cache(histories: dict[str, pd.DataFrame], params: dict) -> dict:
    """Compute RSI series once for all symbols and windows needed by this param set."""
    cache = {}
    for window, syms in [
        (params["risk_on_rsi_window"],  params["risk_on_pool"]),
        (params["risk_off_rsi_window"], params["risk_off_rising_pool"]),
    ]:
        for sym in syms:
            if sym in histories and (sym, window) not in cache:
                cache[(sym, window)] = _rolling_rsi(histories[sym]["close"], window)
    return cache


def choose_targets(histories: dict[str, pd.DataFrame], as_of, params: dict,
                   rsi_cache: dict | None = None) -> list[str]:
    agg = histories["AGG"]["close"]
    bil = histories["BIL"]["close"]
    tlt = histories["TLT"]["close"]

    abl = params["agg_bil_lookback"]
    tbl = params["tlt_bil_lookback"]

    def _rsi_at(sym: str, window: int) -> float:
        if rsi_cache is not None and (sym, window) in rsi_cache:
            s = rsi_cache[(sym, window)]
        else:
            s = _rolling_rsi(histories[sym]["close"], window)
        return float(s.loc[:as_of].iloc[-1])

    vsw   = params.get("vol_score_weight", 0.0)
    vswin = params.get("vol_score_window", 20)
    vscap = params.get("vol_surge_cap", 3.0)

    # EMA cross params — ema_weight=0 means pure RSI (backward compatible)
    emaw  = params.get("ema_weight", 0.0)
    ema_fast = params.get("ema_fast", 10)
    ema_slow = params.get("ema_slow", 40)

    def _blend_score(sym: str, rsi_val: float, n_pool: int, direction: str) -> float:
        """
        Composite score (lower = better pick) blending up to three signals:
          - RSI rank           (always active, weight = 1 - vsw - emaw)
          - Volume surge       (active when vol_score_weight > 0)
          - EMA cross strength (active when ema_weight > 0)

        All three are normalized to [0, 1] before blending.
        Higher normalized value = stronger bullish signal = preferred when
        direction='highest'. Score is inverted so sorted ascending = best first.
        """
        # RSI: normalize to [0,1], flip if direction='lowest'
        rsi_norm = rsi_val / 100.0
        if direction == "lowest":
            rsi_norm = 1.0 - rsi_norm

        # Volume surge: high surge = preferred (lower score = picked first)
        surge = _vol_surge_score(histories, sym, as_of, vswin, vscap)
        vol_norm = 1.0 / surge  # invert: high surge → low vol_norm → prefer

        # EMA cross: [0,1], >0.5 = fast above slow (bullish)
        ema_norm = _ema_cross_score(histories[sym]["close"], as_of, ema_fast, ema_slow) \
                   if (emaw > 0 and sym in histories) else 0.5

        rsi_w = max(0.0, 1.0 - vsw - emaw)
        # Combined signal (higher = more bullish = preferred)
        signal = rsi_w * rsi_norm + vsw * (1.0 - vol_norm) + emaw * ema_norm
        return 1.0 - signal  # invert so sorted ascending picks best

    def _sector_filter(ranked_pairs: list, n: int) -> list[str]:
        """Pick top-n from a ranked list, skipping same-sector duplicates when sector_diverse is set."""
        if not params.get("sector_diverse", False):
            return [sym for sym, _ in ranked_pairs[:n]]
        seen: set[str] = set()
        picks: list[str] = []
        for sym, _ in ranked_pairs:
            bucket = SECTOR_BUCKETS.get(sym, sym)
            if bucket not in seen:
                picks.append(sym)
                seen.add(bucket)
            if len(picks) == n:
                break
        return picks

    if _cumret(agg, abl, as_of) > _cumret(bil, abl, as_of):
        pool = params["risk_on_pool"]
        n = min(params["n_risk_on"], len(pool))
        window = params["risk_on_rsi_window"]
        rsi_vals = {sym: _rsi_at(sym, window) for sym in pool if sym in histories}
        direction = params.get("risk_on_rsi_direction", "lowest")
        if vsw > 0 or emaw > 0:
            scored = {sym: _blend_score(sym, rv, len(pool), direction) for sym, rv in rsi_vals.items()}
            ranked = sorted(scored.items(), key=lambda kv: kv[1])
        else:
            ranked = sorted(rsi_vals.items(), key=lambda kv: kv[1], reverse=(direction == "highest"))
        return _sector_filter(ranked, n)

    if _cumret(tlt, tbl, as_of) < _cumret(bil, tbl, as_of):
        pool = params["risk_off_rising_pool"]
        n_from_pool = max(1, params["n_risk_off_rising"] - (1 if params.get("rising_rate_include_uup") else 0))
        n_from_pool = min(n_from_pool, len(pool))
        window = params["risk_off_rsi_window"]
        rsi_vals = {sym: _rsi_at(sym, window) for sym in pool if sym in histories}
        direction = params.get("risk_off_rsi_direction", "lowest")
        if vsw > 0 or emaw > 0:
            scored = {sym: _blend_score(sym, rv, len(pool), direction) for sym, rv in rsi_vals.items()}
            ranked = sorted(scored.items(), key=lambda kv: kv[1])
        else:
            ranked = sorted(rsi_vals.items(), key=lambda kv: kv[1], reverse=(direction == "highest"))
        picks = _sector_filter(ranked, n_from_pool)
        if params.get("rising_rate_include_uup") and "UUP" in histories:
            picks = ["UUP"] + picks
        return picks

    pool = params["risk_off_falling_pool"]
    n = min(params["n_risk_off_falling"], len(pool))
    return pool[:n]


# ── Buy-and-hold benchmark ────────────────────────────────────────────────────

def _buy_and_hold_nav(
    histories: dict[str, pd.DataFrame],
    symbols: list[str],
    cash: float,
    index: pd.Index,
) -> pd.Series:
    """Equal-weight buy-and-hold the given symbols over the strategy's date range."""
    if not symbols:
        return pd.Series(dtype=float)
    valid = [s for s in symbols if s in histories]
    if not valid:
        return pd.Series(dtype=float)

    # Align all close series to the strategy's date index
    closes = {}
    for sym in valid:
        s = histories[sym]["close"].reindex(index, method="ffill")
        closes[sym] = s

    df = pd.DataFrame(closes)
    # Normalize each to 1.0 at the first non-NaN row, then average
    first_valid = df.apply(lambda col: col.first_valid_index())
    start_idx = max(first_valid)  # wait until all symbols have data
    df = df.loc[start_idx:]
    normed = df / df.iloc[0]
    equal_weight = normed.mean(axis=1)
    return (equal_weight * cash).rename("buy_and_hold")


# ── Main backtest runner ─────────────────────────────────────────────────────

def run_etf_backtest(
    params: dict[str, Any],
    start: str,
    end: str,
    cash: float = 100_000.0,
    massive_key: str | None = None,
    polygon_key: str | None = None,
) -> dict[str, Any]:
    """
    Run a single backtest with the given params.
    Loads from local feather files (data/daily/) — no API calls.
    """
    needed = set(ANCHORS)
    needed.update(params["risk_on_pool"])
    needed.update(params["risk_off_rising_pool"])
    needed.update(params["risk_off_falling_pool"])
    if params.get("rising_rate_include_uup"):
        needed.add("UUP")
    symbols = sorted(needed)

    warmup = max(params["agg_bil_lookback"], params["tlt_bil_lookback"],
                 params["risk_on_rsi_window"], params["risk_off_rsi_window"]) + 5

    # Load extra history before start to prime lookbacks, so day 1 of the
    # requested window is already tradeable
    pre_start = (pd.Timestamp(start) - pd.Timedelta(days=warmup * 2)).strftime("%Y-%m-%d")
    histories = load_local_histories(symbols, pre_start, end)

    # Build common dates from anchor intersection, then split into warmup vs live
    anchor_indexes = [set(histories[a].index) for a in ANCHORS if a in histories]
    all_common = sorted(set.intersection(*anchor_indexes))
    start_ts = pd.Timestamp(start, tz="UTC")
    # Find the index of the first date on or after start
    live_from = next((i for i, d in enumerate(all_common) if d >= start_ts), None)
    if live_from is None or live_from < warmup:
        live_from = warmup  # fall back if not enough pre-history
    common = all_common

    positions: dict[str, int] = {sym: 0 for sym in symbols}
    portfolio_cash = Decimal(str(cash))
    nav: list[dict] = []
    trade_history: list[dict] = []
    first_live_day = None
    bnh_symbols: list[str] = []
    min_hold = params.get("min_hold_days", 1)
    last_rebalance_idx = -min_hold
    rsi_cache = precompute_rsi_cache(histories, params)
    stop_loss_pct = params.get("stop_loss_pct", 0.0)
    # After a stop-out, lock re-entry for this many trading days (~1 calendar month).
    # Prevents whipsaw re-entry within the same bad month.
    stop_loss_lockout = int(params.get("stop_loss_lockout_days", 22))
    entry_value: float | None = None  # portfolio value at most recent position entry
    vol_target_pct = params.get("vol_target_pct", 0.0)
    vol_target_lookback = int(params.get("vol_target_lookback", 21))
    nav_for_vol: list[float] = []  # rolling NAV for realized vol computation

    for idx in range(1, len(common)):
        today = common[idx]
        yesterday = common[idx - 1]
        port_value = float(value_of_portfolio(histories, positions, portfolio_cash, today))

        # Skip days before the requested start window
        if idx < live_from:
            continue

        # ── Intramonth stop-loss ─────────────────────────────────────────────
        # If portfolio drops stop_loss_pct% from entry, liquidate to cash and
        # lock out re-entry for stop_loss_lockout_days trading days so the
        # strategy doesn't whipsaw back in during the same bad month.
        if (stop_loss_pct > 0 and entry_value is not None
                and port_value < entry_value * (1.0 - stop_loss_pct / 100.0)):
            # Use the existing rebalance_positions with empty targets to liquidate
            # cleanly through the same code path as normal rebalances.
            positions, portfolio_cash, sl_trades = rebalance_positions(
                histories, positions, portfolio_cash, [], today, weights=None
            )
            trade_history.extend(sl_trades)
            entry_value = None
            last_rebalance_idx = idx + stop_loss_lockout  # lock out for ~1 month

        nav_for_vol.append(port_value)

        try:
            targets = choose_targets(histories, yesterday, params, rsi_cache)
            if first_live_day is None:
                first_live_day = today
                bnh_symbols = targets[:]
            current_held = {sym for sym, qty in positions.items() if qty > 0}
            held_long_enough = (idx - last_rebalance_idx) >= min_hold
            if set(targets) != current_held and held_long_enough:
                combo_alpha = params.get("combo_alpha")
                if combo_alpha is not None:
                    weights = compute_combo_weights(
                        histories, targets, yesterday,
                        momentum_lookback=params.get("combo_momentum_lookback", 21),
                        vol_lookback=params.get("combo_vol_lookback", 21),
                        alpha=combo_alpha,
                        max_weight=params.get("combo_max_weight", 1.0),
                    )
                else:
                    vww = params.get("vol_weight_window", 0)
                    weights = compute_vol_weights(histories, targets, yesterday, vww) if vww > 0 else None

                # Volatility targeting: scale weights so realized portfolio vol ≈ target
                if vol_target_pct > 0 and len(nav_for_vol) >= vol_target_lookback + 1:
                    import math as _math
                    nav_window = nav_for_vol[-(vol_target_lookback + 1):]
                    daily_rets = [nav_window[i] / nav_window[i-1] - 1 for i in range(1, len(nav_window))]
                    mean_r = sum(daily_rets) / len(daily_rets)
                    variance = sum((r - mean_r) ** 2 for r in daily_rets) / len(daily_rets)
                    realized_vol = _math.sqrt(variance * 252) * 100  # annualized %
                    if realized_vol > 0.1:
                        vol_scale = min(1.0, vol_target_pct / realized_vol)
                        # Apply scale: reduce all weights, remainder stays cash
                        if weights:
                            weights = {k: v * vol_scale for k, v in weights.items()}
                        else:
                            eq = 1.0 / len(targets) if targets else 1.0
                            weights = {s: eq * vol_scale for s in targets}

                positions, portfolio_cash, trades = rebalance_positions(
                    histories, positions, portfolio_cash, targets, today, weights=weights
                )
                trade_history.extend(trades)
                last_rebalance_idx = idx
                # Record entry value for stop-loss tracking
                entry_value = float(value_of_portfolio(histories, positions, portfolio_cash, today))
        except (BacktestError, KeyError, IndexError):
            pass

        port_value = float(value_of_portfolio(histories, positions, portfolio_cash, today))
        nav.append({"date": today, "value": port_value})

    if not nav:
        raise BacktestError("Backtest produced no data — check that anchor symbols (AGG, BIL, TLT) have data in the requested date range.")
    nav_df = pd.DataFrame(nav).set_index("date")
    perf = compute_performance(nav_df["value"])
    trade_df = pd.DataFrame(trade_history) if trade_history else pd.DataFrame()

    # ── Buy-and-hold benchmark (aligned to first trade date) ─────────────────
    live_index = nav_df.index[nav_df.index >= first_live_day] if first_live_day else nav_df.index
    bnh_series = _buy_and_hold_nav(histories, bnh_symbols, cash, live_index)

    return {
        "params": params,
        "nav_df": nav_df,
        "first_live_day": first_live_day,
        "bnh_df": bnh_series,
        "bnh_symbols": bnh_symbols,
        "trade_df": trade_df,
        "perf": perf,
        "score": perf.get("total_return_pct", -999.0),
        "symbols_used": symbols,
    }
