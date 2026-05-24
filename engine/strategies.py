"""Strategy persistence — saved strategies, walk-forward runs, trade-mode state.

Local SQLite DB at <data_dir>/stratscout.db (desktop mode) or
<project_root>/stratscout.db (dev). Web tier will swap for Postgres in Phase 3.

Tables:
  strategies        — user-saved parameter sets
  walk_forward_runs — saved walk-forward validation results
  preflight_checks  — historical preflight passes/fails per strategy
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
from pathlib import Path

from stratscout.engine.settings import db_path


# ── Schema ─────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL,                  -- 'etf' | 'smallcap'
    params          TEXT NOT NULL,                  -- JSON
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    trade_mode      TEXT NOT NULL DEFAULT 'off',    -- 'off' | 'paper' | 'live'
    archived        INTEGER NOT NULL DEFAULT 0,
    notes           TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS walk_forward_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     INTEGER NOT NULL,
    ran_at          TEXT NOT NULL,
    train_months    INTEGER NOT NULL,
    n_months        INTEGER NOT NULL,
    hits            INTEGER NOT NULL,
    losses          INTEGER NOT NULL,
    missed_up       INTEGER NOT NULL,
    cash_ok         INTEGER NOT NULL,
    active_win_rate REAL NOT NULL,
    final_equity    REAL NOT NULL,
    spy_equity      REAL NOT NULL,
    rows            TEXT NOT NULL,                  -- JSON-encoded list of monthly rows
    FOREIGN KEY (strategy_id) REFERENCES strategies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS preflight_checks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     INTEGER NOT NULL,
    ran_at          TEXT NOT NULL,
    passed          INTEGER NOT NULL,
    checks          TEXT NOT NULL,                  -- JSON
    FOREIGN KEY (strategy_id) REFERENCES strategies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS fuzz_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at          TEXT NOT NULL,
    strategy_kind   TEXT NOT NULL,
    train_start     TEXT NOT NULL,
    train_end       TEXT NOT NULL,
    fwd_start       TEXT NOT NULL,
    fwd_end         TEXT NOT NULL,
    n_runs          INTEGER NOT NULL,
    completed       INTEGER NOT NULL,
    failed          INTEGER NOT NULL,
    workers         INTEGER NOT NULL,
    explore         REAL NOT NULL,
    goal_id         TEXT NOT NULL DEFAULT '',
    exclude         TEXT NOT NULL DEFAULT '[]',      -- JSON
    elapsed_sec     REAL NOT NULL DEFAULT 0,
    top_score       REAL,
    label           TEXT NOT NULL DEFAULT ''         -- user-set label e.g. "Steady 200 trials"
);

CREATE INDEX IF NOT EXISTS idx_fuzz_runs_ran_at ON fuzz_runs(ran_at DESC);

CREATE TABLE IF NOT EXISTS fuzz_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL,
    rank            INTEGER NOT NULL,
    score           REAL NOT NULL,
    train_return_pct REAL NOT NULL,
    train_cagr_pct  REAL NOT NULL,
    train_dd_pct    REAL NOT NULL,
    fwd_return_pct  REAL NOT NULL,
    fwd_cagr_pct    REAL NOT NULL,
    fwd_dd_pct      REAL NOT NULL,
    n_trades        INTEGER NOT NULL,
    params          TEXT NOT NULL,                   -- JSON
    FOREIGN KEY (run_id) REFERENCES fuzz_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_fuzz_results_run_score
    ON fuzz_results(run_id, score DESC);
CREATE INDEX IF NOT EXISTS idx_fuzz_results_score_global
    ON fuzz_results(score DESC);

CREATE TABLE IF NOT EXISTS trade_orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     INTEGER NOT NULL,
    ran_at          TEXT NOT NULL,
    mode            TEXT NOT NULL,                  -- 'dry' | 'paper' | 'live'
    action          TEXT NOT NULL,                  -- 'BUY' | 'SELL' | 'HOLD' | 'TARGET'
    symbol          TEXT NOT NULL,
    qty             INTEGER,                        -- null for TARGET / HOLD rows
    status          TEXT NOT NULL,                  -- 'recorded' | 'submitted' | 'filled' | 'rejected'
    message         TEXT NOT NULL DEFAULT '',
    broker_order_id TEXT,
    FOREIGN KEY (strategy_id) REFERENCES strategies(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_trade_orders_strategy_ts
    ON trade_orders(strategy_id, ran_at DESC);
"""


def _conn() -> sqlite3.Connection:
    p = db_path("stratscout")
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db() -> None:
    con = _conn()
    con.executescript(_SCHEMA)
    con.commit()
    con.close()


# ── Strategies ──────────────────────────────────────────────────────────────

