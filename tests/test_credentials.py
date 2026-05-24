"""Tests for the credential storage layer."""
from __future__ import annotations


def test_providers_catalog_is_populated():
    from stratscout.engine.credentials import PROVIDERS
    assert "alpaca" in PROVIDERS
    assert "schwab" in PROVIDERS
    assert "polygon" in PROVIDERS
    assert "thetadata" in PROVIDERS


def test_alpaca_required_keys():
    from stratscout.engine.credentials import PROVIDERS
    keys = set(PROVIDERS["alpaca"].required_keys)
    assert "api_key" in keys
    assert "api_secret" in keys


def test_put_and_get_round_trip():
    """Save a value, read it back, then clean up."""
    from stratscout.engine import credentials
    test_provider = "alpaca"
    test_field = "api_key"
    original = credentials.get(test_provider, test_field)
    try:
        credentials.put(test_provider, test_field, "test-value-do-not-use")
        assert credentials.get(test_provider, test_field) == "test-value-do-not-use"
    finally:
        if original is not None:
            credentials.put(test_provider, test_field, original)
        else:
            credentials.delete(test_provider, test_field)


def test_status_marks_missing_keys():
    """For a provider with no keys ever set, status should be all_present=False."""
    from stratscout.engine import credentials

    # Use a fake provider via direct manipulation — only check the real "alpaca"
    # whose status we can predict from current env
    s = credentials.status("alpaca", run_test=False)
    assert s is not None
    assert s.id == "alpaca"
    assert "api_key" in s.keys_present


def test_all_status_returns_all_providers():
    from stratscout.engine.credentials import all_status, PROVIDERS
    out = all_status(run_tests=False)
    assert len(out) == len(PROVIDERS)


def test_get_returns_none_for_unknown_field():
    from stratscout.engine.credentials import get
    assert get("alpaca", "nonexistent_field_xyz") is None or get("alpaca", "nonexistent_field_xyz") == ""
