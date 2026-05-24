"""Persistent fuzz-run storage.

Every /fuzz call writes a row to `fuzz_runs` and N rows to `fuzz_results`.
The Find tab uses this to:
  - Show a "Recent runs" list you can revisit
  - Surface an "All-time top" leaderboard across every run
  - Re-load the leaderboard of any past run

Shares the stratscout.db SQLite file with `strategies.py`. Schema is created
by `strategies.init_db()` which is idempotent.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from stratscout.engine.strategies import _conn, _now_iso, init_db


@dataclass
class FuzzRunMeta:
    id: int
    ran_at: str
    strategy_kind: str
    train_start: str
    train_end: str
    fwd_start: str
    fwd_end: str
    n_runs: int
    completed: int
    failed: int
    workers: int
    explore: float
    goal_id: str
    exclude: list[str]
    elapsed_sec: float
    top_score: float | None
    label: str


@dataclass
class FuzzResultRow:
    rank: int
    score: float
    train_return_pct: float
    train_cagr_pct: float
    train_dd_pct: float
    fwd_return_pct: float
    fwd_cagr_pct: float
    fwd_dd_pct: float
    n_trades: int
    params: dict[str, Any]
    run_id: int = 0
    ran_at: str = ""    # populated by global-leaderboard query


def save_run(
    *,
    strategy_kind: str,
    train_start: str,
    train_end: str,
    fwd_start: str,
    fwd_end: str,
    n_runs: int,
    completed: int,
    failed: int,
    workers: int,
    explore: float,
    goal_id: str,
    exclude: list[str],
    elapsed_sec: float,
    results: list[dict],
    label: str = "",
) -> FuzzRunMeta:
    """Persist a fuzz run and all its result rows. Returns the run metadata."""
    init_db()
    top = max((r["score"] for r in results), default=None)
    con = _conn()
    cur = con.execute(
        """
        INSERT INTO fuzz_runs
          (ran_at, strategy_kind, train_start, train_end, fwd_start, fwd_end,
           n_runs, completed, failed, workers, explore, goal_id, exclude,
           elapsed_sec, top_score, label)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _now_iso(), strategy_kind, train_start, train_end, fwd_start, fwd_end,
            n_runs, completed, failed, workers, explore, goal_id,
            json.dumps(exclude), elapsed_sec, top, label,
        ),
    )
    run_id = cur.lastrowid

    if results:
        # Pre-sort and assign rank
        ordered = sorted(results, key=lambda r: r["score"], reverse=True)
        con.executemany(
            """
            INSERT INTO fuzz_results
              (run_id, rank, score, train_return_pct, train_cagr_pct, train_dd_pct,
               fwd_return_pct, fwd_cagr_pct, fwd_dd_pct, n_trades, params)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id, i + 1,
                    float(r["score"]),
                    float(r["train_return_pct"]),
                    float(r["train_cagr_pct"]),
                    float(r["train_dd_pct"]),
                    float(r["fwd_return_pct"]),
                    float(r["fwd_cagr_pct"]),
                    float(r["fwd_dd_pct"]),
                    int(r["n_trades"]),
                    json.dumps(r["params"], default=str),
                )
                for i, r in enumerate(ordered)
            ],
        )
    con.commit()
    row = con.execute("SELECT * FROM fuzz_runs WHERE id = ?", (run_id,)).fetchone()
    con.close()
    return _meta_from_row(row)


def list_runs(limit: int = 30) -> list[FuzzRunMeta]:
    init_db()
    con = _conn()
    rows = con.execute(
        "SELECT * FROM fuzz_runs ORDER BY ran_at DESC LIMIT ?", (limit,),
    ).fetchall()
    con.close()
    return [_meta_from_row(r) for r in rows]


def get_run(run_id: int) -> FuzzRunMeta | None:
    init_db()
    con = _conn()
    row = con.execute("SELECT * FROM fuzz_runs WHERE id = ?", (run_id,)).fetchone()
    con.close()
    return _meta_from_row(row) if row else None


def get_results(run_id: int, limit: int | None = None) -> list[FuzzResultRow]:
    init_db()
    con = _conn()
    if limit:
        rows = con.execute(
            "SELECT * FROM fuzz_results WHERE run_id = ? ORDER BY rank ASC LIMIT ?",
            (run_id, limit),
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM fuzz_results WHERE run_id = ? ORDER BY rank ASC",
            (run_id,),
        ).fetchall()
    con.close()
    return [_result_from_row(r) for r in rows]


def all_time_leaderboard(
    limit: int = 50,
    strategy_kind: str = "etf",
) -> list[FuzzResultRow]:
    """Top-N results across every saved fuzz run."""
    init_db()
    con = _conn()
    rows = con.execute(
        """
        SELECT fr.*, runs.ran_at AS run_ran_at, runs.strategy_kind AS run_kind
        FROM fuzz_results fr
        JOIN fuzz_runs runs ON fr.run_id = runs.id
        WHERE runs.strategy_kind = ?
        ORDER BY fr.score DESC
        LIMIT ?
        """,
        (strategy_kind, limit),
    ).fetchall()
    con.close()
    out: list[FuzzResultRow] = []
    for r in rows:
        row = _result_from_row(r)
        row.ran_at = r["run_ran_at"]
        out.append(row)
    return out


def delete_run(run_id: int) -> bool:
    init_db()
    con = _conn()
    cur = con.execute("DELETE FROM fuzz_runs WHERE id = ?", (run_id,))
    con.commit()
    deleted = cur.rowcount > 0
    con.close()
    return deleted


def relabel_run(run_id: int, label: str) -> FuzzRunMeta | None:
    init_db()
    con = _conn()
    con.execute("UPDATE fuzz_runs SET label = ? WHERE id = ?", (label, run_id))
    con.commit()
    con.close()
    return get_run(run_id)


def _meta_from_row(row) -> FuzzRunMeta:
    return FuzzRunMeta(
        id=row["id"], ran_at=row["ran_at"],
        strategy_kind=row["strategy_kind"],
        train_start=row["train_start"], train_end=row["train_end"],
        fwd_start=row["fwd_start"], fwd_end=row["fwd_end"],
        n_runs=row["n_runs"], completed=row["completed"], failed=row["failed"],
        workers=row["workers"], explore=row["explore"],
        goal_id=row["goal_id"],
        exclude=json.loads(row["exclude"] or "[]"),
        elapsed_sec=row["elapsed_sec"],
        top_score=row["top_score"],
        label=row["label"] or "",
    )


def _result_from_row(row) -> FuzzResultRow:
    return FuzzResultRow(
        rank=row["rank"],
        score=row["score"],
        train_return_pct=row["train_return_pct"],
        train_cagr_pct=row["train_cagr_pct"],
        train_dd_pct=row["train_dd_pct"],
        fwd_return_pct=row["fwd_return_pct"],
        fwd_cagr_pct=row["fwd_cagr_pct"],
        fwd_dd_pct=row["fwd_dd_pct"],
        n_trades=row["n_trades"],
        params=json.loads(row["params"]),
        run_id=row["run_id"],
    )
