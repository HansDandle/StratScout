"""FastAPI app for the StratScout engine.

In desktop mode this runs as a Python sidecar on 127.0.0.1; in web mode it
runs on Fly.io. Same code, different host. Auth/billing live in front of this
service (Clerk + Next.js Route Handlers) — the FastAPI service trusts the
upstream's user context passed via X-User-Id header in web mode, and is fully
open in desktop mode.
"""
from __future__ import annotations

import logging
import os

import json
import queue
import threading
import time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from stratscout import __version__
from stratscout.api.schemas import (
    BacktestRequest, BacktestResponse, BaselineRequest, BaselineResponse,
    BaselineSeries, CategoriesResponse, CategoryRow,
    CreateStrategyRequest, DownloadRequest, DownloadResponse,
    DryRunResponse, InstallScheduleRequest, RunResponse, ScheduleStatus,
    FuzzResultRow, FuzzRunDetailResponse, FuzzRunListResponse,
    FuzzRunMetaRow, FuzzRunRequest, FuzzRunResponse,
    HealthResponse, InventoryResponse, LeaderboardEntry, LeaderboardResponse,
    PerfSummary, PreflightCheckOut, PreflightResponse,
    ProviderStatus as ProviderStatusModel, ProvidersResponse, PutCredentialRequest,
    RelabelFuzzRunRequest, StrategyListResponse, StrategyRow,
    SuggestFuzzWindowRequest, SuggestFuzzWindowResponse,
    SuggestWalkForwardRequest, SuggestWalkForwardResponse,
    SymbolCoverageRow, TestCredentialResponse, TradeOrderRow, TradeOrdersResponse,
    UpdateStrategyRequest, WalkForwardResponse, WalkForwardRowOut,
    WalkForwardRunRequest,
    FactorRow, FactorsResponse, FactorDownloadRequest, FactorDownloadResponse,
)
from stratscout.engine.settings import data_dir, daily_dir

log = logging.getLogger(__name__)

app = FastAPI(
    title="StratScout API",
    version=__version__,
    description="Backtest, fuzz, and live-trade orchestration",
)

# CORS — wide open in desktop mode (sidecar), restricted in web mode.
_origins = (
    ["*"] if os.environ.get("STRATSCOUT_MODE") == "desktop"
    else os.environ.get("STRATSCOUT_CORS_ORIGINS", "").split(",")
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=__version__,
        data_dir=str(data_dir()),
        mode=os.environ.get("STRATSCOUT_MODE", "dev"),
    )


@app.post("/backtest", response_model=BacktestResponse)
def backtest(req: BacktestRequest) -> BacktestResponse:
    """Run a single backtest synchronously. Returns NAV + perf."""
    if req.strategy_kind == "etf":
        from stratscout.engine.backtest.etf import run_etf_backtest
        try:
            result = run_etf_backtest(req.params, req.start, req.end, cash=req.cash)
        except Exception as e:
            log.exception("etf backtest failed")
            raise HTTPException(status_code=400, detail=str(e)) from e

        nav = result.get("nav_df")
        bnh = result.get("bnh_df")
        # nav_df has shape (N, 1) — collapse to 1-D series of floats
        nav_series = nav["value"] if (nav is not None and "value" in nav.columns) else nav.iloc[:, 0] if nav is not None else None
        return BacktestResponse(
            perf=PerfSummary(**result["perf"]),
            n_trades=len(result.get("trade_df", [])),
            nav_index=[str(d.date()) for d in nav_series.index] if nav_series is not None else [],
            nav_values=[float(v) for v in nav_series.values] if nav_series is not None else [],
            bnh_values=[float(v) for v in bnh.values] if bnh is not None else None,
        )

    if req.strategy_kind == "smallcap":
        from stratscout.engine.backtest.smallcap import run_backtest, load_data, find_signals
        from stratscout.engine.data.universes import smallcap_universe
        try:
            data = load_data(smallcap_universe())
            signals = find_signals(
                data,
                req.params.get("vol_lookback", 20),
                req.params.get("vol_mult", 5.0),
                req.start, req.end,
                require_green=req.params.get("require_green", False),
            )
            result = run_backtest(
                data, signals,
                hold_days=req.params.get("hold_days", 5),
                max_positions=req.params.get("max_positions", 4),
                cash=req.cash,
            )
        except Exception as e:
            log.exception("smallcap backtest failed")
            raise HTTPException(status_code=400, detail=str(e)) from e

        nav = result.get("nav_df")
        return BacktestResponse(
            perf=PerfSummary(**result["perf"]),
            n_trades=len(result.get("trade_df", [])),
            nav_index=[str(d.date()) for d in nav.index] if nav is not None else [],
            nav_values=[float(v) for v in nav.values] if nav is not None else [],
        )

    raise HTTPException(status_code=400, detail=f"Unknown strategy_kind: {req.strategy_kind}")


_BASELINE_LABELS = {
    "SPY":  "SPY (S&P 500)",
    "QQQ":  "QQQ (Nasdaq 100)",
    "TQQQ": "TQQQ (3× Nasdaq)",
    "UPRO": "UPRO (3× S&P 500)",
    "SOXL": "SOXL (3× Semiconductors)",
    "TLT":  "TLT (20Y Treasuries)",
    "GLD":  "GLD (Gold)",
    "AGG":  "AGG (Bond aggregate)",
}


