"""
Evolvable strategy DSL for genetic programming.

A strategy is a plain Python dict (JSON-serializable) containing a list of
rules evaluated top-to-bottom. The first rule whose condition is True fires
its action; if none match, the default_action fires.

Grammar:
  strategy       = {"rules": [rule, ...], "default_action": action}
  rule           = {"condition": condition, "action": action}
  condition      = {"lhs": indicator, "op": ">"|"<"|">="|"<=", "rhs": indicator}
  indicator      = {"indicator": "cumret",    "symbol": str, "lookback": int}
                 | {"indicator": "rsi",        "symbol": str, "window": int}
                 | {"indicator": "vol_surge",  "symbol": str, "window": int}
                 | {"indicator": "ema_cross",  "symbol": str, "fast": int, "slow": int}
                 | {"indicator": "volatility", "symbol": str, "window": int}
                 | {"indicator": "const",      "value": float}
  action         = {"pool": [sym, ...], "n": int, "signal": str, "signal_window": int,
                    "ema_fast": int, "ema_slow": int}
  signal         = "rsi_lowest" | "rsi_highest" | "momentum" | "vol_surge" | "ema_cross"
"""
from __future__ import annotations

import copy
import random
from typing import Any

from stratscout.engine.data.universes import (
    ANCHORS, RISK_ON_POOL, RISK_OFF_RISING_POOL, RISK_OFF_FALLING_POOL,
    MIN_RISK_ON, MIN_RISK_OFF_RISING, MIN_RISK_OFF_FALLING,
)

_CONDITION_SYMBOLS: list[str] = ANCHORS + RISK_ON_POOL + RISK_OFF_RISING_POOL + RISK_OFF_FALLING_POOL

_POOL_DEFS: dict[str, tuple[list[str], int]] = {
    "risk_on":          (RISK_ON_POOL,          MIN_RISK_ON),
    "risk_off_rising":  (RISK_OFF_RISING_POOL,  MIN_RISK_OFF_RISING),
    "risk_off_falling": (RISK_OFF_FALLING_POOL, MIN_RISK_OFF_FALLING),
}

_SIGNALS = ["rsi_lowest", "rsi_highest", "momentum", "vol_surge", "ema_cross"]

_CUMRET_LOOKBACK = (5, 120)
_RSI_WINDOW      = (5, 30)
_VOL_WINDOW      = (5, 30)
_EMA_FAST        = [5, 8, 10, 12, 15, 20]
_EMA_SLOW        = [20, 30, 40, 50, 60, 80, 100]
_CUMRET_CONST    = (-0.10, 0.10)
_RSI_CONST       = (20.0, 80.0)


# ── Indicator evaluation ──────────────────────────────────────────────────────

def _eval_indicator(ind: dict, histories: dict, as_of) -> float:
    try:
        kind = ind["indicator"]
        if kind == "const":
            return float(ind["value"])

        sym = ind.get("symbol", "")
        if sym not in histories:
            return 0.0
        close = histories[sym]["close"]

        if kind == "cumret":
            lb = int(ind["lookback"])
            hist = close.loc[:as_of]
            if len(hist) <= lb:
                return 0.0
            return float(hist.iloc[-1] / hist.iloc[-1 - lb] - 1)

        if kind == "rsi":
            from stratscout.engine.backtest.etf import _rolling_rsi
            rsi_s = _rolling_rsi(close, int(ind["window"]))
            vals = rsi_s.loc[:as_of]
            return float(vals.iloc[-1]) if len(vals) > 0 else 50.0

        if kind == "vol_surge":
            from stratscout.engine.backtest.etf import _vol_surge_score
            return _vol_surge_score(histories, sym, as_of, window=int(ind.get("window", 20)))

        if kind == "ema_cross":
            from stratscout.engine.backtest.etf import _ema_cross_score
            return _ema_cross_score(close, as_of, int(ind["fast"]), int(ind["slow"]))

        if kind == "volatility":
            window = int(ind["window"])
            hist = close.loc[:as_of]
            if len(hist) < window + 2:
                return 0.0
            return float(hist.pct_change().dropna().iloc[-window:].std())

    except Exception:
        pass
    return 0.0


def eval_condition(cond: dict, histories: dict, as_of) -> bool:
    lhs = _eval_indicator(cond["lhs"], histories, as_of)
    rhs = _eval_indicator(cond["rhs"], histories, as_of)
    op = cond["op"]
    if op == ">":  return lhs > rhs
    if op == "<":  return lhs < rhs
    if op == ">=": return lhs >= rhs
    if op == "<=": return lhs <= rhs
    return False


