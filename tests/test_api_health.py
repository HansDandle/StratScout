"""Smoke tests for the FastAPI service."""
from __future__ import annotations

import random

from fastapi.testclient import TestClient


def test_health_returns_ok():
    from stratscout.api.app import app
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "data_dir" in body


def test_backtest_etf_returns_nav():
    from stratscout.api.app import app
    from stratscout.engine.backtest.etf import random_params

    random.seed(42)
    params = random_params()

    client = TestClient(app)
    r = client.post(
        "/backtest",
        json={
            "strategy_kind": "etf",
            "params": params,
            "start": "2023-01-01",
            "end": "2024-01-01",
            "cash": 10_000,
        },
    )
    assert r.status_code == 200, r.text
    j = r.json()
    assert "perf" in j
    assert "total_return_pct" in j["perf"]
    assert "nav_values" in j
    assert len(j["nav_values"]) > 0
    assert all(isinstance(v, float) for v in j["nav_values"])


def test_backtest_rejects_unknown_strategy_kind():
    from stratscout.api.app import app
    client = TestClient(app)
    r = client.post(
        "/backtest",
        json={"strategy_kind": "bogus", "params": {}, "start": "2023-01-01", "end": "2024-01-01"},
    )
    assert r.status_code == 400


def test_baselines_returns_buy_and_hold_for_known_symbols():
    from stratscout.api.app import app
    client = TestClient(app)
    r = client.post(
        "/baselines",
        json={"symbols": ["SPY", "QQQ"], "start": "2023-01-01", "end": "2024-01-01", "cash": 10_000},
    )
    assert r.status_code == 200, r.text
    j = r.json()
    assert len(j["baselines"]) == 2
    for b in j["baselines"]:
        assert b["symbol"] in {"SPY", "QQQ"}
        assert len(b["values"]) > 100
        # First NAV should be near starting cash
        assert abs(b["values"][0] - 10_000) < 50
        # Return should be reasonable for 2023
        assert -50 < b["total_return_pct"] < 200


def test_baselines_404_for_unknown_symbol():
    from stratscout.api.app import app
    client = TestClient(app)
    r = client.post(
        "/baselines",
        json={"symbols": ["NOTAREALSYMBOL"], "start": "2023-01-01", "end": "2024-01-01"},
    )
    assert r.status_code == 404


def test_data_inventory_endpoint():
    from stratscout.api.app import app
    client = TestClient(app)
    r = client.get("/data/inventory")
    assert r.status_code == 200, r.text
    j = r.json()
    assert "total" in j
    assert "symbols" in j
    assert isinstance(j["symbols"], list)
    assert j["total"] == len(j["symbols"])


def test_suggest_fuzz_window_endpoint():
    from stratscout.api.app import app
    client = TestClient(app)
    r = client.post(
        "/data/suggest-fuzz-window",
        json={"required_symbols": ["SPY", "QQQ"], "fwd_months": 12},
    )
    assert r.status_code == 200, r.text
    j = r.json()
    assert "available" in j
    if j["available"]:
        assert j["train_start"] < j["train_end"]
        assert j["train_end"] == j["fwd_start"]


def test_fuzz_endpoint_small_run():
    """Small N — just confirm the endpoint returns a well-formed leaderboard."""
    from stratscout.api.app import app
    client = TestClient(app)
    r = client.post(
        "/fuzz",
        json={
            "strategy_kind": "etf",
            "train_start": "2023-01-01", "train_end": "2024-01-01",
            "fwd_start": "2024-01-01",   "fwd_end": "2024-06-01",
            "n_runs": 5, "workers": 2, "explore": 0.9,
        },
    )
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["completed"] == 5
    assert "results" in j
    # Sorted descending
    scores = [row["score"] for row in j["results"]]
    assert scores == sorted(scores, reverse=True)