@app.post("/baselines", response_model=BaselineResponse)
def baselines(req: BaselineRequest) -> BaselineResponse:
    """Return buy-and-hold NAV for each symbol — fast, cached, no fuzzing.

    Used by the UI to overlay comparison lines without re-running a backtest.
    """
    import pandas as pd

    out: list[BaselineSeries] = []
    start_ts = pd.Timestamp(req.start, tz="UTC")
    end_ts = pd.Timestamp(req.end, tz="UTC")

    for sym in req.symbols:
        sym_u = sym.upper().strip()
        path = daily_dir() / f"{sym_u}.feather"
        if not path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"No data for {sym_u}. Run the data downloader first.",
            )
        df = pd.read_feather(path)
        df["date"] = pd.to_datetime(df["date"], utc=True)
        df = df.sort_values("date").set_index("date")
        sub = df.loc[start_ts:end_ts, "close"]
        if len(sub) < 2:
            raise HTTPException(
                status_code=400,
                detail=f"Not enough data for {sym_u} in window {req.start}→{req.end}",
            )
        # Normalize so first bar = req.cash, then compound by close-to-close returns
        nav = (sub / sub.iloc[0]) * req.cash
        total_return = (float(sub.iloc[-1] / sub.iloc[0]) - 1) * 100

        out.append(BaselineSeries(
            symbol=sym_u,
            label=_BASELINE_LABELS.get(sym_u, sym_u),
            index=[str(d.date()) for d in nav.index],
            values=[float(v) for v in nav.values],
            total_return_pct=total_return,
        ))

    return BaselineResponse(baselines=out)


# ── Data inventory & window planning ──────────────────────────────────────────

@app.get("/data/inventory", response_model=InventoryResponse)
def data_inventory() -> InventoryResponse:
    """Per-symbol data coverage. The UI shows this so users know what they have."""
    from stratscout.engine.data.inventory import scan_inventory, summarize, to_dict
    rows = scan_inventory()
    s = summarize(rows)
    return InventoryResponse(
        total=s.total, with_data=s.with_data, stale=s.stale,
        sufficient_for_backtest=s.sufficient_for_backtest,
        sufficient_for_walk_forward=s.sufficient_for_walk_forward,
        earliest_bar=s.earliest_bar, latest_bar=s.latest_bar,
        symbols=[SymbolCoverageRow(**to_dict(r)) for r in rows],
    )


@app.post("/data/suggest-fuzz-window", response_model=SuggestFuzzWindowResponse)
def suggest_fuzz_window_ep(req: SuggestFuzzWindowRequest) -> SuggestFuzzWindowResponse:
    from stratscout.engine.data.windows import suggest_fuzz_window
    w = suggest_fuzz_window(
        required_symbols=req.required_symbols,
        fwd_months=req.fwd_months,
        min_train_months=req.min_train_months,
    )
    if not w:
        return SuggestFuzzWindowResponse(available=False, notes=["No overlapping data available."])
    return SuggestFuzzWindowResponse(
        available=True,
        train_start=w.train_start, train_end=w.train_end,
        fwd_start=w.fwd_start, fwd_end=w.fwd_end,
        train_months=w.train_months, fwd_months=w.fwd_months,
        notes=w.notes,
    )


@app.post("/data/suggest-walk-forward", response_model=SuggestWalkForwardResponse)
def suggest_walk_forward_ep(req: SuggestWalkForwardRequest) -> SuggestWalkForwardResponse:
    from stratscout.engine.data.windows import suggest_walk_forward
    w = suggest_walk_forward(
        required_symbols=req.required_symbols,
        target_train_months=req.target_train_months,
        target_validation_months=req.target_validation_months,
    )
    if not w:
        return SuggestWalkForwardResponse(available=False, notes=["No overlapping data available."])
    return SuggestWalkForwardResponse(
        available=True,
        train_months=w.train_months,
        validation_start=w.validation_start,
        validation_end=w.validation_end,
        n_validation_months=w.n_validation_months,
        notes=w.notes,
    )


# ── Data: categories + download ───────────────────────────────────────────────

@app.get("/data/categories", response_model=CategoriesResponse)
def data_categories() -> CategoriesResponse:
    """High-level overview by data type (daily / intraday / smallcap / options)."""
    from stratscout.engine.data.inventory import categorize
    cats = categorize()
    return CategoriesResponse(categories=[CategoryRow(**vars(c)) for c in cats])


