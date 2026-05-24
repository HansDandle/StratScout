"""Pydantic schemas for the StratScout HTTP API.

These are the wire formats. Domain types (Position, Account, etc.) live in
stratscout.engine.brokers.base and are mapped to/from these schemas at the
API boundary.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class BacktestRequest(BaseModel):
    strategy_kind: str = Field(..., description="'etf' | 'smallcap'")
    params: dict[str, Any]
    start: str
    end: str
    cash: float = 10_000.0


class PerfSummary(BaseModel):
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float


class BacktestResponse(BaseModel):
    perf: PerfSummary
    n_trades: int
    nav_index: list[str]
    nav_values: list[float]
    bnh_values: list[float] | None = None


class FuzzRequest(BaseModel):
    strategy_kind: str = Field(..., description="'etf' | 'smallcap'")
    n_runs: int = 200
    workers: int = 4
    explore: float = 0.6
    start: str
    end: str
    exclude: list[str] = []


class JobStatus(BaseModel):
    job_id: str
    state: str  # 'queued' | 'running' | 'completed' | 'failed'
    progress: float = 0.0
    message: str = ""


class StrategyRow(BaseModel):
    """One row from the fuzzer results table — what the leaderboard renders."""
    id: int
    score: float
    train_return_pct: float
    fwd_return_pct: float | None = None
    max_drawdown_pct: float
    n_trades: int
    params: dict[str, Any]


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    data_dir: str
    mode: str  # 'desktop' | 'web-api' | 'serial'


class BaselineRequest(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["SPY"])
    start: str
    end: str
    cash: float = 10_000.0


class BaselineSeries(BaseModel):
    symbol: str
    label: str
    index: list[str]
    values: list[float]
    total_return_pct: float


class BaselineResponse(BaseModel):
    baselines: list[BaselineSeries]


# ── Data inventory ────────────────────────────────────────────────────────────

class SymbolCoverageRow(BaseModel):
    symbol: str
    has_data: bool
    first_bar: str | None = None
    last_bar: str | None = None
    n_bars: int = 0
    stale: bool = False
    sufficient_for_backtest: bool = False
    sufficient_for_walk_forward: bool = False
    role: str = ""


class InventoryResponse(BaseModel):
    total: int
    with_data: int
    stale: int
    sufficient_for_backtest: int
    sufficient_for_walk_forward: int
    earliest_bar: str | None
    latest_bar: str | None
    symbols: list[SymbolCoverageRow]


# ── Window suggestions ────────────────────────────────────────────────────────

class SuggestFuzzWindowRequest(BaseModel):
    strategy_kind: str = "etf"
    required_symbols: list[str] | None = None
    fwd_months: int = 12
    min_train_months: int = 24


class SuggestFuzzWindowResponse(BaseModel):
    available: bool
    train_start: str | None = None
    train_end: str | None = None
    fwd_start: str | None = None
    fwd_end: str | None = None
    train_months: int = 0
    fwd_months: int = 0
    notes: list[str] = Field(default_factory=list)


class SuggestWalkForwardRequest(BaseModel):
    strategy_kind: str = "etf"
    required_symbols: list[str] | None = None
    target_train_months: int = 12
    target_validation_months: int = 24


class SuggestWalkForwardResponse(BaseModel):
    available: bool
    train_months: int = 0
    validation_start: str | None = None
    validation_end: str | None = None
    n_validation_months: int = 0
    notes: list[str] = Field(default_factory=list)


# ── Fuzz session ──────────────────────────────────────────────────────────────

class FuzzRunRequest(BaseModel):
    strategy_kind: str = "etf"
    train_start: str
    train_end: str
    fwd_start: str
    fwd_end: str
    n_runs: int = 200
    workers: int = 4
    explore: float = 0.6
    exclude: list[str] = Field(default_factory=list)
    goal_id: str = ""           # for persistence/audit; doesn't affect engine
    label: str = ""
    # When non-empty, the fuzzer runs in pure-refine mode around these seeds.
    seed_params: list[dict[str, Any]] = Field(default_factory=list)


class FuzzResultRow(BaseModel):
    score: float
    train_return_pct: float
    train_cagr_pct: float
    train_dd_pct: float
    fwd_return_pct: float
    fwd_cagr_pct: float
    fwd_dd_pct: float
    n_trades: int
    params: dict[str, Any]


class FuzzRunResponse(BaseModel):
    strategy_kind: str
    train_start: str
    train_end: str
    fwd_start: str
    fwd_end: str
    n_runs: int
    completed: int
    failed: int
    total_results: int
    results: list[FuzzResultRow]
    run_id: int | None = None
    elapsed_sec: float = 0.0


class FuzzRunMetaRow(BaseModel):
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


class FuzzRunListResponse(BaseModel):
    runs: list[FuzzRunMetaRow]


class FuzzRunDetailResponse(BaseModel):
    meta: FuzzRunMetaRow
    results: list[FuzzResultRow]


class LeaderboardEntry(BaseModel):
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
    run_id: int
    ran_at: str


class LeaderboardResponse(BaseModel):
    entries: list[LeaderboardEntry]


class RelabelFuzzRunRequest(BaseModel):
    label: str


# ── Credentials / settings ────────────────────────────────────────────────────

class ProviderStatus(BaseModel):
    id: str
    name: str
    description: str
    signup_url: str | None
    required_keys: list[str]
    keys_present: dict[str, bool]   # which fields have values (never expose values)
    all_present: bool
    test_message: str = ""
    test_ok: bool | None = None


class ProvidersResponse(BaseModel):
    providers: list[ProviderStatus]


class PutCredentialRequest(BaseModel):
    provider_id: str
    field_name: str
    value: str


class TestCredentialResponse(BaseModel):
    ok: bool
    message: str


# ── Categorized data inventory ────────────────────────────────────────────────

class CategoryRow(BaseModel):
    kind: str
    label: str
    path: str
    n_files: int
    total_size_mb: float
    earliest: str | None
    latest: str | None
    note: str


class CategoriesResponse(BaseModel):
    categories: list[CategoryRow]


# ── Data download ─────────────────────────────────────────────────────────────

class DownloadRequest(BaseModel):
    symbols: list[str]
    start: str = "2018-01-01"
    end: str | None = None
    overwrite: bool = False


class DownloadResponse(BaseModel):
    total: int
    done: int
    failed: list[tuple[str, str]]
    source_used: dict[str, str]
    log_tail: list[str]


# ── Strategies (save / load / trade-mode) ────────────────────────────────────

class StrategyRow(BaseModel):
    id: int
    name: str
    kind: str
    params: dict[str, Any]
    created_at: str
    updated_at: str
    trade_mode: str
    archived: bool
    notes: str


class StrategyListResponse(BaseModel):
    strategies: list[StrategyRow]


class CreateStrategyRequest(BaseModel):
    name: str
    kind: str
    params: dict[str, Any]
    notes: str = ""


class UpdateStrategyRequest(BaseModel):
    name: str | None = None
    params: dict[str, Any] | None = None
    trade_mode: str | None = None
    archived: bool | None = None
    notes: str | None = None


# ── Walk-forward ─────────────────────────────────────────────────────────────

class WalkForwardRunRequest(BaseModel):
    start: str
    end: str
    train_months: int = 12
    n_trials: int = 200
    workers: int = 4
    exclude: list[str] = Field(default_factory=list)
    starting_cash: float = 10_000.0
    strategy_id: int | None = None   # if set, save the result against this strategy
    val_weeks: int | None = None          # None=monthly, 2=biweekly
    martingale_factor: float = 1.0        # 1.0=off, >1 scales position after losses
    reserve_cash: float = 0.0             # cash held in money market alongside deployed capital
    mm_rate_annual: float = 0.045         # annual money market yield on reserve
    fast_mode: bool = False               # single training window (3× faster, slight quality tradeoff)
    use_optuna: bool = False              # Bayesian TPE sampler (~3× fewer trials needed)
    use_gp: bool = False                  # GP evolution engine (discovers if/else logic automatically)
    gp_population: int = 100             # strategies per generation
    gp_generations: int = 30             # evolution generations per month
    ai_steer_every: int = 5              # call Claude for steering every N completed months (0=off)


class WalkForwardRowOut(BaseModel):
    month: str
    spy_return_pct: float
    train_score: float
    val_return_pct: float
    val_dd_pct: float
    val_trades: int
    verdict: str
    params: dict[str, Any] | None = None
    note: str | None = None


class WalkForwardResponse(BaseModel):
    train_months: int
    n_months: int
    hits: int
    losses: int
    missed_up: int
    cash_ok: int
    active_win_rate: float
    final_equity: float
    spy_equity: float
    starting_cash: float
    rows: list[WalkForwardRowOut]
    saved_run_id: int | None = None
    ran_at: str | None = None        # when this WF was saved (for the detail panel)
    analysis: dict[str, Any] | None = None


# ── Trade orders / dry-run ────────────────────────────────────────────────────

class TradeOrderRow(BaseModel):
    id: int
    strategy_id: int
    ran_at: str
    mode: str            # 'dry' | 'paper' | 'live'
    action: str          # 'BUY' | 'SELL' | 'HOLD' | 'TARGET'
    symbol: str
    qty: int | None = None
    status: str          # 'recorded' | 'submitted' | 'filled' | 'rejected'
    message: str = ""
    broker_order_id: str | None = None


class TradeOrdersResponse(BaseModel):
    orders: list[TradeOrderRow]


class DryRunResponse(BaseModel):
    strategy_id: int
    ran_at: str
    targets: list[str]
    regime: str
    as_of: str
    note: str = ""
    order_ids: list[int]


class RunResponse(BaseModel):
    """Unified response for dry-run / paper / live execution."""
    strategy_id: int
    mode: str                     # 'dry' | 'paper' | 'live'
    ran_at: str
    targets: list[str]
    regime: str
    as_of: str
    note: str = ""
    order_ids: list[int]
    placed: int = 0               # successful broker submissions (0 for dry)
    failed: int = 0
    fell_back_to_dry: bool = False  # paper/live ran without broker creds


# ── Daily schedule (Windows Task Scheduler today) ─────────────────────────────

class ScheduleStatus(BaseModel):
    supported: bool                 # False on non-Windows
    installed: bool
    next_run: str | None = None
    last_result: str | None = None
    schedule: str | None = None
    run_time: str | None = None
    task_name: str = ""


class InstallScheduleRequest(BaseModel):
    run_time: str = "09:35"        # 24-hour HH:MM local time


# ── Preflight ────────────────────────────────────────────────────────────────

class PreflightCheckOut(BaseModel):
    id: str
    label: str
    passed: bool
    hint: str
    fix_action: str = ""


class PreflightResponse(BaseModel):
    strategy_id: int
    passed: bool
    checks: list[PreflightCheckOut]


# ── Factor Lab ────────────────────────────────────────────────────────────────

class FactorRow(BaseModel):
    name: str
    tier: int
    description: str
    hypothesis: str
    has_data: bool
    last_date: str | None = None
    current_value: float | None = None
    n_months: int | None = None
    ic: float | None = None
    abs_ic: float | None = None
    t_stat: float | None = None
    p_bonferroni: float | None = None
    significant: bool | None = None
    ic_train: float | None = None
    ic_oos: float | None = None
    ic_bull: float | None = None
    ic_bear: float | None = None
    ic_sideways: float | None = None


class FactorsResponse(BaseModel):
    factors: list[FactorRow]


class FactorDownloadRequest(BaseModel):
    tier: int | None = None          # 1 = calculable only, 2 = API only, None = all
    derive: bool = False             # also generate derived combinations
    n_derived: int = 200             # how many derived factors to generate
    clear_derived: bool = False      # wipe existing derived factors first
    overwrite: bool = False


class FactorDownloadResponse(BaseModel):
    written: list[str]
    failed: list[str]
    message: str
