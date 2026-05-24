// Wire types — mirror stratscout/api/schemas.py

export type StrategyKind = "etf" | "smallcap";

export interface PerfSummary {
  total_return_pct: number;
  cagr_pct: number;
  max_drawdown_pct: number;
}

export interface BacktestRequest {
  strategy_kind: StrategyKind;
  params: Record<string, unknown>;
  start: string;
  end: string;
  cash?: number;
}

export interface BacktestResponse {
  perf: PerfSummary;
  n_trades: number;
  nav_index: string[];
  nav_values: number[];
  bnh_values?: number[] | null;
}

export interface HealthResponse {
  status: string;
  version: string;
  data_dir: string;
  mode: string;
}

export interface BaselineRequest {
  symbols: string[];
  start: string;
  end: string;
  cash?: number;
}

export interface BaselineSeries {
  symbol: string;
  label: string;
  index: string[];
  values: number[];
  total_return_pct: number;
}

export interface BaselineResponse {
  baselines: BaselineSeries[];
}

// ── Data inventory ──────────────────────────────────────────────────────────

export interface SymbolCoverageRow {
  symbol: string;
  has_data: boolean;
  first_bar: string | null;
  last_bar: string | null;
  n_bars: number;
  stale: boolean;
  sufficient_for_backtest: boolean;
  sufficient_for_walk_forward: boolean;
  role: string;
}

export interface InventoryResponse {
  total: number;
  with_data: number;
  stale: number;
  sufficient_for_backtest: number;
  sufficient_for_walk_forward: number;
  earliest_bar: string | null;
  latest_bar: string | null;
  symbols: SymbolCoverageRow[];
}

// ── Window suggestions ──────────────────────────────────────────────────────

export interface SuggestFuzzWindowRequest {
  strategy_kind?: string;
  required_symbols?: string[];
  fwd_months?: number;
  min_train_months?: number;
}

export interface SuggestFuzzWindowResponse {
  available: boolean;
  train_start: string | null;
  train_end: string | null;
  fwd_start: string | null;
  fwd_end: string | null;
  train_months: number;
  fwd_months: number;
  notes: string[];
}

// ── Fuzz ────────────────────────────────────────────────────────────────────

export interface FuzzRunRequest {
  strategy_kind?: string;
  train_start: string;
  train_end: string;
  fwd_start: string;
  fwd_end: string;
  n_runs?: number;
  workers?: number;
  explore?: number;
  exclude?: string[];
  goal_id?: string;
  label?: string;
  /** When provided, the fuzzer refines around these seeds (no random exploration). */
  seed_params?: Record<string, unknown>[];
}

export interface FuzzResultRow {
  score: number;
  train_return_pct: number;
  train_cagr_pct: number;
  train_dd_pct: number;
  fwd_return_pct: number;
  fwd_cagr_pct: number;
  fwd_dd_pct: number;
  n_trades: number;
  params: Record<string, unknown>;
}

export interface FuzzRunResponse {
  strategy_kind: string;
  train_start: string;
  train_end: string;
  fwd_start: string;
  fwd_end: string;
  n_runs: number;
  completed: number;
  failed: number;
  total_results: number;
  results: FuzzResultRow[];
  run_id: number | null;
  elapsed_sec: number;
}

export interface FuzzRunMetaRow {
  id: number;
  ran_at: string;
  strategy_kind: string;
  train_start: string;
  train_end: string;
  fwd_start: string;
  fwd_end: string;
  n_runs: number;
  completed: number;
  failed: number;
  workers: number;
  explore: number;
  goal_id: string;
  exclude: string[];
  elapsed_sec: number;
  top_score: number | null;
  label: string;
}

export interface FuzzRunListResponse {
  runs: FuzzRunMetaRow[];
}

export interface FuzzRunDetailResponse {
  meta: FuzzRunMetaRow;
  results: FuzzResultRow[];
}

export interface LeaderboardEntry {
  rank: number;
  score: number;
  train_return_pct: number;
  train_cagr_pct: number;
  train_dd_pct: number;
  fwd_return_pct: number;
  fwd_cagr_pct: number;
  fwd_dd_pct: number;
  n_trades: number;
  params: Record<string, unknown>;
  run_id: number;
  ran_at: string;
}

export interface LeaderboardResponse {
  entries: LeaderboardEntry[];
}

// ── Credentials / settings ──────────────────────────────────────────────────

export interface ProviderStatus {
  id: string;
  name: string;
  description: string;
  signup_url: string | null;
  required_keys: string[];
  keys_present: Record<string, boolean>;
  all_present: boolean;
  test_message: string;
  test_ok: boolean | null;
}

export interface ProvidersResponse {
  providers: ProviderStatus[];
}

export interface PutCredentialRequest {
  provider_id: string;
  field_name: string;
  value: string;
}

export interface TestCredentialResponse {
  ok: boolean;
  message: string;
}

// ── Categorized data inventory ──────────────────────────────────────────────

export interface CategoryRow {
  kind: string;
  label: string;
  path: string;
  n_files: number;
  total_size_mb: number;
  earliest: string | null;
  latest: string | null;
  note: string;
}

export interface CategoriesResponse {
  categories: CategoryRow[];
}

// ── Data download ──────────────────────────────────────────────────────────

export interface DownloadRequest {
  symbols: string[];
  start?: string;
  end?: string | null;
  overwrite?: boolean;
}

