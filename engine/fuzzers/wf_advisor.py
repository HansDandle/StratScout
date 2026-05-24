"""
Walk-forward AI advisor.

Calls Claude to generate:
  B) A one-sentence note for each completed month.
  C) A structured diagnosis after the full run.

Gracefully skips if the Anthropic API key is not configured.
"""
from __future__ import annotations

import json
from typing import Any

from stratscout.engine import credentials


def _api_key() -> str | None:
    return credentials.get("anthropic", "api_key")


def _call(messages: list[dict], model: str = "claude-haiku-4-5-20251001", max_tokens: int = 256) -> str | None:
    key = _api_key()
    if not key:
        return None
    try:
        import requests
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={"model": model, "max_tokens": max_tokens, "messages": messages},
            timeout=30,
        )
        if r.status_code == 200:
            return r.json()["content"][0]["text"].strip()
        return None
    except Exception:
        return None


def advise_month(row: dict, notes_so_far: list[str]) -> str | None:
    """Return a one-sentence note for a completed walk-forward month."""
    verdict = row.get("verdict", "")
    month = row.get("month", "")
    val_ret = row.get("val_return_pct", 0.0)
    val_dd = row.get("val_dd_pct", 0.0)
    spy_ret = row.get("spy_return_pct", 0.0)
    train_score = row.get("train_score", 0.0)
    n_trades = row.get("val_trades", 0)
    params_raw = row.get("params")

    params_str = ""
    if params_raw:
        try:
            p = params_raw if isinstance(params_raw, dict) else json.loads(params_raw)
            if "rules" in p:
                from stratscout.engine.fuzzers.strategy_dsl import describe_strategy
                params_str = describe_strategy(p)
            else:
                params_str = json.dumps(p, separators=(",", ":"))[:200]
        except Exception:
            pass

    context = "\n".join(f"- {n}" for n in notes_so_far[-3:]) if notes_so_far else "None yet."

    prompt = (
        f"Walk-forward month {month}: verdict={verdict}, val_return={val_ret:.1f}%, "
        f"val_dd={val_dd:.1f}%, spy={spy_ret:.1f}%, train_score={train_score:.3f}, "
        f"trades={n_trades}.\nStrategy: {params_str}\n"
        f"Recent pattern from prior months:\n{context}\n\n"
        "In ONE sentence (max 25 words), give a sharp observation about this month's result."
    )
    return _call([{"role": "user", "content": prompt}], max_tokens=60)


def steer(rows: list[dict]) -> dict[str, Any] | None:
    """After every N months, ask Claude which symbols to drop from the search.

    Returns {"exclude_add": ["SYM", ...], "reason": "..."} or None if no key.
    Called with the last 3–6 completed rows so context is tight (low token cost).
    """
    if not rows:
        return None

    summary = "\n".join(
        f"{r.get('month','?')} {r.get('verdict','?')} val={r.get('val_return_pct',0):.1f}% "
        f"dd={r.get('val_dd_pct',0):.1f}% spy={r.get('spy_return_pct',0):.1f}% "
        f"trades={r.get('val_trades',0)}"
        for r in rows
    )

    prompt = (
        f"Recent walk-forward months:\n{summary}\n\n"
        "Based on these results, should any ETF symbols be excluded from the next months' search? "
        "Respond with JSON only: {\"exclude_add\": [\"SYM1\", \"SYM2\"], \"reason\": \"one sentence\"}. "
        "If nothing should be excluded, return {\"exclude_add\": [], \"reason\": \"no change needed\"}. "
        "Only suggest real ETF tickers you are confident about."
    )
    text = _call([{"role": "user", "content": prompt}], max_tokens=80)
    if not text:
        return None
    try:
        result = json.loads(text)
        if isinstance(result.get("exclude_add"), list):
            return result
        return None
    except Exception:
        return None


def analyze_run(rows: list[dict], notes: dict[str, str]) -> dict[str, Any] | None:
    """Return structured diagnosis after a full walk-forward run."""
    if not rows:
        return None

    hits = [r for r in rows if r.get("verdict") == "HIT"]
    losses = [r for r in rows if r.get("verdict") == "LOSS"]

    summary_lines = []
    for r in rows:
        note = notes.get(r.get("month", ""), "")
        summary_lines.append(
            f"{r.get('month','?')} | {r.get('verdict','?')} | "
            f"val={r.get('val_return_pct',0):.1f}% dd={r.get('val_dd_pct',0):.1f}% "
            f"spy={r.get('spy_return_pct',0):.1f}% | {note}"
        )

    prompt = (
        f"Walk-forward run summary ({len(rows)} months, {len(hits)} hits, {len(losses)} losses):\n\n"
        + "\n".join(summary_lines)
        + "\n\nRespond with a JSON object with exactly these keys:\n"
        '  "win_pattern": one sentence describing what the winning months have in common,\n'
        '  "loss_pattern": one sentence describing what the losing months have in common,\n'
        '  "next_steps": a plain string with 2-3 bullet points separated by newlines (no JSON arrays, no markdown).\n'
        "Return ONLY the JSON object, no extra text."
    )

    text = _call(
        [{"role": "user", "content": prompt}],
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
    )
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return {"win_pattern": "", "loss_pattern": "", "next_steps": text}