def test_fuzz_rejects_unsupported_strategy():
    """ETF and smallcap are wired; anything else is a 400."""
    from stratscout.api.app import app
    client = TestClient(app)
    r = client.post(
        "/fuzz",
        json={
            "strategy_kind": "options",
            "train_start": "2023-01-01", "train_end": "2024-01-01",
            "fwd_start": "2024-01-01",   "fwd_end": "2024-06-01",
            "n_runs": 5,
        },
    )
    assert r.status_code == 400


def test_data_categories_endpoint():
    from stratscout.api.app import app
    client = TestClient(app)
    r = client.get("/data/categories")
    assert r.status_code == 200
    j = r.json()
    assert "categories" in j
    kinds = {c["kind"] for c in j["categories"]}
    # All expected categories should appear (even if 0 files)
    assert "daily" in kinds
    assert "intraday" in kinds
    assert "smallcap" in kinds
    assert "options" in kinds


def test_settings_credentials_lists_all_providers():
    from stratscout.api.app import app
    client = TestClient(app)
    r = client.get("/settings/credentials")
    assert r.status_code == 200
    ids = {p["id"] for p in r.json()["providers"]}
    assert {"alpaca", "schwab", "polygon", "thetadata"} <= ids


def test_settings_credentials_never_exposes_secrets():
    """Even if keys are present, the response only has booleans — never values."""
    from stratscout.api.app import app
    client = TestClient(app)
    r = client.get("/settings/credentials")
    body = r.text
    # Heuristic: real secrets are usually long opaque strings. The response
    # shape only includes `keys_present` (bool dict) so this check is more
    # about the schema staying that way.
    j = r.json()
    for p in j["providers"]:
        # The "keys_present" dict should map field → bool, not field → string
        for v in p["keys_present"].values():
            assert isinstance(v, bool)
        # Top-level fields must not contain raw secrets
        assert "api_secret" not in p
        assert "refresh_token" not in p
    # And the raw body shouldn't contain anything looking like a long opaque key
    assert "BEGIN" not in body  # PEM/private keys


def test_put_credential_rejects_unknown_provider():
    from stratscout.api.app import app
    client = TestClient(app)
    r = client.put(
        "/settings/credentials",
        json={"provider_id": "bogus", "field_name": "x", "value": "y"},
    )
    assert r.status_code == 404


def test_put_credential_rejects_unknown_field():
    from stratscout.api.app import app
    client = TestClient(app)
    r = client.put(
        "/settings/credentials",
        json={"provider_id": "alpaca", "field_name": "definitely_not_a_field", "value": "x"},
    )
    assert r.status_code == 400


def test_data_download_validates_payload():
    from stratscout.api.app import app
    client = TestClient(app)
    r = client.post("/data/download", json={"symbols": []})
    assert r.status_code == 400


def test_strategies_crud_round_trip():
    from stratscout.api.app import app
    client = TestClient(app)

    r = client.post("/strategies", json={
        "name": "api crud test",
        "kind": "etf",
        "params": {"agg_bil_lookback": 60, "risk_on_pool": ["SPY"]},
    })
    assert r.status_code == 200, r.text
    sid = r.json()["id"]

    try:
        r = client.get(f"/strategies/{sid}")
        assert r.status_code == 200
        assert r.json()["name"] == "api crud test"

        r = client.patch(f"/strategies/{sid}", json={"trade_mode": "paper"})
        assert r.status_code == 200
        assert r.json()["trade_mode"] == "paper"

        # Preflight should fail (no walk-forward yet)
        r = client.get(f"/strategies/{sid}/preflight")
        assert r.status_code == 200
        assert r.json()["passed"] is False
    finally:
        r = client.delete(f"/strategies/{sid}")
        assert r.status_code == 200


def test_strategies_rejects_unknown_kind():
    from stratscout.api.app import app
    client = TestClient(app)
    r = client.post("/strategies", json={
        "name": "bogus", "kind": "rocket", "params": {},
    })
    assert r.status_code == 400


def test_walk_forward_validates_payload():
    from stratscout.api.app import app
    client = TestClient(app)
    r = client.post("/walk-forward", json={
        "start": "2024-01-01", "end": "2024-02-01",
        "workers": 99,  # too many
    })
    assert r.status_code == 400
