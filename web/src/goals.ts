// Search goals — UI-level presets that bias the fuzzer and filter results.
//
// Each goal sets:
//   - exclude: symbols the fuzzer should NOT pick (e.g. 3× leverage for "Steady")
//   - explore: random-vs-refine ratio
//   - filters: client-side leaderboard filters applied after the run
//
// The user can override any individual setting; goal presets are starting points.

export interface SearchGoal {
  id: string;
  name: string;
  description: string;
  exclude: string[];
  explore: number;
  filters: {
    minTrainCAGR: number;
    minFwdCAGR: number;
    maxWorstDD: number;   // e.g. -25 means drop runs with any window DD < -25%
    minTrades: number;
  };
}

const ALL_LEVERAGED_3X = [
  "SOXL", "TQQQ", "UPRO", "TECL", "SPXL", "FAS", "CURE", "LABU",
  "DRN", "FNGU", "UTSL", "MIDU", "TNA",
  "JNUG", "GDXU",
  "TMF", "TBF", "SQQQ",
];

const ALL_CRYPTO = ["MSTR", "GBTC", "BITX", "CONL", "IBIT"];

export const GOALS: SearchGoal[] = [
  {
    id: "steady",
    name: "Steady",
    description:
      "Lower-volatility candidates. Excludes 3× leveraged ETFs and crypto. Targets reasonable CAGR with shallow drawdowns.",
    exclude: [...ALL_LEVERAGED_3X, ...ALL_CRYPTO],
    explore: 0.7,
    filters: {
      minTrainCAGR: 10,
      minFwdCAGR: 5,
      maxWorstDD: -25,
      minTrades: 1,
    },
  },
  {
    id: "balanced",
    name: "Balanced",
    description:
      "Allows 3× ETFs but excludes crypto. Aims for solid CAGR while keeping drawdowns tolerable.",
    exclude: [...ALL_CRYPTO],
    explore: 0.6,
    filters: {
      minTrainCAGR: 20,
      minFwdCAGR: 10,
      maxWorstDD: -40,
      minTrades: 1,
    },
  },
  {
    id: "aggressive",
    name: "Aggressive",
    description:
      "Full universe including 3× leverage + crypto proxies. Big upside, big drawdowns. Lottery-ticket territory.",
    exclude: [],
    explore: 0.55,
    filters: {
      minTrainCAGR: 40,
      minFwdCAGR: 20,
      maxWorstDD: -60,
      minTrades: 1,
    },
  },
  {
    id: "custom",
    name: "Custom",
    description:
      "No presets — set your own filters and let the fuzzer roam the full universe.",
    exclude: [],
    explore: 0.6,
    filters: {
      minTrainCAGR: -100,
      minFwdCAGR: -100,
      maxWorstDD: -100,
      minTrades: 0,
    },
  },
];

export const findGoal = (id: string) =>
  GOALS.find((g) => g.id === id) ?? GOALS[1]; // default to Balanced

/** Worst-window drawdown across train + forward. Negative number. */
export function worstDd(row: { train_dd_pct: number; fwd_dd_pct: number }): number {
  return Math.min(row.train_dd_pct, row.fwd_dd_pct);
}

/** Does a leaderboard row pass the active filters? */
export function rowPassesFilters(
  row: {
    train_cagr_pct: number;
    fwd_cagr_pct: number;
    train_dd_pct: number;
    fwd_dd_pct: number;
    n_trades: number;
  },
  f: SearchGoal["filters"],
): boolean {
  if (row.train_cagr_pct < f.minTrainCAGR) return false;
  if (row.fwd_cagr_pct < f.minFwdCAGR) return false;
  if (worstDd(row) < f.maxWorstDD) return false;
  if (row.n_trades < f.minTrades) return false;
  return true;
}
