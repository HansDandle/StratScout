"""Credential storage.

Where keys go:
  - Desktop: OS keychain via `keyring` (Windows Credential Locker, macOS Keychain, libsecret)
  - Dev / fallback: ~/.stratscout/credentials.json
  - Legacy migration: reads from project-root .env file on first start

Each "provider" (alpaca / schwab / polygon / thetadata) has its own set of
required keys. The UI uses Provider.required_keys to render the right form,
get_provider_status() to show "Connected"/"Missing" badges, and test_provider()
to verify keys actually work before saving them as primary.

API endpoints just call functions here — no broker logic in the FastAPI layer.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import keyring
import requests

from stratscout.engine.settings import data_dir


SERVICE = "stratscout"  # keyring "service" name — namespace for all keys
_FALLBACK_FILE = Path.home() / ".stratscout" / "credentials.json"


@dataclass
class Provider:
    id: str                          # 'alpaca' | 'schwab' | 'polygon' | 'thetadata'
    name: str                        # human-readable
    required_keys: list[str]         # field names the UI prompts for
    description: str                 # what this provider gives you
    signup_url: str | None = None    # where to get keys
    test_fn: Callable[[dict], tuple[bool, str]] | None = None  # validates a creds dict; returns (ok, message)


# ── Provider catalog ──────────────────────────────────────────────────────────

def _test_alpaca(creds: dict) -> tuple[bool, str]:
    """Hit /v2/account on the paper endpoint."""
    api_key = creds.get("api_key")
    secret = creds.get("api_secret")
    if not api_key or not secret:
        return False, "Missing api_key or api_secret"
    paper = str(creds.get("paper", "true")).lower() != "false"
    base = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
    try:
        r = requests.get(
            f"{base}/v2/account",
            headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret},
            timeout=10,
        )
        if r.status_code == 200:
            j = r.json()
            return True, f"Connected — account {j.get('account_number','?')} (cash ${j.get('cash','?')})"
        return False, f"HTTP {r.status_code}: {r.text[:150]}"
    except requests.exceptions.RequestException as e:
        return False, f"Network error: {e}"


def _test_schwab(creds: dict) -> tuple[bool, str]:
    """Schwab requires an OAuth dance — we can only test that the refresh token still works."""
    refresh = creds.get("refresh_token")
    app_key = creds.get("app_key")
    app_secret = creds.get("app_secret")
    if not all([refresh, app_key, app_secret]):
        return False, "Need app_key, app_secret, and refresh_token (from OAuth flow)"
    try:
        import base64
        b64 = base64.b64encode(f"{app_key}:{app_secret}".encode()).decode()
        r = requests.post(
            "https://api.schwabapi.com/v1/oauth/token",
            headers={"Authorization": f"Basic {b64}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "refresh_token", "refresh_token": refresh},
            timeout=10,
        )
        if r.status_code == 200:
            return True, "Refresh token valid — Schwab connected"
        return False, f"HTTP {r.status_code}: {r.text[:150]} (refresh tokens last 7 days; you may need to re-auth)"
    except requests.exceptions.RequestException as e:
        return False, f"Network error: {e}"


def _test_polygon(creds: dict) -> tuple[bool, str]:
    """Hit a trivial /v3/reference endpoint to validate the Polygon/Massive key."""
    api_key = creds.get("api_key")
    if not api_key:
        return False, "Missing api_key"
    try:
        r = requests.get(
            "https://api.polygon.io/v3/reference/tickers",
            params={"limit": 1, "apiKey": api_key},
            timeout=10,
        )
        if r.status_code == 200:
            return True, "Polygon/Massive key valid"
        return False, f"HTTP {r.status_code}: {r.text[:150]}"
    except requests.exceptions.RequestException as e:
        return False, f"Network error: {e}"


def _test_anthropic(creds: dict) -> tuple[bool, str]:
    """Send a trivial completion to verify the API key works."""
    api_key = creds.get("api_key")
    if not api_key:
        return False, "Missing api_key"
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 8, "messages": [{"role": "user", "content": "hi"}]},
            timeout=15,
        )
        if r.status_code == 200:
            return True, "Anthropic API key valid"
        return False, f"HTTP {r.status_code}: {r.text[:150]}"
    except requests.exceptions.RequestException as e:
        return False, f"Network error: {e}"


def _test_thetadata(creds: dict) -> tuple[bool, str]:
    """ThetaData requires a local Terminal process; we just check we can reach it."""
    try:
        r = requests.get("http://127.0.0.1:25510/v2/list/dates/stock/quote?root=SPY", timeout=3)
        if r.status_code == 200:
            return True, "ThetaTerminal reachable"
        return False, f"ThetaTerminal HTTP {r.status_code}"
    except requests.exceptions.RequestException:
        return False, "ThetaTerminal not running on 127.0.0.1:25510 — start it locally"


PROVIDERS: dict[str, Provider] = {
    "alpaca": Provider(
        id="alpaca",
        name="Alpaca",
        required_keys=["api_key", "api_secret", "paper"],
        description="Stock + ETF daily/intraday bars. Free paper trading. Recommended for getting started.",
        signup_url="https://alpaca.markets/",
        test_fn=_test_alpaca,
    ),
    "schwab": Provider(
        id="schwab",
        name="Charles Schwab",
        required_keys=["app_key", "app_secret", "refresh_token", "account_number"],
        description="Real-money trading + portfolio access. Requires OAuth registration with Schwab developer portal.",
        signup_url="https://developer.schwab.com/",
        test_fn=_test_schwab,
    ),
    "polygon": Provider(
        id="polygon",
        name="Polygon (Massive)",
        required_keys=["api_key"],
        description="Higher-quality historical data going further back. Paid.",
        signup_url="https://polygon.io/",
        test_fn=_test_polygon,
    ),
    "anthropic": Provider(
        id="anthropic",
        name="Anthropic (Claude)",
        required_keys=["api_key"],
        description="Claude AI integration for walk-forward analysis and monthly trade notes.",
        signup_url="https://console.anthropic.com/",
        test_fn=_test_anthropic,
    ),
    "thetadata": Provider(
        id="thetadata",
        name="ThetaData",
        required_keys=[],  # actually uses email/password to log into local Terminal, but the connection is over localhost
        description="Historical options chains with real bid/ask + Greeks. Paid, runs as local Terminal process.",
        signup_url="https://www.thetadata.net/",
        test_fn=_test_thetadata,
    ),
}


# ── Storage layer ─────────────────────────────────────────────────────────────

def _keyring_get(provider_id: str, field_name: str) -> str | None:
    try:
        return keyring.get_password(SERVICE, f"{provider_id}.{field_name}")
    except keyring.errors.KeyringError:
        return None


def _keyring_set(provider_id: str, field_name: str, value: str) -> bool:
    try:
        keyring.set_password(SERVICE, f"{provider_id}.{field_name}", value)
        return True
    except keyring.errors.KeyringError:
        return False


def _keyring_delete(provider_id: str, field_name: str) -> None:
    try:
        keyring.delete_password(SERVICE, f"{provider_id}.{field_name}")
    except keyring.errors.KeyringError:
        pass


def _fallback_load() -> dict:
    if _FALLBACK_FILE.exists():
        try:
            return json.loads(_FALLBACK_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _fallback_save(data: dict) -> None:
    _FALLBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    _FALLBACK_FILE.write_text(json.dumps(data, indent=2))


def _env_file_keys() -> dict[str, str]:
    """Read the legacy project-root .env into a dict, ignoring comments/blank lines.

    Used as a migration source so users with existing .env don't have to re-enter keys.
    """
    candidates = [
        Path(__file__).resolve().parents[2] / ".env",
        Path.cwd() / ".env",
    ]
    for p in candidates:
        if p.exists():
            try:
                out: dict[str, str] = {}
                for line in p.read_text().splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    out[k.strip()] = v.strip().strip('"').strip("'")
                return out
            except OSError:
                continue
    return {}


_ENV_MAPPING = {
    # provider_id.field → .env variable name
    "alpaca.api_key":     "ALPACA_API_KEY",
    "alpaca.api_secret":  "ALPACA_SECRET_KEY",
    "polygon.api_key":    "MASSIVE_API_KEY",
}


def get(provider_id: str, field_name: str) -> str | None:
    """Return the value for a key, trying keyring → fallback JSON → legacy .env."""
    v = _keyring_get(provider_id, field_name)
    if v:
        return v
    fb = _fallback_load()
    v = fb.get(f"{provider_id}.{field_name}")
    if v:
        return v
    env_name = _ENV_MAPPING.get(f"{provider_id}.{field_name}")
    if env_name:
        env_keys = _env_file_keys()
        if env_name in env_keys:
            return env_keys[env_name]
        if env_name in os.environ:
            return os.environ[env_name]
    return None


def get_all(provider_id: str) -> dict:
    """Return a dict of {field: value} for a provider, masking nothing."""
    p = PROVIDERS.get(provider_id)
    if not p:
        return {}
    return {f: get(provider_id, f) for f in p.required_keys}


def put(provider_id: str, field_name: str, value: str) -> bool:
    """Save a key, preferring keyring, falling back to JSON."""
    if _keyring_set(provider_id, field_name, value):
        return True
    fb = _fallback_load()
    fb[f"{provider_id}.{field_name}"] = value
    _fallback_save(fb)
    return True


def delete(provider_id: str, field_name: str) -> None:
    _keyring_delete(provider_id, field_name)
    fb = _fallback_load()
    fb.pop(f"{provider_id}.{field_name}", None)
    _fallback_save(fb)


# ── Higher-level status / test ────────────────────────────────────────────────

@dataclass
class ProviderStatus:
    id: str
    name: str
    description: str
    signup_url: str | None
    required_keys: list[str]
    keys_present: dict[str, bool]  # field name → has value (we never expose values)
    all_present: bool
    test_message: str = ""
    test_ok: bool | None = None      # None == not yet tested


def status(provider_id: str, run_test: bool = False) -> ProviderStatus | None:
    p = PROVIDERS.get(provider_id)
    if not p:
        return None
    present = {f: get(provider_id, f) is not None for f in p.required_keys}
    all_present = all(present.values()) if p.required_keys else True
    test_ok: bool | None = None
    msg = ""
    if run_test and p.test_fn:
        creds = get_all(provider_id)
        test_ok, msg = p.test_fn(creds)
    return ProviderStatus(
        id=p.id,
        name=p.name,
        description=p.description,
        signup_url=p.signup_url,
        required_keys=p.required_keys,
        keys_present=present,
        all_present=all_present,
        test_message=msg,
        test_ok=test_ok,
    )


def all_status(run_tests: bool = False) -> list[ProviderStatus]:
    out: list[ProviderStatus] = []
    for pid in PROVIDERS:
        s = status(pid, run_test=run_tests)
        if s:
            out.append(s)
    return out
