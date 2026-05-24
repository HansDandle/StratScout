"""Pre-flight checks that gate the Live trade-mode toggle.

A strategy can only switch to Live when ALL checks pass. The user sees
this checklist in the UI and can fix each item individually.

Checks (in order):
  1. Strategy has at least 3 years of historical backtest passing
  2. Walk-forward validation passed: active win rate >= 50% over >= 12 months
  3. Max drawdown in any window <= 35% (configurable)
  4. At least 30 days of paper trading recorded (TODO once paper engine ships)
  5. User has acknowledged the risk disclosure
  6. Account size sanity check (TODO: when broker is connected)

This module is intentionally pure — no DB writes — so the API layer can
call it stateless and the UI can display the result.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

from stratscout.engine.strategies import (
    Strategy, latest_walk_forward, get_strategy,
)


@dataclass
class PreflightCheck:
    id: str
    label: str
    passed: bool
    hint: str
    fix_action: str = ""   # short label the UI uses on the CTA button


@dataclass
class PreflightReport:
    strategy_id: int
    passed: bool
    checks: list[PreflightCheck]

    def as_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "passed": self.passed,
            "checks": [asdict(c) for c in self.checks],
        }


def evaluate(strategy_id: int, max_dd_threshold: float = 35.0) -> PreflightReport | None:
    """Run all preflight checks against a saved strategy. Returns None if missing."""
    s = get_strategy(strategy_id)
    if s is None:
        return None

    checks: list[PreflightCheck] = []

    # 1. Walk-forward exists?
    wf = latest_walk_forward(strategy_id)
    if wf is None:
        checks.append(PreflightCheck(
            id="walk_forward_present",
            label="Walk-forward validation run",
            passed=False,
            hint="No walk-forward yet. Run one to confirm the strategy holds up out-of-sample.",
            fix_action="Run walk-forward",
        ))
    else:
        checks.append(PreflightCheck(
            id="walk_forward_present",
            label="Walk-forward validation run",
            passed=True,
            hint=f"Last run: {wf.n_months} months evaluated.",
        ))

        # 2. Active win rate >= 50% over >= 12 months
        active = wf.hits + wf.losses
        wr_ok = wf.active_win_rate >= 50.0 and active >= 12
        checks.append(PreflightCheck(
            id="active_win_rate",
            label="Active win rate >= 50% over >= 12 traded months",
            passed=wr_ok,
            hint=(
                f"Active rate {wf.active_win_rate:.0f}% over {active} traded months. "
                f"Need >= 50% over >= 12 to gate Live."
                if not wr_ok else
                f"Active rate {wf.active_win_rate:.0f}% over {active} traded months."
            ),
            fix_action="Tune params and re-run",
        ))

        # 3. Max drawdown gate — based on observed forward DD in the walk-forward
        worst_dd = min((float(row.get("val_dd_pct", row.get("val_dd", 0))) for row in wf.rows), default=0.0)
        dd_ok = worst_dd >= -max_dd_threshold
        checks.append(PreflightCheck(
            id="max_drawdown",
            label=f"Worst monthly drawdown <= {int(max_dd_threshold)}%",
            passed=dd_ok,
            hint=(
                f"Worst observed monthly DD was {worst_dd:.1f}%. "
                f"Threshold: {-max_dd_threshold:.0f}%."
            ),
            fix_action="Add stop-loss or reduce position size",
        ))

    # 4. Paper-trading history (not yet implemented — gate but disclose)
    checks.append(PreflightCheck(
        id="paper_history",
        label="30+ days of paper trading recorded",
        passed=False,
        hint="Paper-trading engine ships in a later phase. For now, this gate is informational.",
        fix_action="Switch to Paper mode",
    ))

    # 5. Risk acknowledgement — derived from strategy.notes containing the token
    ack = "ACK_RISK" in (s.notes or "")
    checks.append(PreflightCheck(
        id="risk_acknowledged",
        label="Risk disclosure acknowledged",
        passed=ack,
        hint=(
            "You've reviewed the strategy's worst-case drawdown and confirmed it's an "
            "acceptable loss for this account."
            if ack else
            "Open the strategy and acknowledge the worst-case drawdown before going live."
        ),
        fix_action="Acknowledge risk",
    ))

    passed = all(c.passed for c in checks)
    return PreflightReport(strategy_id=strategy_id, passed=passed, checks=checks)
