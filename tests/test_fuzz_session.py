"""End-to-end test of the fuzz session wrapper."""
from __future__ import annotations


def test_run_etf_fuzz_returns_sorted_leaderboard():
    """Small N to keep CI fast — assert the wrapper produces sorted, well-formed rows."""
    from stratscout.engine.fuzzers.session import run_etf_fuzz

    s = run_etf_fuzz(
        train_start="2022-01-01", train_end="2024-01-01",
        fwd_start="2024-01-01",   fwd_end="2025-01-01",
        n_runs=8, workers=2, explore=0.9,
    )
    assert s.completed == 8
    assert len(s.results) <= 8
    # Sorted descending by score
    scores = [r.score for r in s.results]
    assert scores == sorted(scores, reverse=True)
    # Each row should have required fields
    if s.results:
        r = s.results[0]
        assert isinstance(r.params, dict)
        assert isinstance(r.n_trades, int)
        assert "risk_on_pool" in r.params


def test_run_etf_fuzz_progress_cb_fires():
    from stratscout.engine.fuzzers.session import run_etf_fuzz

    calls: list[tuple[int, int]] = []
    run_etf_fuzz(
        train_start="2023-01-01", train_end="2024-01-01",
        fwd_start="2024-01-01",   fwd_end="2024-06-01",
        n_runs=5, workers=2, explore=0.9,
        progress_cb=lambda done, total: calls.append((done, total)),
    )
    assert len(calls) == 5
    assert calls[-1] == (5, 5)
    # done should be monotonically non-decreasing
    for i in range(1, len(calls)):
        assert calls[i][0] >= calls[i - 1][0]


def test_next_params_with_zero_explore_always_refines():
    """``_next_params`` is the dispatcher inside the fuzzer that decides random
    vs refine. When given non-empty top_params and explore_ratio=0.0 it must
    *always* take the refine branch — this is the invariant the seed_params
    path relies on to be a true refine mode."""
    from stratscout.engine.fuzzers.etf import _next_params

    seed = {
        "agg_bil_lookback": 90,
        "tlt_bil_lookback": 15,
        "risk_on_rsi_window": 20,
        "risk_off_rsi_window": 15,
        "risk_on_rsi_direction": "lowest",
        "risk_off_rsi_direction": "lowest",
        "n_risk_on": 1,
        "n_risk_off_rising": 1,
        "n_risk_off_falling": 1,
        "min_hold_days": 7,
        "rising_rate_include_uup": False,
        "sector_diverse": False,
        "risk_on_pool": ["SOXL"],
        "risk_off_rising_pool": ["TBF"],
        "risk_off_falling_pool": ["GLD"],
        "vol_weight_window": 0,
        "vol_score_weight": 0.0,
        "vol_score_window": 20,
        "vol_surge_cap": 3.0,
        "ema_weight": 0.0,
    }
    # 50 trials, explore=0 + only seed in top_params → 100% must refine.
    # refine_params *can* swap one pool member but won't replace the entire
    # pool — so the result pool must always share at least one element with
    # the seed pool.
    seed_on = set(seed["risk_on_pool"])
    for _ in range(50):
        out = _next_params([seed], explore_ratio=0.0)
        out_on = set(out.get("risk_on_pool", []))
        assert out_on & seed_on, (
            f"refine produced pool {out_on} with no overlap with seed {seed_on}"
            " — random branch leaked in"
        )


def test_run_smallcap_fuzz_shape_and_kind():
    """Smoke: run_smallcap_fuzz reports strategy_kind='smallcap' and returns
    a sorted (possibly-empty) leaderboard. We don't assert results exist
    because the smallcap data dir may be empty in CI — but the wrapper must
    not crash and must serialize cleanly."""
    from stratscout.engine.fuzzers.session import run_smallcap_fuzz, session_to_dict

    s = run_smallcap_fuzz(
        train_start="2024-01-01", train_end="2025-01-01",
        fwd_start="2025-01-01",   fwd_end="2025-06-01",
        n_runs=2, workers=1, explore=0.9,
    )
    assert s.strategy_kind == "smallcap"
    assert s.completed == 2
    scores = [r.score for r in s.results]
    assert scores == sorted(scores, reverse=True)
    d = session_to_dict(s, max_rows=10)
    assert d["strategy_kind"] == "smallcap"
    assert "total_results" in d


def test_run_etf_fuzz_accepts_seed_params():
    """End-to-end smoke: run_etf_fuzz with seed_params returns sorted, well-formed
    results without crashing. (Compound drift makes tighter invariants flaky.)"""
    from stratscout.engine.fuzzers.session import run_etf_fuzz
    from stratscout.engine.backtest.etf import random_params

    seed = random_params()
    s = run_etf_fuzz(
        train_start="2023-01-01", train_end="2024-01-01",
        fwd_start="2024-01-01",   fwd_end="2024-06-01",
        n_runs=4, workers=2,
        explore=0.9,
        seed_params=[seed],
    )
    assert s.completed == 4
    assert len(s.results) >= 1
    scores = [r.score for r in s.results]
    assert scores == sorted(scores, reverse=True)
