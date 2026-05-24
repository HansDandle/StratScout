"""Windows Task Scheduler integration for the daily run.

Wraps the ``schtasks.exe`` CLI behind a tiny Python facade so the API service
can install / inspect / remove the daily-run task without shelling out from
the UI.

A single named task ("StratScout Daily Run") fires
``python -m stratscout.engine.scheduled_run`` on weekdays at a chosen local
time. Re-installing replaces the existing task atomically.

Mac / Linux equivalents (launchd, cron) aren't implemented yet — the helper
raises ``RuntimeError("unsupported platform")`` off Windows so the API can
surface a friendly message.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass

TASK_NAME = "StratScout Daily Run"


@dataclass
class TaskStatus:
    installed: bool
    next_run: str | None = None           # e.g. "5/19/2026 9:35:00 AM" (locale-formatted)
    last_result: str | None = None        # e.g. "0x0"
    schedule: str | None = None           # e.g. "Weekly"
    run_time: str | None = None           # e.g. "9:35:00 AM"
    raw: str = ""                         # full schtasks /Query output for debugging


def is_supported() -> bool:
    return sys.platform == "win32"


def _require_windows() -> None:
    if not is_supported():
        raise RuntimeError(
            "Windows Task Scheduler is the only scheduler wired today. "
            "macOS (launchd) and Linux (systemd timer / cron) are TBD.",
        )


def _python_command() -> str:
    """Build the command schtasks will run.

    schtasks /TR wraps the entire command in quotes itself, so we use plain
    spacing and escape any inner quotes by doubling them.
    """
    exe = sys.executable or "python.exe"
    return f'\\"{exe}\\" -m stratscout.engine.scheduled_run'


def status() -> TaskStatus:
    """Query whether the daily task exists and when it next fires."""
    _require_windows()
    try:
        r = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError as e:
        raise RuntimeError("schtasks.exe not found on PATH") from e

    if r.returncode != 0:
        # Most common reason: task not installed (ERROR: The system cannot find ...)
        return TaskStatus(installed=False, raw=r.stderr or r.stdout)

    out = r.stdout
    # schtasks /FO LIST output is "Field: Value" pairs separated by blank lines.
    fields = {}
    for line in out.splitlines():
        m = re.match(r"^([\w\s/]+?):\s+(.+)$", line.rstrip())
        if m:
            fields.setdefault(m.group(1).strip(), m.group(2).strip())
    return TaskStatus(
        installed=True,
        next_run=fields.get("Next Run Time"),
        last_result=fields.get("Last Result"),
        schedule=fields.get("Schedule Type") or fields.get("Schedule"),
        run_time=fields.get("Start Time"),
        raw=out,
    )


def install(run_time: str = "09:35") -> TaskStatus:
    """Install (or replace) the weekday task at ``run_time`` local.

    ``run_time`` must be HH:MM 24-hour. Days are Mon-Fri.
    """
    _require_windows()
    if not re.match(r"^\d{2}:\d{2}$", run_time):
        raise ValueError("run_time must be HH:MM (24-hour) — e.g. '09:35'")

    # schtasks insists on quoted /TR; we wrap our command so spaces in the
    # python path survive parsing.
    cmd_to_run = f'"{_python_command()}"'

    args = [
        "schtasks", "/Create", "/F",          # /F replaces any existing task
        "/SC", "WEEKLY",
        "/D", "MON,TUE,WED,THU,FRI",
        "/TN", TASK_NAME,
        "/TR", cmd_to_run,
        "/ST", run_time,
        "/RL", "LIMITED",                     # standard-user rights — keychain works
    ]
    try:
        r = subprocess.run(args, capture_output=True, text=True, check=False)
    except FileNotFoundError as e:
        raise RuntimeError("schtasks.exe not found on PATH") from e
    if r.returncode != 0:
        raise RuntimeError(
            f"schtasks /Create failed (rc={r.returncode}): "
            f"{(r.stderr or r.stdout or '').strip()[:300]}",
        )
    return status()


def remove() -> bool:
    """Delete the task. Returns True if it existed, False if it was already gone."""
    _require_windows()
    r = subprocess.run(
        ["schtasks", "/Delete", "/F", "/TN", TASK_NAME],
        capture_output=True, text=True, check=False,
    )
    if r.returncode == 0:
        return True
    # rc=1 with "cannot find" → task didn't exist, treat as idempotent
    err = (r.stderr or r.stdout or "").lower()
    if "cannot find" in err or "does not exist" in err:
        return False
    raise RuntimeError(
        f"schtasks /Delete failed (rc={r.returncode}): "
        f"{(r.stderr or r.stdout or '').strip()[:300]}",
    )


# Silence unused-import warnings.
_ = os