@app.post("/data/download", response_model=DownloadResponse)
def data_download(req: DownloadRequest) -> DownloadResponse:
    """Download daily bars for the given symbols. Auto-falls back Alpaca → yfinance.

    For interactive single-call use; >100 symbols should add SSE progress later.
    """
    from stratscout.engine.data.fetch import download_symbols
    if not req.symbols:
        raise HTTPException(status_code=400, detail="symbols list is empty")
    if len(req.symbols) > 200:
        raise HTTPException(status_code=400, detail="max 200 symbols per call")
    try:
        p = download_symbols(req.symbols, start=req.start, end=req.end, overwrite=req.overwrite)
    except Exception as e:
        log.exception("download failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
    return DownloadResponse(
        total=p.total,
        done=p.done,
        failed=p.failed,
        source_used=p.source_used,
        log_tail=p.log_lines[-80:],  # cap log payload
    )


# ── Credentials / settings ────────────────────────────────────────────────────

@app.get("/settings/credentials", response_model=ProvidersResponse)
def settings_credentials(test: bool = False) -> ProvidersResponse:
    """List all providers and which keys are populated.

    Add ?test=true to actually hit each provider's API to confirm the keys work.
    Never returns the secret values themselves — only presence booleans.
    """
    from stratscout.engine.credentials import all_status
    rows = all_status(run_tests=test)
    return ProvidersResponse(
        providers=[ProviderStatusModel(**vars(r)) for r in rows],
    )


@app.put("/settings/credentials", response_model=ProviderStatusModel)
def put_credential(req: PutCredentialRequest) -> ProviderStatusModel:
    from stratscout.engine import credentials as creds
    if req.provider_id not in creds.PROVIDERS:
        raise HTTPException(status_code=404, detail=f"unknown provider: {req.provider_id}")
    if req.field_name not in creds.PROVIDERS[req.provider_id].required_keys:
        raise HTTPException(status_code=400, detail=f"unknown field for {req.provider_id}: {req.field_name}")
    creds.put(req.provider_id, req.field_name, req.value.strip())
    s = creds.status(req.provider_id, run_test=False)
    assert s is not None
    return ProviderStatusModel(**vars(s))


@app.delete("/settings/credentials/{provider_id}/{field_name}")
def delete_credential(provider_id: str, field_name: str) -> dict:
    from stratscout.engine import credentials as creds
    if provider_id not in creds.PROVIDERS:
        raise HTTPException(status_code=404, detail=f"unknown provider: {provider_id}")
    creds.delete(provider_id, field_name)
    return {"ok": True}


@app.post("/settings/credentials/{provider_id}/test", response_model=TestCredentialResponse)
def test_credential(provider_id: str) -> TestCredentialResponse:
    """Run the provider's test function and report whether it accepted the stored creds."""
    from stratscout.engine.credentials import status
    s = status(provider_id, run_test=True)
    if s is None:
        raise HTTPException(status_code=404, detail=f"unknown provider: {provider_id}")
    return TestCredentialResponse(ok=bool(s.test_ok), message=s.test_message)


# ── Fuzz session ──────────────────────────────────────────────────────────────

@app.post("/fuzz", response_model=FuzzRunResponse)
def fuzz_run(req: FuzzRunRequest) -> FuzzRunResponse:
    """Run a fuzz session synchronously, persist every result, return the leaderboard.

    Persistence lives in SQLite (`fuzz_runs` + `fuzz_results`). The Find tab
    surfaces past runs via /fuzz/runs and the cross-run leaderboard via
    /fuzz/leaderboard.
    """
    if req.strategy_kind not in ("etf", "smallcap"):
        raise HTTPException(status_code=400, detail=f"Unknown strategy_kind: {req.strategy_kind}")
    if req.n_runs < 1 or req.n_runs > 5_000:
        raise HTTPException(status_code=400, detail="n_runs must be between 1 and 5000")
    if req.workers < 1 or req.workers > 16:
        raise HTTPException(status_code=400, detail="workers must be between 1 and 16")

    import time as _time
    from stratscout.engine.fuzzers.session import run_etf_fuzz, run_smallcap_fuzz, session_to_dict
    from stratscout.engine import fuzz_store

    fuzz_fn = run_etf_fuzz if req.strategy_kind == "etf" else run_smallcap_fuzz

    t0 = _time.perf_counter()
    try:
        s = fuzz_fn(
            train_start=req.train_start, train_end=req.train_end,
            fwd_start=req.fwd_start, fwd_end=req.fwd_end,
            n_runs=req.n_runs, workers=req.workers, explore=req.explore,
            exclude=req.exclude,
            seed_params=req.seed_params or None,
        )
    except Exception as e:
        log.exception("fuzz run failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
    elapsed = _time.perf_counter() - t0

    d = session_to_dict(s, max_rows=100)

    # In refine mode, default the persisted label to something descriptive
    # so the saved-runs panel can tell them apart at a glance.
    effective_label = req.label
    if req.seed_params and not effective_label:
        effective_label = f"refine of {len(req.seed_params)} seed{'s' if len(req.seed_params) != 1 else ''}"

    # Persist (best-effort — UI must still get its response even if save fails)
    run_id: int | None = None
    try:
        # Save EVERY result (not just the top 100 the API returns)
        all_results = [
            {
                "score": r.score,
                "train_return_pct": r.train_return_pct,
                "train_cagr_pct": r.train_cagr_pct,
                "train_dd_pct": r.train_dd_pct,
                "fwd_return_pct": r.fwd_return_pct,
                "fwd_cagr_pct": r.fwd_cagr_pct,
                "fwd_dd_pct": r.fwd_dd_pct,
                "n_trades": r.n_trades,
                "params": r.params,
            }
            for r in s.results
        ]
        meta = fuzz_store.save_run(
            strategy_kind=req.strategy_kind,
            train_start=req.train_start, train_end=req.train_end,
            fwd_start=req.fwd_start, fwd_end=req.fwd_end,
            n_runs=req.n_runs, completed=s.completed, failed=s.failed,
            workers=req.workers, explore=req.explore,
            goal_id=req.goal_id, exclude=req.exclude,
            elapsed_sec=elapsed,
            results=all_results,
            label=effective_label,
        )
        run_id = meta.id
    except Exception:
        log.exception("fuzz_store.save_run failed (response still returned)")

    return FuzzRunResponse(
        strategy_kind=d["strategy_kind"],
        train_start=d["train_start"], train_end=d["train_end"],
        fwd_start=d["fwd_start"], fwd_end=d["fwd_end"],
        n_runs=d["n_runs"], completed=d["completed"], failed=d["failed"],
        total_results=d["total_results"],
        results=[FuzzResultRow(**r) for r in d["results"]],
        run_id=run_id,
        elapsed_sec=elapsed,
    )


# ── Fuzz history + cross-run leaderboard ──────────────────────────────────────

def _meta_to_row(m) -> FuzzRunMetaRow:
    return FuzzRunMetaRow(
        id=m.id, ran_at=m.ran_at, strategy_kind=m.strategy_kind,
        train_start=m.train_start, train_end=m.train_end,
        fwd_start=m.fwd_start, fwd_end=m.fwd_end,
        n_runs=m.n_runs, completed=m.completed, failed=m.failed,
        workers=m.workers, explore=m.explore, goal_id=m.goal_id,
        exclude=m.exclude, elapsed_sec=m.elapsed_sec,
        top_score=m.top_score, label=m.label,
    )


@app.get("/fuzz/runs", response_model=FuzzRunListResponse)
def fuzz_runs_list(limit: int = 30) -> FuzzRunListResponse:
    from stratscout.engine import fuzz_store
    return FuzzRunListResponse(
        runs=[_meta_to_row(m) for m in fuzz_store.list_runs(limit=limit)],
    )


@app.get("/fuzz/runs/{run_id}", response_model=FuzzRunDetailResponse)
def fuzz_runs_detail(run_id: int, limit: int = 100) -> FuzzRunDetailResponse:
    from stratscout.engine import fuzz_store
    meta = fuzz_store.get_run(run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="fuzz run not found")
    results = fuzz_store.get_results(run_id, limit=limit)
    return FuzzRunDetailResponse(
        meta=_meta_to_row(meta),
        results=[
            FuzzResultRow(
                score=r.score,
                train_return_pct=r.train_return_pct,
                train_cagr_pct=r.train_cagr_pct,
                train_dd_pct=r.train_dd_pct,
                fwd_return_pct=r.fwd_return_pct,
                fwd_cagr_pct=r.fwd_cagr_pct,
                fwd_dd_pct=r.fwd_dd_pct,
                n_trades=r.n_trades,
                params=r.params,
            )
            for r in results
        ],
    )


@app.delete("/fuzz/runs/{run_id}")
def fuzz_runs_delete(run_id: int) -> dict:
    from stratscout.engine import fuzz_store
    if not fuzz_store.delete_run(run_id):
        raise HTTPException(status_code=404, detail="fuzz run not found")
    return {"ok": True}


@app.patch("/fuzz/runs/{run_id}", response_model=FuzzRunMetaRow)
def fuzz_runs_relabel(run_id: int, req: RelabelFuzzRunRequest) -> FuzzRunMetaRow:
    from stratscout.engine import fuzz_store
    meta = fuzz_store.relabel_run(run_id, req.label)
    if meta is None:
        raise HTTPException(status_code=404, detail="fuzz run not found")
    return _meta_to_row(meta)


@app.get("/fuzz/leaderboard", response_model=LeaderboardResponse)
def fuzz_leaderboard(limit: int = 50, strategy_kind: str = "etf") -> LeaderboardResponse:
    """Top-N results across every saved fuzz run."""
    from stratscout.engine import fuzz_store
    rows = fuzz_store.all_time_leaderboard(limit=limit, strategy_kind=strategy_kind)
    return LeaderboardResponse(
        entries=[
            LeaderboardEntry(
                rank=i + 1,
                score=r.score,
                train_return_pct=r.train_return_pct,
                train_cagr_pct=r.train_cagr_pct,
                train_dd_pct=r.train_dd_pct,
                fwd_return_pct=r.fwd_return_pct,
                fwd_cagr_pct=r.fwd_cagr_pct,
                fwd_dd_pct=r.fwd_dd_pct,
                n_trades=r.n_trades,
                params=r.params,
                run_id=r.run_id,
                ran_at=r.ran_at,
            )
            for i, r in enumerate(rows)
        ],
    )


# ── Strategies CRUD ──────────────────────────────────────────────────────────

def _strategy_to_row(s) -> StrategyRow:
    return StrategyRow(
        id=s.id, name=s.name, kind=s.kind, params=s.params,
        created_at=s.created_at, updated_at=s.updated_at,
        trade_mode=s.trade_mode, archived=s.archived, notes=s.notes,
    )


@app.get("/strategies", response_model=StrategyListResponse)
def list_strategies_ep(include_archived: bool = False) -> StrategyListResponse:
    from stratscout.engine.strategies import list_strategies
    return StrategyListResponse(
        strategies=[_strategy_to_row(s) for s in list_strategies(include_archived=include_archived)],
    )


@app.post("/strategies", response_model=StrategyRow)
def create_strategy_ep(req: CreateStrategyRequest) -> StrategyRow:
    from stratscout.engine.strategies import save_strategy
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    if req.kind not in ("etf", "smallcap"):
        raise HTTPException(status_code=400, detail=f"unknown kind: {req.kind}")
    s = save_strategy(name=req.name.strip(), kind=req.kind, params=req.params, notes=req.notes)
    return _strategy_to_row(s)


@app.get("/strategies/{strategy_id}", response_model=StrategyRow)
def get_strategy_ep(strategy_id: int) -> StrategyRow:
    from stratscout.engine.strategies import get_strategy
    s = get_strategy(strategy_id)
    if s is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    return _strategy_to_row(s)


@app.patch("/strategies/{strategy_id}", response_model=StrategyRow)
def update_strategy_ep(strategy_id: int, req: UpdateStrategyRequest) -> StrategyRow:
    from stratscout.engine.strategies import update_strategy
    try:
        s = update_strategy(
            strategy_id,
            name=req.name, params=req.params, trade_mode=req.trade_mode,
            archived=req.archived, notes=req.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if s is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    return _strategy_to_row(s)


@app.delete("/strategies/{strategy_id}")
def delete_strategy_ep(strategy_id: int) -> dict:
    from stratscout.engine.strategies import delete_strategy
    ok = delete_strategy(strategy_id)
    if not ok:
        raise HTTPException(status_code=404, detail="strategy not found")
    return {"ok": True}


# ── Walk-forward ─────────────────────────────────────────────────────────────

@app.post("/walk-forward", response_model=WalkForwardResponse)
def walk_forward_ep(req: WalkForwardRunRequest) -> WalkForwardResponse:
    """Run a walk-forward validation. Optionally persist against a strategy."""
    if req.workers < 1 or req.workers > 16:
        raise HTTPException(status_code=400, detail="workers must be 1..16")
    if not req.use_gp and (req.n_trials < 10 or req.n_trials > 2000):
        raise HTTPException(status_code=400, detail="n_trials must be 10..2000")
    from stratscout.engine.fuzzers.walk_forward_session import run_etf_walk_forward
    try:
        session = run_etf_walk_forward(
            start=req.start, end=req.end,
            train_months=req.train_months,
            n_trials=req.n_trials, workers=req.workers,
            exclude=req.exclude, starting_cash=req.starting_cash,
            fast_mode=req.fast_mode, use_optuna=req.use_optuna,
            use_gp=req.use_gp, gp_population=req.gp_population,
            gp_generations=req.gp_generations,
        )
    except Exception as e:
        log.exception("walk-forward failed")
        raise HTTPException(status_code=500, detail=str(e)) from e

    # Always persist — strategy_id is optional metadata, not a save gate.
    from stratscout.engine.strategies import save_walk_forward
    saved_run_id: int | None = None
    try:
        run = save_walk_forward(
            strategy_id=req.strategy_id,
            train_months=session.train_months,
            rows=[r.__dict__ for r in session.rows],
            summary={
                "n_months": session.n_months,
                "hits": session.hits, "losses": session.losses,
                "missed_up": session.missed_up, "cash_ok": session.cash_ok,
                "active_win_rate": session.active_win_rate,
            },
            final_equity=session.final_equity,
            spy_equity=session.spy_equity,
        )
        saved_run_id = run.id
    except Exception:
        log.exception("walk-forward save failed (non-fatal)")

    return WalkForwardResponse(
        train_months=session.train_months,
        n_months=session.n_months,
        hits=session.hits, losses=session.losses,
        missed_up=session.missed_up, cash_ok=session.cash_ok,
        active_win_rate=session.active_win_rate,
        final_equity=session.final_equity, spy_equity=session.spy_equity,
        starting_cash=session.starting_cash,
        rows=[WalkForwardRowOut(**r.__dict__) for r in session.rows],
        saved_run_id=saved_run_id,
    )


@app.post("/walk-forward/stream")
def walk_forward_stream_ep(req: WalkForwardRunRequest) -> StreamingResponse:
    """Walk-forward with SSE progress. Yields one event per completed month,
    then a final 'done' event with the full WalkForwardResponse payload."""
    if req.workers < 1 or req.workers > 16:
        raise HTTPException(status_code=400, detail="workers must be 1..16")
    if not req.use_gp and (req.n_trials < 10 or req.n_trials > 2000):
        raise HTTPException(status_code=400, detail="n_trials must be 10..2000")

    # Run the walk-forward in a background thread so we can stream from a
    # synchronous FastAPI endpoint. Results land in a queue; the generator
    # drains it and yields SSE lines.
    q: queue.Queue = queue.Queue()

    def _run():
        from stratscout.engine.fuzzers.walk_forward_session import (
            WalkForwardSession, iter_etf_walk_forward,
        )
        from stratscout.engine.fuzzers import wf_advisor
        # Wrap steer in a thread so Anthropic HTTP call doesn't block the pool
        import concurrent.futures as _cf
        _steer_executor = _cf.ThreadPoolExecutor(max_workers=1)
        def _steer_async(rows):
            try:
                return _steer_executor.submit(wf_advisor.steer, rows).result(timeout=20)
            except Exception:
                return None
        steer_fn = _steer_async if (wf_advisor._api_key() and req.ai_steer_every > 0) else None
        try:
            for item in iter_etf_walk_forward(
                start=req.start, end=req.end,
                train_months=req.train_months,
                n_trials=req.n_trials, workers=req.workers,
                exclude=req.exclude, starting_cash=req.starting_cash,
                val_weeks=req.val_weeks,
                martingale_factor=req.martingale_factor,
                reserve_cash=req.reserve_cash,
                mm_rate_annual=req.mm_rate_annual,
                fast_mode=req.fast_mode,
                use_optuna=req.use_optuna,
                use_gp=req.use_gp,
                gp_population=req.gp_population,
                gp_generations=req.gp_generations,
                steer_fn=steer_fn,
                steer_every=req.ai_steer_every if req.ai_steer_every > 0 else 5,
            ):
                q.put(item)
        except Exception as exc:
            q.put(exc)
        finally:
            q.put(None)  # sentinel

    threading.Thread(target=_run, daemon=True).start()

    def _generate():
        from stratscout.engine.fuzzers import wf_advisor
        t0 = time.monotonic()
        total: int | None = None
        completed = 0
        notes: dict[str, str] = {}   # month → one-sentence note
        notes_list: list[str] = []   # ordered for context window
        while True:
            item = q.get()
            if item is None:
                break
            if isinstance(item, Exception):
                yield f"data: {json.dumps({'type': 'error', 'message': str(item)})}\n\n"
                break
            # Steering event
            if isinstance(item, dict) and item.get("__steering__"):
                yield f"data: {json.dumps({'type': 'steering', 'exclude_added': item['exclude_added'], 'reason': item['reason'], 'after_month': item['after_month']})}\n\n"
                continue
            from stratscout.engine.fuzzers.walk_forward_session import WalkForwardSession
            if isinstance(item, WalkForwardSession):
                # Save and emit final result
                session = item
                saved_run_id: int | None = None
                try:
                    from stratscout.engine.strategies import save_walk_forward
                    run = save_walk_forward(
                        strategy_id=req.strategy_id,
                        train_months=session.train_months,
                        rows=[r.__dict__ for r in session.rows],
                        summary={
                            "n_months": session.n_months,
                            "hits": session.hits, "losses": session.losses,
                            "missed_up": session.missed_up, "cash_ok": session.cash_ok,
                            "active_win_rate": session.active_win_rate,
                        },
                        final_equity=session.final_equity,
                        spy_equity=session.spy_equity,
                    )
                    saved_run_id = run.id
                except Exception:
                    log.exception("walk-forward save failed (non-fatal)")

                # Attach per-month notes and call final analysis
                rows_with_notes = []
                for r in session.rows:
                    d = r.__dict__.copy()
                    d["note"] = notes.get(r.month)
                    rows_with_notes.append(WalkForwardRowOut(**d))

                analysis = wf_advisor.analyze_run(
                    [r.__dict__ for r in session.rows], notes
                )

                payload = WalkForwardResponse(
                    train_months=session.train_months,
                    n_months=session.n_months,
                    hits=session.hits, losses=session.losses,
                    missed_up=session.missed_up, cash_ok=session.cash_ok,
                    active_win_rate=session.active_win_rate,
                    final_equity=session.final_equity, spy_equity=session.spy_equity,
                    starting_cash=session.starting_cash,
                    rows=rows_with_notes,
                    saved_run_id=saved_run_id,
                    analysis=analysis,
                )
                yield f"data: {json.dumps({'type': 'done', 'result': payload.model_dump()})}\n\n"
            else:
                # Progress event: (completed, total, raw_row)
                completed, total, raw = item
                elapsed = time.monotonic() - t0
                secs_per = elapsed / completed
                remaining = int(secs_per * (total - completed)) if total else 0
                # Generate per-month note (non-blocking — skipped if no API key)
                note = wf_advisor.advise_month(raw, notes_list)
                if note:
                    notes[raw.get("month", "")] = note
                    notes_list.append(note)
                yield f"data: {json.dumps({'type': 'progress', 'completed': completed, 'total': total, 'month': raw.get('month',''), 'note': note, 'elapsed_sec': round(elapsed), 'remaining_sec': remaining})}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/strategies/{strategy_id}/walk-forward/latest", response_model=WalkForwardResponse)
def latest_walk_forward_ep(strategy_id: int) -> WalkForwardResponse:
    """Most recent saved walk-forward for this strategy. 404 if none."""
    from stratscout.engine.strategies import get_strategy, latest_walk_forward
    if get_strategy(strategy_id) is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    wf = latest_walk_forward(strategy_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="no walk-forward saved for this strategy")
    rows = [WalkForwardRowOut(**r) for r in wf.rows]
    # starting_cash isn't stored on the row — fall back to the first row's equity
    # (the WF table predates the column). The UI mostly uses final/spy_equity.
    starting_cash = 10_000.0
    return WalkForwardResponse(
        train_months=wf.train_months, n_months=wf.n_months,
        hits=wf.hits, losses=wf.losses,
        missed_up=wf.missed_up, cash_ok=wf.cash_ok,
        active_win_rate=wf.active_win_rate,
        final_equity=wf.final_equity, spy_equity=wf.spy_equity,
        starting_cash=starting_cash,
        rows=rows, saved_run_id=wf.id, ran_at=wf.ran_at,
    )


# ── Trade orders / dry-run ───────────────────────────────────────────────────

def _order_to_row(o) -> TradeOrderRow:
    return TradeOrderRow(
        id=o.id, strategy_id=o.strategy_id, ran_at=o.ran_at,
        mode=o.mode, action=o.action, symbol=o.symbol, qty=o.qty,
        status=o.status, message=o.message, broker_order_id=o.broker_order_id,
    )


@app.get("/strategies/{strategy_id}/orders", response_model=TradeOrdersResponse)
def list_orders_ep(strategy_id: int, limit: int = 100) -> TradeOrdersResponse:
    """Trade orders recorded for this strategy (dry-runs + future paper/live)."""
    from stratscout.engine.strategies import get_strategy
    from stratscout.engine.trader import list_orders
    if get_strategy(strategy_id) is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    return TradeOrdersResponse(
        orders=[_order_to_row(o) for o in list_orders(strategy_id, limit=limit)],
    )


@app.post("/strategies/{strategy_id}/run-now", response_model=RunResponse)
def run_now_ep(strategy_id: int, mode: str = "dry", note: str = "") -> RunResponse:
    """Execute the strategy in dry / paper / live mode.

    - ``mode=dry`` (default): record what the strategy *would* hold right now.
      No broker call.
    - ``mode=paper``: diff target vs broker paper positions, place market
      orders, record BUY/SELL rows with status.
    - ``mode=live``: same but the live account.
    """
    from stratscout.engine.trader import run_strategy, DryRunResult, ExecutionResult
    if mode not in ("dry", "paper", "live"):
        raise HTTPException(status_code=400, detail=f"mode must be dry/paper/live, got {mode!r}")
    try:
        r = run_strategy(strategy_id, mode=mode, note=note)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except NotImplementedError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.exception("run-now failed")
        raise HTTPException(status_code=500, detail=str(e)) from e

    # Both DryRunResult and ExecutionResult expose the same surface — normalize.
    if isinstance(r, DryRunResult):
        return RunResponse(
            strategy_id=r.strategy_id, mode="dry", ran_at=r.ran_at,
            targets=r.targets, regime=r.regime, as_of=r.as_of,
            note=r.note, order_ids=r.order_ids,
            placed=0, failed=0, fell_back_to_dry=False,
        )
    assert isinstance(r, ExecutionResult)
    return RunResponse(
        strategy_id=r.strategy_id, mode=r.mode, ran_at=r.ran_at,
        targets=r.targets, regime=r.regime, as_of=r.as_of,
        note=r.note, order_ids=r.order_ids,
        placed=r.placed, failed=r.failed, fell_back_to_dry=r.fell_back_to_dry,
    )


# ── Daily schedule (Windows Task Scheduler) ──────────────────────────────────

def _status_payload() -> ScheduleStatus:
    from stratscout.engine import schtasks
    if not schtasks.is_supported():
        return ScheduleStatus(supported=False, installed=False, task_name=schtasks.TASK_NAME)
    s = schtasks.status()
    return ScheduleStatus(
        supported=True,
        installed=s.installed,
        next_run=s.next_run,
        last_result=s.last_result,
        schedule=s.schedule,
        run_time=s.run_time,
        task_name=schtasks.TASK_NAME,
    )


@app.get("/schedule", response_model=ScheduleStatus)
def schedule_status_ep() -> ScheduleStatus:
    """Status of the daily-run scheduled task. ``supported=false`` off Windows."""
    return _status_payload()


@app.post("/schedule", response_model=ScheduleStatus)
def schedule_install_ep(req: InstallScheduleRequest) -> ScheduleStatus:
    """Install (or replace) the daily Mon-Fri task at ``run_time`` local."""
    from stratscout.engine import schtasks
    if not schtasks.is_supported():
        raise HTTPException(status_code=400, detail="Scheduler not supported on this OS yet")
    try:
        schtasks.install(run_time=req.run_time)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return _status_payload()


@app.delete("/schedule", response_model=ScheduleStatus)
def schedule_remove_ep() -> ScheduleStatus:
    """Remove the daily task. Idempotent — returns status either way."""
    from stratscout.engine import schtasks
    if not schtasks.is_supported():
        raise HTTPException(status_code=400, detail="Scheduler not supported on this OS yet")
    try:
        schtasks.remove()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return _status_payload()


# ── Preflight ────────────────────────────────────────────────────────────────

@app.get("/strategies/{strategy_id}/preflight", response_model=PreflightResponse)
def preflight_ep(strategy_id: int) -> PreflightResponse:
    from stratscout.engine.preflight import evaluate
    from stratscout.engine.strategies import save_preflight
    report = evaluate(strategy_id)
    if report is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    save_preflight(strategy_id, report.passed, [vars(c) for c in report.checks])
    return PreflightResponse(
        strategy_id=report.strategy_id,
        passed=report.passed,
        checks=[PreflightCheckOut(**vars(c)) for c in report.checks],
    )


# ── Factor Lab ────────────────────────────────────────────────────────────────

@app.get("/factors", response_model=FactorsResponse)
def factors_list_ep() -> FactorsResponse:
    """List all factors with availability status and IC stats (if computed)."""
    from stratscout.engine.data.factors import FACTOR_META, load_local_factors
    from stratscout.engine.data.factor_backtest import run_factor_backtest

    loaded = load_local_factors()
    # Run IC analysis if SPY data is available — fast enough (< 1s for 10 factors)
    try:
        ic_results = {r["name"]: r for r in run_factor_backtest(verbose=False)}
    except Exception:
        ic_results = {}

    rows = []
    # Known base factors
    for meta in FACTOR_META:
        name = meta["name"]
        ic = ic_results.get(name, {})
        rows.append(FactorRow(
            name=name,
            tier=meta["tier"],
            description=meta["description"],
            hypothesis=meta["hypothesis"],
            has_data=name in loaded,
            last_date=ic.get("last_date") or None,
            current_value=ic.get("current_value"),
            n_months=ic.get("n_months"),
            ic=ic.get("ic"),
            abs_ic=ic.get("abs_ic"),
            t_stat=ic.get("t_stat"),
            p_bonferroni=ic.get("p_bonferroni"),
            significant=ic.get("significant"),
            ic_train=ic.get("ic_train"),
            ic_oos=ic.get("ic_oos"),
            ic_bull=ic.get("ic_bull"),
            ic_bear=ic.get("ic_bear"),
            ic_sideways=ic.get("ic_sideways"),
        ))
    # Derived factors (d__ prefix)
    for name in sorted(loaded):
        if not name.startswith("d__"):
            continue
        ic = ic_results.get(name, {})
        # Decode recipe from name for display
        parts = name[3:].split("__")
        description = " | ".join(parts)
        rows.append(FactorRow(
            name=name,
            tier=3,
            description=description,
            hypothesis="Derived combination",
            has_data=True,
            last_date=ic.get("last_date") or None,
            current_value=ic.get("current_value"),
            n_months=ic.get("n_months"),
            ic=ic.get("ic"),
            abs_ic=ic.get("abs_ic"),
            t_stat=ic.get("t_stat"),
            p_bonferroni=ic.get("p_bonferroni"),
            significant=ic.get("significant"),
            ic_train=ic.get("ic_train"),
            ic_oos=ic.get("ic_oos"),
            ic_bull=ic.get("ic_bull"),
            ic_bear=ic.get("ic_bear"),
            ic_sideways=ic.get("ic_sideways"),
        ))
    return FactorsResponse(factors=rows)


@app.get("/factors/survivors")
def factors_survivors_ep(top_n: int = 100):
    """Return survivors grouped by which base factors appear together."""
    import csv, math as _math
    from stratscout.engine.data.factor_backtest import _survivors_path, group_survivors_by_factors
    path = _survivors_path()
    if not path.exists():
        return {"groups": [], "survivors": [], "total_unique": 0, "total_rows": 0, "log_path": str(path)}

    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    def _f(v):
        try:
            x = float(v)
            return None if _math.isnan(x) else x
        except (TypeError, ValueError):
            return None

    unique_names = {r["name"] for r in rows}
    groups = group_survivors_by_factors(rows)[:top_n]

    return {
        "groups": [
            {
                "factor_key": g["factor_key"],
                "factor_set": g["factor_set"],
                "seen_count": g["seen_count"],
                "best_ic": g["best_ic"],
                "best_ic_oos": g["best_ic_oos"],
                "best_name": g["best_name"],
                "significant_count": g["significant_count"],
                "recipe_count": len(g["recipes"]),
            }
            for g in groups
        ],
        "total_unique": len(unique_names),
        "total_rows": len(rows),
        "log_path": str(path),
    }


@app.post("/factors/download", response_model=FactorDownloadResponse)
def factors_download_ep(req: FactorDownloadRequest) -> FactorDownloadResponse:
    """Generate/download factor data files."""
    from stratscout.engine.data.factors import (
        generate_calculable_factors, download_api_factors,
    )
    written: list[str] = []
    failed: list[str] = []

    if req.tier is None or req.tier == 1:
        try:
            w = generate_calculable_factors(overwrite=req.overwrite)
            written.extend(w)
        except Exception as e:
            failed.append(f"tier1: {e}")

    if req.tier is None or req.tier == 2:
        try:
            download_api_factors(overwrite=req.overwrite)
            from stratscout.engine.data.factors import load_local_factors, FACTOR_META
            loaded = set(load_local_factors().keys())
            tier2 = [m["name"] for m in FACTOR_META if m["tier"] == 2]
            for name in tier2:
                if name in loaded and name not in written:
                    written.append(name)
        except Exception as e:
            failed.append(f"tier2: {e}")

    if req.derive or (req.tier is None and req.n_derived > 0):
        try:
            from stratscout.engine.data.factors import generate_derived_factors, clear_derived_factors
            if req.clear_derived:
                clear_derived_factors()
            dw = generate_derived_factors(n=req.n_derived, overwrite=req.overwrite)
            written.extend(dw)
        except Exception as e:
            failed.append(f"derived: {e}")

    msg = f"{len(written)} factors ready"
    if failed:
        msg += f", {len(failed)} failed"
    return FactorDownloadResponse(written=written, failed=failed, message=msg)


def main() -> None:
    """Entry point for running the API as a sidecar process."""
    import uvicorn
    host = os.environ.get("STRATSCOUT_API_HOST", "127.0.0.1")
    port = int(os.environ.get("STRATSCOUT_API_PORT", "8765"))
    uvicorn.run("stratscout.api.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
