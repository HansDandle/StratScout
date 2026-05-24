"""High-level fuzz session wrapper.

Provides a clean function-call entry point to the fuzzer for use by the API
service. Bypasses CLI arg parsing, returns leaderboard rows in-memory instead
of writing to SQLite. The legacy CLI fuzzers still write to their own DB
(etf_results.db etc.) and continue to work.

Backed by stratscout/engine/fuzzers/etf.py — same _run_one, same scoring,
same parallelism — but a callable instead of a script.
"""
from __future__ import annotations

import multiprocessing as mp
from dataclasses import dataclass, asdict
from typing import Callable

from stratscout.engine.fuzzers import etf as etf_fuzz


@dataclass
class FuzzResultRow:
    score: float
    train_return_pct: float
    train_cagr_pct: float
    train_dd_pct: float
    fwd_return_pct: float
    fwd_cagr_pct: float
    fwd_dd_pct: float
    n_trades: int
    params: dict


@dataclass
class FuzzSession:
    strategy_kind: str
    train_start: str
    train_end: str
    fwd_start: str
    fwd_end: str
    n_runs: int
    workers: int
    explore: float
    exclude: list[str]
    results: list[FuzzResultRow]
    completed: int
    failed: int


def run_etf_fuzz(
    train_start: str,
    train_end: str,
    fwd_start: str,
    fwd_end: str,
    n_runs: int = 200,
    workers: int = 4,
    explore: float = 0.6,
    exclude: list[str] | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
    seed_params: list[dict] | None = None,
) -> FuzzSession:
    """Run an ETF fuzz session and return the in-memory leaderboard.

    progress_cb (if given) is invoked as (completed, total) after every result.

    When ``seed_params`` is non-empty the session runs in "refine" mode:
    every trial mutates one of those seeds via ``refine_params()`` (via
    ``_next_params`` with explore=0 and the seeds pre-loaded as elites).
    Use it to drill into known-good rows without random exploration.
    """
    exclude = exclude or []
    # Spawn pool with the same _worker_init signature as the CLI fuzzer.
    # data_end == fwd_end so workers can compute fwd_perf in the same pass.
    pool = mp.Pool(
        processes=workers,
        initializer=etf_fuzz._worker_init,
        initargs=(train_start, train_end, fwd_end, fwd_start, ""),
    )

    results: list[FuzzResultRow] = []
    completed = 0
    failed = 0
    # In refine mode the seeds are the entire elite pool; explore is forced to 0
    # so _next_params always picks the refine branch.
    refine_mode = bool(seed_params)
    top_params: list[dict] = list(seed_params) if seed_params else []
    effective_explore = 0.0 if refine_mode else explore

    try:
        def gen():
            for _ in range(n_runs):
                yield etf_fuzz._next_params(top_params, effective_explore, exclude)

        for raw in pool.imap_unordered(etf_fuzz._run_one, gen(), chunksize=1):
            completed += 1
            if raw is None:
                failed += 1
            else:
                row = FuzzResultRow(**raw)
                results.append(row)
                # Keep top_params bounded so the elite-refine loop has something to pull from
                if row.score > -100:
                    top_params.append(row.params)
                    if len(top_params) > 20:
                        # Drop the lowest-score entry — cheap since list is small
                        top_params.sort(key=lambda p: results[-1].score, reverse=True)
                        top_params = top_params[:20]
            if progress_cb:
                progress_cb(completed, n_runs)
    finally:
        pool.close()
        pool.join()

    # Sort leaderboard descending by score
    results.sort(key=lambda r: r.score, reverse=True)

    return FuzzSession(
        strategy_kind="etf",
        train_start=train_start, train_end=train_end,
        fwd_start=fwd_start, fwd_end=fwd_end,
        n_runs=n_runs, workers=workers, explore=explore, exclude=exclude,
        results=results, completed=completed, failed=failed,
    )


def run_smallcap_fuzz(
    train_start: str,
    train_end: str,
    fwd_start: str,
    fwd_end: str,
    n_runs: int = 200,
    workers: int = 4,
    explore: float = 0.6,
    exclude: list[str] | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
    seed_params: list[dict] | None = None,
) -> FuzzSession:
    """Run a smallcap volume-anomaly fuzz session.

    Same interface as ``run_etf_fuzz``; same FuzzSession output shape.
    ``exclude`` is accepted for API symmetry but the smallcap fuzzer doesn't
    pick from a pool (it scans the entire universe each run), so it's a no-op
    for now.
    """
    # exclude is intentionally unused — kept in the signature for API parity
    _ = exclude
    from stratscout.engine.fuzzers import smallcap as smallcap_fuzz

    refine_mode = bool(seed_params)
    top_params: list[dict] = list(seed_params) if seed_params else []
    effective_explore = 0.0 if refine_mode else explore

    pool = mp.Pool(
        processes=workers,
        initializer=smallcap_fuzz._worker_init,
        # legacy _worker_init signature: (train_start, train_end, fwd_start, fwd_end, recent_start, recent_end)
        # we don't use the "recent" window here, so pass fwd_end for both.
        initargs=(train_start, train_end, fwd_start, fwd_end, fwd_end, fwd_end),
    )

    results: list[FuzzResultRow] = []
    completed = 0
    failed = 0

    try:
        def gen():
            for _ in range(n_runs):
                yield smallcap_fuzz._next_params(top_params, effective_explore)

        for raw in pool.imap_unordered(smallcap_fuzz._run_one, gen(), chunksize=1):
            completed += 1
            if raw is None:
                failed += 1
            else:
                # Map smallcap result keys → unified FuzzResultRow shape
                fwd_return = raw.get("fwd_return")
                fwd_cagr = raw.get("fwd_cagr")
                fwd_dd = raw.get("fwd_max_dd")
                row = FuzzResultRow(
                    score=raw["score"],
                    train_return_pct=raw.get("total_return", 0.0),
                    train_cagr_pct=raw.get("cagr", 0.0),
                    train_dd_pct=raw.get("max_dd", 0.0),
                    fwd_return_pct=fwd_return if fwd_return is not None else 0.0,
                    fwd_cagr_pct=fwd_cagr if fwd_cagr is not None else 0.0,
                    fwd_dd_pct=fwd_dd if fwd_dd is not None else 0.0,
                    n_trades=int(raw.get("n_trades", 0)),
                    params=raw["params"],
                )
                results.append(row)
                if row.score > -100:
                    top_params.append(row.params)
                    if len(top_params) > 20:
                        top_params = top_params[:20]
            if progress_cb:
                progress_cb(completed, n_runs)
    finally:
        pool.close()
        pool.join()

    results.sort(key=lambda r: r.score, reverse=True)

    return FuzzSession(
        strategy_kind="smallcap",
        train_start=train_start, train_end=train_end,
        fwd_start=fwd_start, fwd_end=fwd_end,
        n_runs=n_runs, workers=workers, explore=explore, exclude=[],
        results=results, completed=completed, failed=failed,
    )


def session_to_dict(s: FuzzSession, max_rows: int = 100) -> dict:
    """Serialize a FuzzSession for HTTP transport — truncate to top N rows."""
    return {
        **{k: v for k, v in asdict(s).items() if k != "results"},
        "results": [asdict(r) for r in s.results[:max_rows]],
        "total_results": len(s.results),
    }
