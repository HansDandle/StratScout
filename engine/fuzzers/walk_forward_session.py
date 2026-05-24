"""High-level walk-forward session wrapper for the API.

Same logic as walk_forward_etf.py CLI but returns results in-memory rather
than writing to walk_forward_etf.db. The CLI continues to work unchanged.
"""
from __future__ import annotations

import multiprocessing as mp
from dataclasses import dataclass
from datetime import date

import pandas as pd
from dateutil.relativedelta import relativedelta

from stratscout.engine.fuzzers import walk_forward_etf as wf


@dataclass
class WalkForwardRow:
    month: str
    spy_return_pct: float
    train_score: float
    val_return_pct: float
    val_dd_pct: float
    val_trades: int
    verdict: str           # 'HIT' | 'LOSS' | 'MISSED-UP' | 'cash-ok'
    params: dict | None    # the params chosen by training that month
    note: str | None = None


@dataclass
class WalkForwardSession:
    train_months: int
    n_months: int
    hits: int
    losses: int
    missed_up: int
    cash_ok: int
    active_win_rate: float
    final_equity: float
    spy_equity: float
    starting_cash: float
    rows: list[WalkForwardRow]


def _spy_return_pct(month_start: str, month_end: str) -> float:
    try:
        from stratscout.engine.settings import daily_dir
        path = daily_dir() / "SPY.feather"
        if not path.exists():
            return 0.0
        df = pd.read_feather(path)
        df["date"] = pd.to_datetime(df["date"], utc=True)
        df = df.set_index("date").sort_index()
        s = pd.Timestamp(month_start, tz="UTC")
        e = pd.Timestamp(month_end, tz="UTC")
        sub = df.loc[s:e, "close"]
        if len(sub) < 2:
            return 0.0
        return float(sub.iloc[-1] / sub.iloc[0] - 1) * 100
    except Exception:
        return 0.0