def _eval_action(action: dict, histories: dict, as_of) -> list[str]:
    pool: list[str] = [s for s in action.get("pool", []) if s in histories]
    if not pool:
        return []
    n = min(int(action.get("n", 1)), len(pool))
    signal = action.get("signal", "rsi_lowest")
    window = int(action.get("signal_window", 14))

    if signal in ("rsi_lowest", "rsi_highest"):
        from stratscout.engine.backtest.etf import _rolling_rsi
        scores: dict[str, float] = {}
        for sym in pool:
            rsi_s = _rolling_rsi(histories[sym]["close"], window)
            vals = rsi_s.loc[:as_of]
            scores[sym] = float(vals.iloc[-1]) if len(vals) > 0 else 50.0
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=(signal == "rsi_highest"))
        return [sym for sym, _ in ranked[:n]]

    if signal == "momentum":
        scores = {}
        for sym in pool:
            hist = histories[sym]["close"].loc[:as_of]
            scores[sym] = float(hist.iloc[-1] / hist.iloc[-1 - window] - 1) if len(hist) > window else 0.0
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        return [sym for sym, _ in ranked[:n]]

    if signal == "vol_surge":
        from stratscout.engine.backtest.etf import _vol_surge_score
        scores = {sym: _vol_surge_score(histories, sym, as_of, window=window) for sym in pool}
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        return [sym for sym, _ in ranked[:n]]

    if signal == "ema_cross":
        from stratscout.engine.backtest.etf import _ema_cross_score
        fast = int(action.get("ema_fast", 10))
        slow = int(action.get("ema_slow", 40))
        scores = {sym: _ema_cross_score(histories[sym]["close"], as_of, fast, slow) for sym in pool}
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        return [sym for sym, _ in ranked[:n]]

    # fallback: take first n
    return pool[:n]


def eval_strategy(strategy: dict, histories: dict, as_of) -> list[str]:
    """Evaluate rule tree top-to-bottom; return target ETF symbols."""
    for rule in strategy.get("rules", []):
        try:
            if eval_condition(rule["condition"], histories, as_of):
                return _eval_action(rule["action"], histories, as_of)
        except Exception:
            continue
    return _eval_action(strategy["default_action"], histories, as_of)


# ── Grammar construction ──────────────────────────────────────────────────────

def _random_indicator(kinds: list[str] | None = None) -> dict:
    kind = random.choice(kinds or ["cumret", "rsi", "vol_surge", "ema_cross", "volatility"])
    sym = random.choice(_CONDITION_SYMBOLS)
    if kind == "cumret":
        return {"indicator": "cumret", "symbol": sym, "lookback": random.randint(*_CUMRET_LOOKBACK)}
    if kind == "rsi":
        return {"indicator": "rsi", "symbol": sym, "window": random.randint(*_RSI_WINDOW)}
    if kind == "vol_surge":
        return {"indicator": "vol_surge", "symbol": sym, "window": random.randint(*_VOL_WINDOW)}
    if kind == "ema_cross":
        fast = random.choice(_EMA_FAST)
        slow = random.choice([s for s in _EMA_SLOW if s > fast])
        return {"indicator": "ema_cross", "symbol": sym, "fast": fast, "slow": slow}
    if kind == "volatility":
        return {"indicator": "volatility", "symbol": sym, "window": random.randint(*_VOL_WINDOW)}
    return {"indicator": "cumret", "symbol": sym, "lookback": 60}


def _random_const(ref_kind: str) -> dict:
    if ref_kind == "rsi":
        return {"indicator": "const", "value": round(random.uniform(*_RSI_CONST), 1)}
    if ref_kind == "cumret":
        return {"indicator": "const", "value": round(random.uniform(*_CUMRET_CONST), 4)}
    return {"indicator": "const", "value": round(random.uniform(0.0, 0.1), 4)}


def _random_condition() -> dict:
    lhs = _random_indicator()
    if random.random() < 0.6:
        rhs = _random_indicator([lhs["indicator"]])
        if rhs.get("symbol") == lhs.get("symbol"):
            others = [s for s in _CONDITION_SYMBOLS if s != lhs.get("symbol")]
            rhs["symbol"] = random.choice(others) if others else rhs["symbol"]
    else:
        rhs = _random_const(lhs["indicator"])
    return {"lhs": lhs, "op": random.choice([">", "<", ">=", "<="]), "rhs": rhs}


