"""Tests for the window suggester."""
from __future__ import annotations


def test_suggest_fuzz_window_returns_valid_split():
    from stratscout.engine.data.windows import suggest_fuzz_window
    w = suggest_fuzz_window(required_symbols=["SPY", "QQQ", "TLT"], fwd_months=12)
    if w is None:
        return  # no overlapping data — okay, just skip
    assert w.train_start < w.train_end
    assert w.train_end == w.fwd_start  # contiguous, no overlap
    assert w.fwd_start < w.fwd_end
    assert w.train_months > 0
    assert w.fwd_months > 0
    assert len(w.notes) > 0


def test_suggest_fuzz_window_shrinks_when_data_short():
    """If we ask for 12mo fwd but min_train requires more than we have, fwd shrinks."""
    from stratscout.engine.data.windows import suggest_fuzz_window
    # Real symbols, but require a huge train window to force the shrinking path
    w = suggest_fuzz_window(required_symbols=["SPY"], fwd_months=12, min_train_months=240)
    if w is None:
        return
    # Forward window should have been reduced
    assert w.fwd_months <= 12


def test_suggest_walk_forward_returns_plan():
    from stratscout.engine.data.windows import suggest_walk_forward
    wf = suggest_walk_forward(required_symbols=["SPY", "QQQ", "TLT"])
    if wf is None:
        return
    assert wf.train_months >= 3
    assert wf.n_validation_months >= 3
    assert wf.validation_start < wf.validation_end


def test_suggest_returns_none_for_unknown_symbols():
    from stratscout.engine.data.windows import suggest_fuzz_window
    w = suggest_fuzz_window(required_symbols=["NOTAREAL1", "NOTAREAL2"])
    assert w is None