@dataclass
class Strategy:
    id: int
    name: str
    kind: str
    params: dict
    created_at: str
    updated_at: str
    trade_mode: str
    archived: bool
    notes: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Strategy":
        return cls(
            id=row["id"],
            name=row["name"],
            kind=row["kind"],
            params=json.loads(row["params"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            trade_mode=row["trade_mode"],
            archived=bool(row["archived"]),
            notes=row["notes"],
        )


def save_strategy(name: str, kind: str, params: dict, notes: str = "") -> Strategy:
    init_db()
    now = _now_iso()
    con = _conn()
    cur = con.execute(
        "INSERT INTO strategies (name, kind, params, created_at, updated_at, trade_mode, archived, notes) "
        "VALUES (?, ?, ?, ?, ?, 'off', 0, ?)",
        (name, kind, json.dumps(params, default=str), now, now, notes),
    )
    new_id = cur.lastrowid
    con.commit()
    row = con.execute("SELECT * FROM strategies WHERE id = ?", (new_id,)).fetchone()
    con.close()
    return Strategy.from_row(row)


def list_strategies(include_archived: bool = False) -> list[Strategy]:
    init_db()
    con = _conn()
    if include_archived:
        rows = con.execute("SELECT * FROM strategies ORDER BY updated_at DESC").fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM strategies WHERE archived = 0 ORDER BY updated_at DESC"
        ).fetchall()
    con.close()
    return [Strategy.from_row(r) for r in rows]


def get_strategy(strategy_id: int) -> Strategy | None:
    init_db()
    con = _conn()
    row = con.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
    con.close()
    return Strategy.from_row(row) if row else None


def update_strategy(
    strategy_id: int,
    *,
    name: str | None = None,
    params: dict | None = None,
    trade_mode: str | None = None,
    archived: bool | None = None,
    notes: str | None = None,
) -> Strategy | None:
    init_db()
    fields, values = [], []
    if name is not None:
        fields.append("name = ?"); values.append(name)
    if params is not None:
        fields.append("params = ?"); values.append(json.dumps(params, default=str))
    if trade_mode is not None:
        if trade_mode not in ("off", "paper", "live"):
            raise ValueError(f"trade_mode must be off/paper/live, got {trade_mode!r}")
        fields.append("trade_mode = ?"); values.append(trade_mode)
    if archived is not None:
        fields.append("archived = ?"); values.append(int(archived))
    if notes is not None:
        fields.append("notes = ?"); values.append(notes)
    if not fields:
        return get_strategy(strategy_id)
    fields.append("updated_at = ?")
    values.append(_now_iso())
    values.append(strategy_id)
    con = _conn()
    con.execute(f"UPDATE strategies SET {', '.join(fields)} WHERE id = ?", values)
    con.commit()
    con.close()
    return get_strategy(strategy_id)


def delete_strategy(strategy_id: int) -> bool:
    init_db()
    con = _conn()
    cur = con.execute("DELETE FROM strategies WHERE id = ?", (strategy_id,))
    con.commit()
    deleted = cur.rowcount > 0
    con.close()
    return deleted


# ── Walk-forward runs ──────────────────────────────────────────────────────

@dataclass
class WalkForwardRun:
    id: int
    strategy_id: int
    ran_at: str
    train_months: int
    n_months: int
    hits: int
    losses: int
    missed_up: int
    cash_ok: int
    active_win_rate: float
    final_equity: float
    spy_equity: float
    rows: list[dict] = field(default_factory=list)


def save_walk_forward(
    strategy_id: int | None,
    train_months: int,
    rows: list[dict],
    summary: dict,
    final_equity: float,
    spy_equity: float,
) -> WalkForwardRun:
    init_db()
    now = _now_iso()
    con = _conn()
    cur = con.execute(
        "INSERT INTO walk_forward_runs "
        "(strategy_id, ran_at, train_months, n_months, hits, losses, missed_up, cash_ok, "
        " active_win_rate, final_equity, spy_equity, rows) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            strategy_id, now, train_months,
            int(summary.get("n_months", len(rows))),
            int(summary.get("hits", 0)),
            int(summary.get("losses", 0)),
            int(summary.get("missed_up", 0)),
            int(summary.get("cash_ok", 0)),
            float(summary.get("active_win_rate", 0.0)),
            float(final_equity),
            float(spy_equity),
            json.dumps(rows, default=str),
        ),
    )
    new_id = cur.lastrowid
    con.commit()
    row = con.execute("SELECT * FROM walk_forward_runs WHERE id = ?", (new_id,)).fetchone()
    con.close()
    return WalkForwardRun(
        id=row["id"], strategy_id=row["strategy_id"], ran_at=row["ran_at"],
        train_months=row["train_months"], n_months=row["n_months"],
        hits=row["hits"], losses=row["losses"], missed_up=row["missed_up"],
        cash_ok=row["cash_ok"], active_win_rate=row["active_win_rate"],
        final_equity=row["final_equity"], spy_equity=row["spy_equity"],
        rows=json.loads(row["rows"]),
    )


def latest_walk_forward(strategy_id: int) -> WalkForwardRun | None:
    init_db()
    con = _conn()
    row = con.execute(
        "SELECT * FROM walk_forward_runs WHERE strategy_id = ? ORDER BY ran_at DESC LIMIT 1",
        (strategy_id,),
    ).fetchone()
    con.close()
    if not row:
        return None
    return WalkForwardRun(
        id=row["id"], strategy_id=row["strategy_id"], ran_at=row["ran_at"],
        train_months=row["train_months"], n_months=row["n_months"],
        hits=row["hits"], losses=row["losses"], missed_up=row["missed_up"],
        cash_ok=row["cash_ok"], active_win_rate=row["active_win_rate"],
        final_equity=row["final_equity"], spy_equity=row["spy_equity"],
        rows=json.loads(row["rows"]),
    )


# ── Preflight ──────────────────────────────────────────────────────────────

def save_preflight(strategy_id: int, passed: bool, checks: list[dict]) -> None:
    init_db()
    con = _conn()
    con.execute(
        "INSERT INTO preflight_checks (strategy_id, ran_at, passed, checks) VALUES (?, ?, ?, ?)",
        (strategy_id, _now_iso(), int(passed), json.dumps(checks)),
    )
    con.commit()
    con.close()