def _build_months(start: str, end: str, train_months: int, n_trials: int,
                  exclude: list[str], val_weeks: int | None = None,
                  fast_mode: bool = False, workers: int = 4,
                  use_gp: bool = False,
                  gp_population: int = 100, gp_generations: int = 30) -> list[tuple]:
    val_start = date.fromisoformat(start)
    val_end   = date.fromisoformat(end)
    months: list[tuple] = []
    m = val_start
    while m < val_end:
        m_end   = m + (relativedelta(weeks=val_weeks) if val_weeks else relativedelta(months=1))
        t_end   = m
        t_mid2  = m - relativedelta(months=train_months // 3)
        t_mid1  = m - relativedelta(months=(train_months * 2) // 3)
        t_start = m - relativedelta(months=train_months)
        if use_gp:
            months.append((
                m.isoformat(), m_end.isoformat(),
                t_start.isoformat(), t_mid1.isoformat(), t_mid2.isoformat(),
                gp_population, gp_generations, exclude,
            ))
        else:
            months.append((
                m.isoformat(), m_end.isoformat(),
                t_start.isoformat(), t_mid1.isoformat(), t_mid2.isoformat(),
                n_trials, exclude, fast_mode, workers,
            ))
        m = m_end
    return months


def _process_raw_rows(raw_rows: list[dict], starting_cash: float,
                      train_months: int,
                      martingale_factor: float = 1.0,
                      reserve_cash: float = 0.0,
                      mm_rate_annual: float = 0.045,
                      val_weeks: int | None = None) -> WalkForwardSession:
    """Convert raw per-month dicts into a WalkForwardSession."""
    raw_rows.sort(key=lambda r: r["month"])
    out: list[WalkForwardRow] = []
    spy_equity = starting_cash
    hits = losses = missed_up = cash_ok = 0
    consecutive_losses = 0

    # Capital split: deployed in strategy, reserve in money market
    deployed = starting_cash
    base_deployed = starting_cash
    reserve = reserve_cash
    periods_per_year = 26 if val_weeks == 2 else 12
    mm_rate_period = mm_rate_annual / periods_per_year

    for raw in raw_rows:
        # Reserve earns MM rate every period
        reserve *= (1 + mm_rate_period)

        # Martingale: target deployed = base × factor^consecutive_losses, capped at 4×
        if martingale_factor > 1.0 and consecutive_losses > 0:
            target = min(base_deployed * (martingale_factor ** consecutive_losses), base_deployed * 4.0)
            extra = target - deployed
            if extra > 0:
                drawn = min(extra, reserve)
                deployed += drawn
                reserve -= drawn

        equity = deployed + reserve
        m_start = raw["month"]
        m_end   = (date.fromisoformat(m_start) + relativedelta(months=1)).isoformat()
        spy_ret = _spy_return_pct(m_start, m_end)
        spy_equity *= (1 + spy_ret / 100)
        market_up = spy_ret > 1.0

        if raw["val_trades"] == 0:
            verdict    = "MISSED-UP" if market_up else "cash-ok"
            val_return = 0.0
            consecutive_losses = 0
            if market_up:
                missed_up += 1
            else:
                cash_ok += 1
        elif raw["val_return"] > 0:
            verdict    = "HIT"
            hits      += 1
            val_return = float(raw["val_return"])
            deployed  *= (1 + val_return / 100)
            consecutive_losses = 0
            # Replenish: push deployed back down to base, excess goes to reserve
            if deployed > base_deployed:
                reserve  += deployed - base_deployed
                deployed  = base_deployed
        else:
            verdict    = "LOSS"
            losses    += 1
            val_return = float(raw["val_return"])
            deployed  *= (1 + val_return / 100)
            consecutive_losses += 1

        equity = deployed + reserve

        params = raw.get("params")
        if isinstance(params, str):
            import json as _json
            try:
                params = _json.loads(params)
            except (TypeError, ValueError):
                params = None

        out.append(WalkForwardRow(
            month=m_start, spy_return_pct=spy_ret,
            train_score=float(raw["train_score"]),
            val_return_pct=val_return, val_dd_pct=float(raw["val_dd"]),
            val_trades=int(raw["val_trades"]),
            verdict=verdict, params=params,
        ))

    active   = hits + losses
    win_rate = (hits / active * 100) if active > 0 else 0.0
    return WalkForwardSession(
        train_months=train_months, n_months=len(out),
        hits=hits, losses=losses, missed_up=missed_up, cash_ok=cash_ok,
        active_win_rate=win_rate,
        final_equity=equity, spy_equity=spy_equity, starting_cash=starting_cash,
        rows=out,
    )


def iter_etf_walk_forward(
    start: str,
    end: str,
    train_months: int = 12,
    n_trials: int = 200,
    workers: int = 4,
    exclude: list[str] | None = None,
    starting_cash: float = 10_000.0,
    val_weeks: int | None = None,
    martingale_factor: float = 1.0,
    reserve_cash: float = 0.0,
    mm_rate_annual: float = 0.045,
    fast_mode: bool = False,
    use_optuna: bool = False,
    use_gp: bool = False,
    gp_population: int = 100,
    gp_generations: int = 30,
    steer_fn=None,          # callable(rows_so_far) -> {"exclude_add": [...], "reason": str} | None
    steer_every: int = 3,   # call steer_fn after every N completed months
):
    """Generator version — yields (completed, total, raw_row) as each period
    finishes, then yields the final WalkForwardSession as the last item.
    Callers can distinguish by checking ``isinstance(item, WalkForwardSession)``.
    val_weeks: None=calendar monthly, 2=biweekly, 1=weekly.
    use_gp: use GP evolution engine instead of random/Bayesian search.
    steer_fn: optional callback for mid-run steering (sequential mode).
    """
    exclude = list(exclude or [])

    if use_gp:
        month_workers = workers
    elif use_optuna:
        month_workers = 1
    else:
        month_workers = workers

    if use_gp:
        run_fn = wf._run_month_gp
    elif use_optuna:
        run_fn = wf._run_month_optuna
    else:
        run_fn = wf._run_month

    # Build initial month list
    months = _build_months(
        start, end, train_months, n_trials, exclude, val_weeks,
        fast_mode, workers,
        use_gp=use_gp, gp_population=gp_population, gp_generations=gp_generations,
    )
    total = len(months)
    raw_rows: list[dict] = []

    pool = mp.Pool(processes=month_workers, initializer=wf._worker_init)
    try:
        if steer_fn is None:
            # Original parallel path — all months in flight at once
            for i, raw in enumerate(pool.imap_unordered(run_fn, months), 1):
                raw_rows.append(raw)
                yield i, total, raw
        else:
            # Batched path — run steer_every months in parallel, then steer, repeat
            i = 0
            while i < len(months):
                batch = months[i:i + steer_every]
                batch_raws = []
                for raw in pool.imap_unordered(run_fn, batch):
                    i += 1
                    raw_rows.append(raw)
                    batch_raws.append(raw)
                    yield i, total, raw

                # Steer after each full batch (not after the last batch)
                if i < len(months):
                    steering = steer_fn(batch_raws)
                    if steering and steering.get("exclude_add"):
                        new_syms = [s for s in steering["exclude_add"] if s not in exclude]
                        if new_syms:
                            exclude = exclude + new_syms
                            yield {"__steering__": True, "exclude_added": new_syms,
                                   "reason": steering.get("reason", ""), "after_month": i}
                            # Rebuild remaining months with updated exclude
                            remaining = _build_months(
                                raw_rows[-1]["month"], end, train_months, n_trials,
                                exclude, val_weeks, fast_mode, workers,
                                use_gp=use_gp, gp_population=gp_population,
                                gp_generations=gp_generations,
                            )
                            months = months[:i] + remaining[1:]
                            total = len(months)
    finally:
        pool.close()
        pool.join()

    yield _process_raw_rows(raw_rows, starting_cash, train_months,
                            martingale_factor, reserve_cash, mm_rate_annual, val_weeks)


def run_etf_walk_forward(
    start: str,
    end: str,
    train_months: int = 12,
    n_trials: int = 200,
    workers: int = 4,
    exclude: list[str] | None = None,
    starting_cash: float = 10_000.0,
    val_weeks: int | None = None,
    martingale_factor: float = 1.0,
    reserve_cash: float = 0.0,
    mm_rate_annual: float = 0.045,
    fast_mode: bool = False,
    use_optuna: bool = False,
    use_gp: bool = False,
    gp_population: int = 100,
    gp_generations: int = 30,
) -> WalkForwardSession:
    """Run a walk-forward validation (blocking). Delegates to iter_etf_walk_forward."""
    exclude = exclude or []
    session = None
    for item in iter_etf_walk_forward(
        start=start, end=end, train_months=train_months,
        n_trials=n_trials, workers=workers,
        exclude=exclude, starting_cash=starting_cash,
        val_weeks=val_weeks, martingale_factor=martingale_factor,
        reserve_cash=reserve_cash, mm_rate_annual=mm_rate_annual,
        fast_mode=fast_mode, use_optuna=use_optuna,
        use_gp=use_gp, gp_population=gp_population, gp_generations=gp_generations,
    ):
        if isinstance(item, WalkForwardSession):
            session = item
    assert session is not None
    return session
