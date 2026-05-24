"""Daily execution entry point — fired by Windows Task Scheduler.

Walks every saved strategy with ``trade_mode in {paper, live}`` and calls
``run_strategy()`` for each. Logs each run to ``<data_dir>/scheduled_run.log``.

This module is deliberately importable without side effects — the Task
Scheduler invokes it via:

    python -m stratscout.engine.scheduled_run

The script returns a non-zero exit code if *every* strategy failed; otherwise
it returns 0 so Task Scheduler treats the run as successful and keeps
firing on subsequent days.

Notes:
- The Task Scheduler runs the script as the logged-in user so the keychain
  lookups in ``credentials.get`` work.
- The script never raises out of the top-level — each strategy is wrapped in a
  try/except so one bad strategy doesn't poison the whole run.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from datetime import datetime, timezone

from stratscout.engine.settings import data_dir
from stratscout.engine.strategies import list_strategies
from stratscout.engine.trader import run_strategy, DryRunResult, ExecutionResult


def _setup_logging() -> logging.Logger:
    """File-rotating logger at <data_dir>/scheduled_run.log + stderr."""
    log_path = data_dir() / "scheduled_run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("stratscout.scheduled_run")
    logger.setLevel(logging.INFO)
    # Avoid duplicate handlers when invoked multiple times in the same process
    # (mainly relevant for tests).
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=512_000, backupCount=4, encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler(stream=sys.stderr)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


def _format_result(r: DryRunResult | ExecutionResult) -> str:
    if isinstance(r, ExecutionResult):
        return (
            f"mode={r.mode} regime={r.regime} placed={r.placed} failed={r.failed} "
            f"fell_back_to_dry={r.fell_back_to_dry} targets={r.targets}"
        )
    return f"mode=dry regime={r.regime} targets={r.targets}"


def run_once() -> tuple[int, int]:
    """Execute one scheduling pass. Returns ``(succeeded, failed)``.

    Public so tests can drive it without invoking the CLI.
    """
    log = _setup_logging()
    started = datetime.now(timezone.utc).isoformat()
    log.info("scheduled_run begin (utc=%s)", started)

    succeeded = 0
    failed = 0
    try:
        strategies = list_strategies(include_archived=False)
    except Exception:
        log.exception("could not list strategies — aborting run")
        return 0, 1

    active = [s for s in strategies if s.trade_mode in ("paper", "live")]
    log.info("found %d active strategies (of %d total)", len(active), len(strategies))

    for s in active:
        try:
            r = run_strategy(s.id, mode=s.trade_mode, note=f"scheduled @ {started[:16]}Z")
            log.info("strategy %d %s: %s", s.id, s.name, _format_result(r))
            succeeded += 1
        except Exception:
            log.exception("strategy %d %s failed", s.id, s.name)
            failed += 1

    log.info("scheduled_run end · succeeded=%d failed=%d", succeeded, failed)
    return succeeded, failed


def main() -> int:
    succeeded, failed = run_once()
    # Exit code 0 if at least one succeeded *or* nothing was active (a noop is
    # still a successful run from Task Scheduler's POV).
    if succeeded == 0 and failed > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
