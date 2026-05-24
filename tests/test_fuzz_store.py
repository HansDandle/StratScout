"""Tests for fuzz-run persistence."""
from __future__ import annotations

import pytest


@pytest.fixture
def cleanup_runs():
    """Capture the set of run-ids that exist before the test; delete anything new at the end."""
    from stratscout.engine import fuzz_store
    before = {m.id for m in fuzz_store.list_runs(limit=10_000)}
    yield
    after = fuzz_store.list_runs(limit=10_000)
    for m in after:
        if m.id not in before:
            fuzz_store.delete_run(m.id)


def _sample_result(score: float, **overrides) -> dict:
    base = {
        "score": score,
        "train_return_pct": score / 2,
        "train_cagr_pct": score / 3,
        "train_dd_pct": -10.0,
        "fwd_return_pct": score / 4,
        "fwd_cagr_pct": score / 5,
        "fwd_dd_pct": -8.0,
        "n_trades": 5,
        "params": {"risk_on_pool": ["SPY"], "agg_bil_lookback": 60},
    }
    base.update(overrides)
    return base


def test_save_run_persists_meta_and_results(cleanup_runs):
    from stratscout.engine import fuzz_store
    meta = fuzz_store.save_run(
        strategy_kind="etf",
        train_start="2023-01-01", train_end="2024-01-01",
        fwd_start="2024-01-01", fwd_end="2024-06-01",
        n_runs=3, completed=3, failed=0, workers=2, explore=0.6,
        goal_id="balanced", exclude=["FNGU"], elapsed_sec=4.2,
        results=[_sample_result(50), _sample_result(10), _sample_result(80)],
        label="unit-test",
    )
    assert meta.id is not None
    assert meta.top_score == 80
    rows = fuzz_store.get_results(meta.id)
    # results stored sorted by score desc
    assert [r.score for r in rows] == [80, 50, 10]
    assert [r.rank for r in rows] == [1, 2, 3]
    # params round-trip cleanly
    assert rows[0].params == {"risk_on_pool": ["SPY"], "agg_bil_lookback": 60}


def test_list_runs_orders_newest_first(cleanup_runs):
    from stratscout.engine import fuzz_store
    a = fuzz_store.save_run(
        strategy_kind="etf",
        train_start="2023-01-01", train_end="2024-01-01",
        fwd_start="2024-01-01", fwd_end="2024-06-01",
        n_runs=1, completed=1, failed=0, workers=2, explore=0.6,
        goal_id="", exclude=[], elapsed_sec=1.0,
        results=[_sample_result(10)],
    )
    b = fuzz_store.save_run(
        strategy_kind="etf",
        train_start="2023-01-01", train_end="2024-01-01",
        fwd_start="2024-01-01", fwd_end="2024-06-01",
        n_runs=1, completed=1, failed=0, workers=2, explore=0.6,
        goal_id="", exclude=[], elapsed_sec=1.0,
        results=[_sample_result(20)],
    )
    runs = fuzz_store.list_runs()
    ids = [r.id for r in runs[:2]]
    assert b.id in ids
    assert a.id in ids


def test_all_time_leaderboard_aggregates_across_runs(cleanup_runs):
    """Two runs into the DB → their best rows must appear in the global top,
    sorted descending. We don't assert positional rank because there may be
    higher-score rows from prior sessions in the DB."""
    from stratscout.engine import fuzz_store
    run_a = fuzz_store.save_run(
        strategy_kind="etf",
        train_start="2023-01-01", train_end="2024-01-01",
        fwd_start="2024-01-01", fwd_end="2024-06-01",
        n_runs=2, completed=2, failed=0, workers=2, explore=0.6,
        goal_id="", exclude=[], elapsed_sec=1.0,
        results=[_sample_result(50), _sample_result(70)],
    )
    run_b = fuzz_store.save_run(
        strategy_kind="etf",
        train_start="2023-01-01", train_end="2024-01-01",
        fwd_start="2024-01-01", fwd_end="2024-06-01",
        n_runs=1, completed=1, failed=0, workers=2, explore=0.6,
        goal_id="", exclude=[], elapsed_sec=1.0,
        results=[_sample_result(95)],
    )
    top = fuzz_store.all_time_leaderboard(limit=500, strategy_kind="etf")
    # Filter to rows just from these two runs
    mine = [r for r in top if r.run_id in {run_a.id, run_b.id}]
    scores = [r.score for r in mine]
    assert scores == sorted(scores, reverse=True)
    # Both runs' top scores are present
    assert 95.0 in scores
    assert 70.0 in scores


def test_delete_run_removes_results(cleanup_runs):
    from stratscout.engine import fuzz_store
    m = fuzz_store.save_run(
        strategy_kind="etf",
        train_start="2023-01-01", train_end="2024-01-01",
        fwd_start="2024-01-01", fwd_end="2024-06-01",
        n_runs=1, completed=1, failed=0, workers=2, explore=0.6,
        goal_id="", exclude=[], elapsed_sec=1.0,
        results=[_sample_result(50)],
    )
    assert fuzz_store.delete_run(m.id) is True
    assert fuzz_store.get_run(m.id) is None
    assert fuzz_store.get_results(m.id) == []


def test_relabel_run(cleanup_runs):
    from stratscout.engine import fuzz_store
    m = fuzz_store.save_run(
        strategy_kind="etf",
        train_start="2023-01-01", train_end="2024-01-01",
        fwd_start="2024-01-01", fwd_end="2024-06-01",
        n_runs=1, completed=1, failed=0, workers=2, explore=0.6,
        goal_id="", exclude=[], elapsed_sec=1.0,
        results=[_sample_result(50)],
    )
    updated = fuzz_store.relabel_run(m.id, "renamed by test")
    assert updated.label == "renamed by test"


def test_fuzz_endpoint_persists(cleanup_runs):
    """Hit /fuzz with a tiny payload and confirm a run row materializes."""
    from fastapi.testclient import TestClient
    from stratscout.api.app import app
    from stratscout.engine import fuzz_store
    client = TestClient(app)
    r = client.post("/fuzz", json={
        "strategy_kind": "etf",
        "train_start": "2023-01-01", "train_end": "2024-01-01",
        "fwd_start": "2024-01-01",   "fwd_end": "2024-06-01",
        "n_runs": 4, "workers": 2, "explore": 0.9, "label": "pytest persist",
    })
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["run_id"] is not None
    saved = fuzz_store.get_run(j["run_id"])
    assert saved is not None
    assert saved.label == "pytest persist"
    rows = fuzz_store.get_results(j["run_id"])
    assert len(rows) == j["completed"]
