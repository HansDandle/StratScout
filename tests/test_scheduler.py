"""Tests for the daily scheduler: scheduled_run.py and schtasks.py."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


# ── scheduled_run.run_once() ─────────────────────────────────────────────────

def test_run_once_no_active_strategies(tmp_path):
    """No paper/live strategies → returns (0, 0) without calling run_strategy."""
    from stratscout.engine import scheduled_run

    inactive = [MagicMock(id=1, name="off strat", trade_mode="off")]
    with (
        patch("stratscout.engine.scheduled_run.list_strategies", return_value=inactive),
        patch("stratscout.engine.scheduled_run.run_strategy") as mock_run,
        patch("stratscout.engine.scheduled_run.data_dir", return_value=tmp_path),
    ):
        succeeded, failed = scheduled_run.run_once()

    assert succeeded == 0
    assert failed == 0
    mock_run.assert_not_called()


def test_run_once_active_strategy_called(tmp_path):
    """One paper strategy → run_strategy called once, returns (1, 0)."""
    from stratscout.engine import scheduled_run

    active = [MagicMock(id=7, name="paper strat", trade_mode="paper")]
    fake_result = MagicMock(regime="risk-on", targets=["SOXL"])
    with (
        patch("stratscout.engine.scheduled_run.list_strategies", return_value=active),
        patch("stratscout.engine.scheduled_run.run_strategy", return_value=fake_result) as mock_run,
        patch("stratscout.engine.scheduled_run.data_dir", return_value=tmp_path),
    ):
        succeeded, failed = scheduled_run.run_once()

    assert succeeded == 1
    assert failed == 0
    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args
    assert call_kwargs.args[0] == 7 or call_kwargs.kwargs.get("mode") == "paper"


def test_run_once_strategy_exception_is_isolated(tmp_path):
    """A strategy that raises still lets others run; failed counter increments."""
    from stratscout.engine import scheduled_run

    s_bad = MagicMock(id=1, name="bad", trade_mode="paper")
    s_ok = MagicMock(id=2, name="ok", trade_mode="live")
    fake_result = MagicMock(regime="cash", targets=[])
    call_count = 0

    def fake_run(strategy_id, mode, note):
        nonlocal call_count
        call_count += 1
        if strategy_id == 1:
            raise RuntimeError("broker connection failed")
        return fake_result

    with (
        patch("stratscout.engine.scheduled_run.list_strategies", return_value=[s_bad, s_ok]),
        patch("stratscout.engine.scheduled_run.run_strategy", side_effect=fake_run),
        patch("stratscout.engine.scheduled_run.data_dir", return_value=tmp_path),
    ):
        succeeded, failed = scheduled_run.run_once()

    assert succeeded == 1
    assert failed == 1
    assert call_count == 2


def test_run_once_list_strategies_fails(tmp_path):
    """If list_strategies raises, run_once returns (0, 1) immediately."""
    from stratscout.engine import scheduled_run

    with (
        patch("stratscout.engine.scheduled_run.list_strategies", side_effect=Exception("db gone")),
        patch("stratscout.engine.scheduled_run.data_dir", return_value=tmp_path),
    ):
        succeeded, failed = scheduled_run.run_once()

    assert succeeded == 0
    assert failed == 1


def test_main_returns_0_when_all_succeed(tmp_path):
    """main() exits 0 when at least one strategy ran successfully."""
    from stratscout.engine import scheduled_run

    with patch.object(scheduled_run, "run_once", return_value=(1, 0)):
        assert scheduled_run.main() == 0


def test_main_returns_1_when_all_failed(tmp_path):
    """main() exits 1 only if succeeded==0 and failed>0."""
    from stratscout.engine import scheduled_run

    with patch.object(scheduled_run, "run_once", return_value=(0, 2)):
        assert scheduled_run.main() == 1


def test_main_returns_0_for_empty_run(tmp_path):
    """main() exits 0 when there are no active strategies (noop is still success)."""
    from stratscout.engine import scheduled_run

    with patch.object(scheduled_run, "run_once", return_value=(0, 0)):
        assert scheduled_run.main() == 0


# ── schtasks helpers (platform-safe) ─────────────────────────────────────────

def test_is_supported_matches_platform():
    from stratscout.engine.schtasks import is_supported
    assert is_supported() == (sys.platform == "win32")


def test_require_windows_raises_on_non_windows():
    from stratscout.engine import schtasks

    if sys.platform == "win32":
        pytest.skip("test only meaningful on non-Windows CI")

    with pytest.raises(RuntimeError, match="Windows Task Scheduler"):
        schtasks.status()

    with pytest.raises(RuntimeError, match="Windows Task Scheduler"):
        schtasks.install("09:35")

    with pytest.raises(RuntimeError, match="Windows Task Scheduler"):
        schtasks.remove()


def test_install_rejects_bad_run_time():
    """install() raises ValueError for invalid HH:MM before touching schtasks."""
    from stratscout.engine import schtasks

    if not sys.platform == "win32":
        # Patch _require_windows so we still test the validation
        with patch.object(schtasks, "_require_windows"):
            with pytest.raises(ValueError, match="HH:MM"):
                schtasks.install("9:35")    # single-digit hour
            with pytest.raises(ValueError, match="HH:MM"):
                schtasks.install("9am")


def test_status_returns_not_installed_when_task_missing():
    """status() returns TaskStatus(installed=False) if schtasks says the task
    doesn't exist (rc != 0 with 'cannot find' in stderr)."""
    from stratscout.engine import schtasks

    fake_proc = MagicMock()
    fake_proc.returncode = 1
    fake_proc.stdout = ""
    fake_proc.stderr = "ERROR: The system cannot find the file specified."

    with (
        patch.object(schtasks, "_require_windows"),   # skip platform check
        patch("stratscout.engine.schtasks.subprocess.run", return_value=fake_proc),
    ):
        result = schtasks.status()

    assert result.installed is False
    assert result.next_run is None