def _random_action(pool_name: str | None = None) -> dict:
    if pool_name is None:
        pool_name = random.choice(list(_POOL_DEFS.keys()))
    full_pool, min_n = _POOL_DEFS[pool_name]
    size = random.randint(min_n, min(len(full_pool), min_n + 4))
    pool = random.sample(full_pool, size)
    n = random.randint(1, min(3, len(pool)))
    signal = random.choice(_SIGNALS)
    action: dict[str, Any] = {
        "pool": pool, "n": n, "signal": signal,
        "signal_window": random.randint(*_RSI_WINDOW),
    }
    if signal == "ema_cross":
        fast = random.choice(_EMA_FAST)
        slow = random.choice([s for s in _EMA_SLOW if s > fast])
        action["ema_fast"] = fast
        action["ema_slow"] = slow
    return action


def _fix_action(action: dict) -> dict:
    action = copy.deepcopy(action)
    pool = action.get("pool", [])
    if not pool:
        action["pool"] = random.sample(RISK_OFF_FALLING_POOL, MIN_RISK_OFF_FALLING)
        pool = action["pool"]
    action["n"] = max(1, min(int(action.get("n", 1)), len(pool)))
    if action.get("signal") == "ema_cross":
        fast = int(action.get("ema_fast", 10))
        slow = int(action.get("ema_slow", 40))
        if fast >= slow:
            action["ema_slow"] = fast * 3
    return action


def random_strategy() -> dict:
    """Generate a random valid GP strategy."""
    n_rules = random.randint(1, 4)
    used: set[str] = set()
    rules = []
    for _ in range(n_rules):
        available = [p for p in _POOL_DEFS if p not in used]
        if not available:
            break
        pool_name = random.choice(available)
        used.add(pool_name)
        rules.append({"condition": _random_condition(), "action": _random_action(pool_name)})
    remaining = [p for p in _POOL_DEFS if p not in used]
    default_pool = remaining[0] if remaining else random.choice(list(_POOL_DEFS.keys()))
    return {"rules": rules, "default_action": _random_action(default_pool)}


# ── Mutation ──────────────────────────────────────────────────────────────────

def _pool_category(pool: list[str]) -> str | None:
    for name, (full, _) in _POOL_DEFS.items():
        if any(s in full for s in pool):
            return name
    return None


def _mutate_indicator(ind: dict, strength: float) -> dict:
    ind = copy.deepcopy(ind)
    kind = ind["indicator"]
    if kind == "const":
        ind["value"] = round(ind["value"] * (1 + random.uniform(-0.5, 0.5) * strength), 4)
        return ind
    if random.random() < 0.3 * strength:
        ind["symbol"] = random.choice(_CONDITION_SYMBOLS)
    if kind == "cumret":
        lo, hi = _CUMRET_LOOKBACK
        spread = max(1, int((hi - lo) * 0.3 * strength))
        ind["lookback"] = max(lo, min(hi, ind["lookback"] + random.randint(-spread, spread)))
    elif kind in ("rsi", "vol_surge", "volatility"):
        lo, hi = _RSI_WINDOW
        spread = max(1, int((hi - lo) * 0.3 * strength))
        ind["window"] = max(lo, min(hi, ind.get("window", 14) + random.randint(-spread, spread)))
    elif kind == "ema_cross" and random.random() < 0.5 * strength:
        fast = random.choice(_EMA_FAST)
        slow = random.choice([s for s in _EMA_SLOW if s > fast])
        ind["fast"], ind["slow"] = fast, slow
    return ind


def _mutate_action(action: dict, strength: float) -> dict:
    action = copy.deepcopy(action)
    if random.random() < 0.3 * strength:
        action["signal"] = random.choice(_SIGNALS)
    if action["signal"] == "ema_cross" and "ema_fast" not in action:
        fast = random.choice(_EMA_FAST)
        action["ema_fast"] = fast
        action["ema_slow"] = random.choice([s for s in _EMA_SLOW if s > fast])
    if random.random() < 0.4 * strength:
        lo, hi = _RSI_WINDOW
        w = action.get("signal_window", 14)
        action["signal_window"] = max(lo, min(hi, w + random.randint(-3, 3)))
    pool = list(action.get("pool", []))
    cat = _pool_category(pool)
    if cat:
        full_pool, min_n = _POOL_DEFS[cat]
        outside = [s for s in full_pool if s not in pool]
        if outside and random.random() < 0.4 * strength:
            if len(pool) > min_n and random.random() > 0.5:
                pool.pop(random.randrange(len(pool)))
            else:
                pool.append(random.choice(outside))
            action["pool"] = pool
    if action["pool"]:
        action["n"] = max(1, min(len(action["pool"]), action.get("n", 1) + random.choice([-1, 0, 1])))
    return _fix_action(action)