export interface DownloadResponse {
  total: number;
  done: number;
  failed: [string, string][];
  source_used: Record<string, string>;
  log_tail: string[];
}

// ── Strategies ───────────────────────────────────────────────────────────────

export type TradeMode = "off" | "paper" | "live";

export interface StrategyRow {
  id: number;
  name: string;
  kind: string;
  params: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  trade_mode: TradeMode;
  archived: boolean;
  notes: string;
}

export interface StrategyListResponse {
  strategies: StrategyRow[];
}

export interface CreateStrategyRequest {
  name: string;
  kind: string;
  params: Record<string, unknown>;
  notes?: string;
}

export interface UpdateStrategyRequest {
  name?: string;
  params?: Record<string, unknown>;
  trade_mode?: TradeMode;
  archived?: boolean;
  notes?: string;
}

// ── Walk-forward ─────────────────────────────────────────────────────────────

export interface WalkForwardRunRequest {
  start: string;
  end: string;
  train_months?: number;
  n_trials?: number;
  workers?: number;
  exclude?: string[];
  starting_cash?: number;
  strategy_id?: number | null;
  val_weeks?: number | null;
  martingale_factor?: number;
  reserve_cash?: number;
  mm_rate_annual?: number;
  fast_mode?: boolean;
  use_optuna?: boolean;
  use_gp?: boolean;
  gp_population?: number;
  gp_generations?: number;
  ai_steer_every?: number;
}

export interface WalkForwardRowOut {
  month: string;
  spy_return_pct: number;
  train_score: number;
  val_return_pct: number;
  val_dd_pct: number;
  val_trades: number;
  verdict: "HIT" | "LOSS" | "MISSED-UP" | "cash-ok" | string;
  params: Record<string, unknown> | null;
  note?: string | null;
}

export interface WalkForwardResponse {
  train_months: number;
  n_months: number;
  hits: number;
  losses: number;
  missed_up: number;
  cash_ok: number;
  active_win_rate: number;
  final_equity: number;
  spy_equity: number;
  starting_cash: number;
  rows: WalkForwardRowOut[];
  saved_run_id: number | null;
  ran_at?: string | null;
  analysis?: {
    win_pattern?: string;
    loss_pattern?: string;
    next_steps?: string;
  } | null;
}

export interface SteeringEvent {
  type: "steering";
  exclude_added: string[];
  reason: string;
  after_month: number;
}

// ── Trade orders / dry-run ───────────────────────────────────────────────────

export interface TradeOrderRow {
  id: number;
  strategy_id: number;
  ran_at: string;
  mode: string;       // 'dry' | 'paper' | 'live'
  action: string;     // 'BUY' | 'SELL' | 'HOLD' | 'TARGET'
  symbol: string;
  qty: number | null;
  status: string;     // 'recorded' | 'submitted' | 'filled' | 'rejected'
  message: string;
  broker_order_id: string | null;
}

export interface TradeOrdersResponse {
  orders: TradeOrderRow[];
}

export interface DryRunResponse {
  strategy_id: number;
  ran_at: string;
  targets: string[];
  regime: string;
  as_of: string;
  note: string;
  order_ids: number[];
}

/** Unified response for dry/paper/live execution. */
export interface RunResponse {
  strategy_id: number;
  mode: string;          // 'dry' | 'paper' | 'live'
  ran_at: string;
  targets: string[];
  regime: string;
  as_of: string;
  note: string;
  order_ids: number[];
  placed: number;
  failed: number;
  fell_back_to_dry: boolean;
}

export interface ScheduleStatus {
  supported: boolean;             // false off Windows
  installed: boolean;
  next_run: string | null;
  last_result: string | null;
  schedule: string | null;
  run_time: string | null;
  task_name: string;
}

// ── Preflight ────────────────────────────────────────────────────────────────

export interface PreflightCheckOut {
  id: string;
  label: string;
  passed: boolean;
  hint: string;
  fix_action: string;
}

export interface PreflightResponse {
  strategy_id: number;
  passed: boolean;
  checks: PreflightCheckOut[];
}

// Strategy templates the UI shows on the onboarding screen
export interface StrategyTemplate {
  id: string;
  name: string;
  kind: StrategyKind;
  description: string;
  defaultParams: Record<string, unknown>;
  riskLabel: "Low" | "Moderate" | "High";
}

// ── Factor Lab ────────────────────────────────────────────────────────────────

export interface FactorRow {
  name: string;
  tier: number;
  description: string;
  hypothesis: string;
  has_data: boolean;
  last_date: string | null;
  current_value: number | null;
  n_months: number | null;
  ic: number | null;
  abs_ic: number | null;
  t_stat: number | null;
  p_bonferroni: number | null;
  significant: boolean | null;
  ic_train: number | null;
  ic_oos: number | null;
  ic_bull: number | null;
  ic_bear: number | null;
  ic_sideways: number | null;
}

export interface FactorsResponse {
  factors: FactorRow[];
}

export interface FactorDownloadRequest {
  tier?: number | null;
  derive?: boolean;
  n_derived?: number;
  clear_derived?: boolean;
  overwrite?: boolean;
}

export interface FactorDownloadResponse {
  written: string[];
  failed: string[];
  message: string;
}

// Comparison baselines (rendered on the NAV chart alongside the strategy)
export interface Baseline {
  id: string;
  label: string;
  symbol?: string;          // e.g. "SPY" — fetched from /baselines
  values?: number[];        // pre-computed, aligned to same index
  color?: string;
}
