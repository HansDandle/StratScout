// Strategy templates shown on the onboarding screen.
// These are starting points — users tune from here.
// Defaults intentionally conservative; the "moderate" knob is the user's tuning room.

import type { StrategyTemplate } from "./types";

export const TEMPLATES: StrategyTemplate[] = [
  {
    id: "etf-rotator-balanced",
    name: "ETF Rotator (Balanced)",
    kind: "etf",
    riskLabel: "Moderate",
    description:
      "Regime-aware rotation between leveraged ETFs (SOXL, TQQQ, UPRO) and defensives (TLT, GLD) based on AGG/BIL signal. Holds 2 positions, rebalances daily.",
    defaultParams: {
      agg_bil_lookback: 60,
      tlt_bil_lookback: 20,
      risk_on_rsi_window: 10,
      risk_off_rsi_window: 20,
      risk_on_rsi_direction: "lowest",
      risk_off_rsi_direction: "lowest",
      n_risk_on: 2,
      n_risk_off_rising: 2,
      n_risk_off_falling: 2,
      min_hold_days: 2,
      rising_rate_include_uup: true,
      sector_diverse: true,
      risk_on_pool: ["SOXL", "TQQQ", "UPRO", "TECL"],
      risk_off_rising_pool: ["QID", "TBF"],
      risk_off_falling_pool: ["UGL", "TMF", "BTAL", "XLP", "GLD"],
    },
  },
  {
    id: "etf-rotator-defensive",
    name: "ETF Rotator (Defensive)",
    kind: "etf",
    riskLabel: "Low",
    description:
      "Same regime logic, but the bull side is SPY/QQQ (no leverage). Lower upside, much smaller drawdowns. Good first strategy.",
    defaultParams: {
      agg_bil_lookback: 60,
      tlt_bil_lookback: 20,
      risk_on_rsi_window: 10,
      risk_off_rsi_window: 20,
      risk_on_rsi_direction: "lowest",
      risk_off_rsi_direction: "lowest",
      n_risk_on: 1,
      n_risk_off_rising: 1,
      n_risk_off_falling: 1,
      min_hold_days: 5,
      rising_rate_include_uup: false,
      sector_diverse: false,
      risk_on_pool: ["SPY", "QQQ"],
      risk_off_rising_pool: ["TBF"],
      risk_off_falling_pool: ["TLT", "GLD"],
    },
  },
  {
    id: "smallcap-momentum",
    name: "Small Cap Volume Anomaly",
    kind: "smallcap",
    riskLabel: "High",
    description:
      "Scan ~500 small-caps daily for volume surges (5× 20-day avg). Buy at next open, hold 5 days, equal-weight up to 4 positions.",
    defaultParams: {
      vol_lookback: 20,
      vol_mult: 5.0,
      hold_days: 5,
      max_positions: 4,
      require_green: false,
    },
  },
];

export const findTemplate = (id: string) =>
  TEMPLATES.find((t) => t.id === id) ?? TEMPLATES[0];

/** All symbols a template's strategy can touch — for window suggestion + data checks. */
export function templateSymbols(t: StrategyTemplate): string[] {
  const ANCHORS = ["AGG", "BIL", "TLT"];
  const out = new Set<string>(ANCHORS);
  const p = t.defaultParams;
  for (const key of ["risk_on_pool", "risk_off_rising_pool", "risk_off_falling_pool"]) {
    const v = p[key];
    if (Array.isArray(v)) for (const s of v) out.add(String(s).toUpperCase());
  }
  return Array.from(out);
}
