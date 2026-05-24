// One-line explanations for every parameter the templates expose.
// Used by the Analyze "Parameters" card and the Find leaderboard hover tips.
// Keep these short and novice-friendly — link to the README for the deep dive.

export interface ParamHint {
  /** Display name (sentence case) */
  label: string;
  /** Short hover blurb. Plain language, no jargon if avoidable. */
  hint: string;
  /** Strategy this param belongs to (for filtering, if needed). */
  kind?: "etf" | "smallcap" | "any";
}

export const PARAM_HINTS: Record<string, ParamHint> = {
  // ─ ETF regime signal ─────────────────────────────────────────────────────
  agg_bil_lookback: {
    label: "AGG/BIL lookback (days)",
    hint:
      "Compares the bond aggregate (AGG) to cash (BIL) over this many days. When AGG outperforms BIL, the regime is risk-on.",
    kind: "etf",
  },
  tlt_bil_lookback: {
    label: "TLT/BIL lookback (days)",
    hint:
      "Compares long-duration treasuries (TLT) to cash (BIL). When rising, the defensive sub-regime favors gold/inverse over treasuries.",
    kind: "etf",
  },
  // ─ ETF selection ─────────────────────────────────────────────────────────
  risk_on_rsi_window: {
    label: "Risk-on RSI window",
    hint: "RSI lookback used to rank risk-on candidates (leveraged ETFs).",
    kind: "etf",
  },
  risk_off_rsi_window: {
    label: "Risk-off RSI window",
    hint: "RSI lookback used to rank risk-off candidates (defensives).",
    kind: "etf",
  },
  risk_on_rsi_direction: {
    label: "Risk-on RSI direction",
    hint: "‘lowest’ buys oversold names; ‘highest’ buys momentum names.",
    kind: "etf",
  },
  risk_off_rsi_direction: {
    label: "Risk-off RSI direction",
    hint: "‘lowest’ buys oversold defensives; ‘highest’ buys momentum defensives.",
    kind: "etf",
  },
  n_risk_on: {
    label: "Risk-on positions",
    hint: "How many leveraged ETFs to hold simultaneously when risk-on.",
    kind: "etf",
  },
  n_risk_off_rising: {
    label: "Risk-off (rising rate) positions",
    hint: "How many short/inverse ETFs to hold when rates are rising.",
    kind: "etf",
  },
  n_risk_off_falling: {
    label: "Risk-off (falling rate) positions",
    hint: "How many defensive ETFs (gold, treasuries, staples) to hold when rates are falling.",
    kind: "etf",
  },
  min_hold_days: {
    label: "Min hold days",
    hint: "Don't sell a position until it's been held this many trading days. Limits whipsaw.",
    kind: "etf",
  },
  rising_rate_include_uup: {
    label: "Include UUP on rising rates",
    hint: "Adds the dollar bull ETF (UUP) to the rising-rate defensive pool.",
    kind: "etf",
  },
  sector_diverse: {
    label: "Force sector diversity",
    hint: "Disallows picking two ETFs from the same sector in the risk-on slot.",
    kind: "etf",
  },
  // ─ ETF pools ─────────────────────────────────────────────────────────────
  risk_on_pool: {
    label: "Risk-on pool",
    hint: "Symbols eligible for the leveraged side. Add/remove tickers below.",
    kind: "etf",
  },
  risk_off_rising_pool: {
    label: "Risk-off pool (rising rates)",
    hint: "Symbols eligible when rates are rising — usually short/inverse ETFs.",
    kind: "etf",
  },
  risk_off_falling_pool: {
    label: "Risk-off pool (falling rates)",
    hint: "Symbols eligible when rates are falling — usually gold, treasuries, staples.",
    kind: "etf",
  },
  // ─ Smallcap volume-anomaly ───────────────────────────────────────────────
  vol_lookback: {
    label: "Volume lookback (days)",
    hint: "Window for computing the rolling average volume baseline.",
    kind: "smallcap",
  },
  vol_mult: {
    label: "Volume multiplier",
    hint: "Trigger when today's volume exceeds N× the average. Higher = stricter signal.",
    kind: "smallcap",
  },
  hold_days: {
    label: "Hold days",
    hint: "How many trading days to hold each entry before exiting.",
    kind: "smallcap",
  },
  max_positions: {
    label: "Max positions",
    hint: "Cap on concurrent small-cap holdings.",
    kind: "smallcap",
  },
  require_green: {
    label: "Require green day",
    hint: "Only enter if the trigger day closed above its open.",
    kind: "smallcap",
  },
};

/** Get a label for an unknown param key — fallback humanizer. */
export function paramLabel(key: string): string {
  return PARAM_HINTS[key]?.label ?? humanize(key);
}

/** Get the hover hint, or empty string if unknown. */
export function paramHint(key: string): string {
  return PARAM_HINTS[key]?.hint ?? "";
}

function humanize(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
