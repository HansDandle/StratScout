"""Golden-output tests: new engine must produce identical numbers to the legacy modules.

These tests pin random seeds and assert exact equality. If they fail after a
refactor, that refactor changed behavior — investigate before merging.
"""
from __future__ import annotations

import random

import pytest


def _legacy_etf_backtest(params: dict, start: str, end: str, cash: float = 10_000):
    """Imports legacy etf_backtest from the project root."""
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from etf_backtest import run_etf_backtest as legacy_run
    return legacy_run(params, start, end, cash=cash)


def test_etf_backtest_matches_legacy_short_window(fixed_seed):
    """Run the same params through legacy and new module — perf dicts must match exactly."""
    random.seed(fixed_seed)
    from etf_backtest import random_params as legacy_params
    random.seed(fixed_seed)
    params = legacy_params()

    from stratscout.engine.backtest.etf import run_etf_backtest as new_run
    new_result = new_run(params, "2023-01-01", "2024-01-01", cash=10_000)
    legacy_result = _legacy_etf_backtest(params, "2023-01-01", "2024-01-01", cash=10_000)

    assert new_result["perf"] == legacy_result["perf"]


def test_etf_backtest_matches_legacy_long_window(fixed_seed):
    """Different window — same assertion."""
    random.seed(fixed_seed)
    from etf_backtest import random_params as legacy_params
    random.seed(fixed_seed)
    params = legacy_params()

    from stratscout.engine.backtest.etf import run_etf_backtest as new_run
    new_result = new_run(params, "2020-01-01", "2024-01-01", cash=10_000)
    legacy_result = _legacy_etf_backtest(params, "2020-01-01", "2024-01-01", cash=10_000)

    assert new_result["perf"] == legacy_result["perf"]


@pytest.mark.parametrize("seed", [1, 7, 13, 42, 99])
def test_etf_backtest_parity_random_seeds(seed):
    """Sweep across multiple random params — every one must match the legacy run."""
    random.seed(seed)
    from etf_backtest import random_params as legacy_params
    random.seed(seed)
    params = legacy_params()

    from stratscout.engine.backtest.etf import run_etf_backtest as new_run
    new_result = new_run(params, "2022-01-01", "2024-01-01", cash=10_000)
    legacy_result = _legacy_etf_backtest(params, "2022-01-01", "2024-01-01", cash=10_000)

    assert new_result["perf"] == legacy_result["perf"], (
        f"Divergence at seed={seed}: legacy={legacy_result['perf']}, new={new_result['perf']}"
    )