def test_status_parses_installed_output():
    """status() correctly parses the 'Field: Value' LIST output."""
    from stratscout.engine import schtasks

    sample_output = (
        "TaskName:          \\StratScout Daily Run\n"
        "Status:            Ready\n"
        "Schedule Type:     Weekly\n"
        "Start Time:        9:35:00 AM\n"
        "Next Run Time:     5/20/2026 9:35:00 AM\n"
        "Last Result:       0x0\n"
    )
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = sample_output

    with (
        patch.object(schtasks, "_require_windows"),
        patch("stratscout.engine.schtasks.subprocess.run", return_value=fake_proc),
    ):
        result = schtasks.status()

    assert result.installed is True
    assert "9:35" in (result.run_time or "")
    assert "9:35" in (result.next_run or "")
    assert result.last_result == "0x0"


def test_install_raises_on_schtasks_failure():
    """install() raises RuntimeError if schtasks /Create returns non-zero."""
    from stratscout.engine import schtasks

    create_proc = MagicMock()
    create_proc.returncode = 1
    create_proc.stderr = "Access is denied."
    create_proc.stdout = ""

    with (
        patch.object(schtasks, "_require_windows"),
        patch("stratscout.engine.schtasks.subprocess.run", return_value=create_proc),
    ):
        with pytest.raises(RuntimeError, match="schtasks /Create failed"):
            schtasks.install("09:35")


def test_remove_returns_false_when_not_installed():
    """remove() returns False (not raises) when the task doesn't exist."""
    from stratscout.engine import schtasks

    del_proc = MagicMock()
    del_proc.returncode = 1
    del_proc.stderr = "ERROR: The system cannot find the file specified."
    del_proc.stdout = ""

    with (
        patch.object(schtasks, "_require_windows"),
        patch("stratscout.engine.schtasks.subprocess.run", return_value=del_proc),
    ):
        result = schtasks.remove()

    assert result is False
