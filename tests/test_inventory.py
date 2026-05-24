"""Tests for the data inventory scanner."""
from __future__ import annotations


def test_scan_returns_rows():
    from stratscout.engine.data.inventory import scan_inventory
    rows = scan_inventory()
    assert len(rows) > 0
    # Universe baseline: at minimum the anchors should appear
    syms = {r.symbol for r in rows}
    assert "SPY" in syms or "AGG" in syms, "expected at least one anchor or SPY in inventory"


def test_coverage_for_known_symbol_has_dates():
    from stratscout.engine.data.inventory import coverage_for
    c = coverage_for("SPY")
    if c.has_data:
        assert c.first_bar is not None
        assert c.last_bar is not None
        assert c.n_bars > 0
        assert c.first_bar <= c.last_bar


def test_coverage_for_unknown_symbol_marks_no_data():
    from stratscout.engine.data.inventory import coverage_for
    c = coverage_for("NOTAREALSYMBOL123")
    assert c.has_data is False
    assert c.n_bars == 0


def test_summary_aggregates_correctly():
    from stratscout.engine.data.inventory import scan_inventory, summarize
    rows = scan_inventory()
    s = summarize(rows)
    assert s.total == len(rows)
    assert 0 <= s.with_data <= s.total
    assert 0 <= s.stale <= s.with_data