def mutate_strategy(s: dict, strength: float = 0.3) -> dict:
    s = copy.deepcopy(s)
    rules = s.get("rules", [])

    if len(rules) < 4 and random.random() < 0.15 * strength:
        used = {_pool_category(r["action"]["pool"]) for r in rules}
        available = [p for p in _POOL_DEFS if p not in used]
        if available:
            rules.append({
                "condition": _random_condition(),
                "action": _random_action(random.choice(available)),
            })

    if len(rules) > 1 and random.random() < 0.15 * strength:
        rules.pop(random.randrange(len(rules)))

    for i, rule in enumerate(rules):
        if random.random() < 0.5 * strength:
            rule = copy.deepcopy(rule)
            cond = rule["condition"]
            if random.random() < 0.5:
                cond["lhs"] = _mutate_indicator(cond["lhs"], strength)
            else:
                cond["rhs"] = _mutate_indicator(cond["rhs"], strength)
            if random.random() < 0.15 * strength:
                cond["op"] = random.choice([">", "<", ">=", "<="])
            rule["condition"] = cond
            rule["action"] = _mutate_action(rule["action"], strength)
            rules[i] = rule

    s["rules"] = rules
    s["default_action"] = _mutate_action(s["default_action"], strength)
    return s


# ── Crossover ─────────────────────────────────────────────────────────────────

def crossover_strategies(a: dict, b: dict) -> tuple[dict, dict]:
    """Single-point crossover of rule lists."""
    a_rules = copy.deepcopy(a.get("rules", []))
    b_rules = copy.deepcopy(b.get("rules", []))
    if not a_rules or not b_rules:
        return copy.deepcopy(a), copy.deepcopy(b)
    cut_a = random.randint(0, len(a_rules))
    cut_b = random.randint(0, len(b_rules))
    child1 = {"rules": (a_rules[:cut_a] + b_rules[cut_b:])[:4],
              "default_action": copy.deepcopy(a["default_action"])}
    child2 = {"rules": (b_rules[:cut_b] + a_rules[cut_a:])[:4],
              "default_action": copy.deepcopy(b["default_action"])}
    return child1, child2


# ── Human-readable description ────────────────────────────────────────────────

def _fmt_ind(ind: dict) -> str:
    kind = ind["indicator"]
    if kind == "const":
        return str(round(float(ind["value"]), 4))
    sym = ind.get("symbol", "?")
    if kind == "cumret":   return f"cumret({sym},{ind['lookback']}d)"
    if kind == "rsi":      return f"rsi({sym},{ind['window']})"
    if kind == "vol_surge":return f"vol_surge({sym},{ind.get('window',20)})"
    if kind == "ema_cross":return f"ema_cross({sym},{ind['fast']}/{ind['slow']})"
    if kind == "volatility":return f"vol({sym},{ind['window']})"
    return "?"


def _fmt_action(action: dict) -> str:
    pool = action.get("pool", [])
    n = action.get("n", 1)
    signal = action.get("signal", "rsi_lowest")
    if signal == "ema_cross":
        sig = f"ema_cross({action.get('ema_fast',10)}/{action.get('ema_slow',40)})"
    else:
        sig = f"{signal}({action.get('signal_window',14)})"
    syms = ",".join(pool[:5]) + ("…" if len(pool) > 5 else "")
    return f"top {n} of [{syms}] by {sig}"


def describe_strategy(s: dict) -> str:
    lines: list[str] = []
    for i, rule in enumerate(s.get("rules", [])):
        cond = rule["condition"]
        kw = "if" if i == 0 else "elif"
        lines.append(f"{kw} {_fmt_ind(cond['lhs'])} {cond['op']} {_fmt_ind(cond['rhs'])}:")
        lines.append(f"    hold {_fmt_action(rule['action'])}")
    lines.append("else:")
    lines.append(f"    hold {_fmt_action(s['default_action'])}")
    return "\n".join(lines)
