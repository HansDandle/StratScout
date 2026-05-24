// Per-tab help content. Plain language — no jargon if avoidable.
// The HelpDrawer in App.tsx renders these as headed sections.

export interface HelpSection {
  heading: string;
  body: string;
}

export interface HelpTopic {
  title: string;
  intro: string;
  sections: HelpSection[];
}

const ANALYZE: HelpTopic = {
  title: "Analyze",
  intro:
    "Run a single backtest of the selected strategy against the chosen date window. The result is one equity curve you can stack against SPY / QQQ / TLT / GLD to see whether the strategy beats simple buy-and-hold.",
  sections: [
    {
      heading: "Backtest window",
      body:
        "Pick a start and end date. The preset buttons cover 1Y / 3Y / 5Y / Since-2020. A 3-year window with daily bars is enough to see most regimes; less than a year is unreliable.",
    },
    {
      heading: "Parameters",
      body:
        "Every strategy has knobs (lookbacks, position counts, pool symbols). The Parameters card shows the current effective set — defaults when you first pick a template, or whatever you loaded from a Find leaderboard row. Hover each label for a one-line explanation.",
    },
    {
      heading: "Baselines",
      body:
        "The chip selectors fetch buy-and-hold curves for common benchmarks. ‘Pool buy & hold’ is the strategy's own basket held continuously — a fair apples-to-apples comparison.",
    },
    {
      heading: "Save strategy",
      body:
        "Saves the current params under a name. Saved strategies show up in Live and can be paper- or live-traded. ‘Save copy’ keeps the original, ‘Update’ overwrites it.",
    },
  ],
};

const FIND: HelpTopic = {
  title: "Find",
  intro:
    "Search the parameter space for better configurations than the template defaults. Each trial scores on train + forward windows. Click any leaderboard row to load those params into Analyze.",
  sections: [
    {
      heading: "Goal presets",
      body:
        "Steady / Balanced / Aggressive each set a different exclude-list (no 3× ETFs in Steady, all leveraged allowed in Aggressive), explore ratio, and default filters. Custom unlocks every knob.",
    },
    {
      heading: "Train / forward split",
      body:
        "The fuzzer optimizes against the train window. The forward window is held out — the score on it tells you whether the params generalize or are curve-fit. Wider train + 6–12 month forward is a reasonable default.",
    },
    {
      heading: "Filters",
      body:
        "Filters are post-hoc and client-side: adjust them at any time, no re-fuzz needed. Max drawdown takes a positive magnitude (40 means ‘hide rows worse than -40%’). If filters hide everything, the diagnostics banner shows which filter is the culprit.",
    },
    {
      heading: "Saved runs",
      body:
        "Every fuzz session is persisted to SQLite. The history panel lists every run you've done — click one to reload its leaderboard. ‘All-time top’ aggregates the best results across every run.",
    },
  ],
};

const WALKFORWARD: HelpTopic = {
  title: "Walk-forward",
  intro:
    "The honest out-of-sample test. For each month in the validation window the fuzzer trains on the prior N months and paper-trades that month with the best-scoring params. The aggregate hit rate is your evidence the strategy is real (not just curve-fit).",
  sections: [
    {
      heading: "Train months (rolling)",
      body:
        "How many months of history to fuzz against for each validation month. 12 is a reasonable default — long enough to span a couple of regime shifts, short enough to keep params relevant.",
    },
    {
      heading: "Trials per month",
      body:
        "How many parameter combinations the fuzzer tries for each month. More = more thorough but slower. 150–300 is a sweet spot.",
    },
    {
      heading: "Active win rate",
      body:
        "Wins divided by traded months (ignores cash-only months). 60%+ is good, 50% is the preflight gate. Strategies that sit in cash during bear markets correctly show up as ‘cash-ok’, not losses.",
    },
    {
      heading: "Equity curve",
      body:
        "Top chart: your strategy's compounded equity vs SPY buy-and-hold over the same months. If the strategy line stays below SPY, you'd have been better off just holding SPY.",
    },
  ],
};

const LIVE: HelpTopic = {
  title: "Live trading",
  intro:
    "Activate saved strategies for Paper or Live trading. Live is gated by preflight checks — you can't go live on a strategy that hasn't been walk-forward validated.",
  sections: [
    {
      heading: "Trade modes",
      body:
        "Off = no trades. Paper = simulated against your broker's paper account (free, no real money). Live = real money. Switching is instantaneous.",
    },
    {
      heading: "Preflight checklist",
      body:
        "Walk-forward must have run with active win rate ≥ 50% over ≥ 12 traded months, worst monthly drawdown ≤ 35%, and you must acknowledge the risk disclosure. Each failing check has a ‘Fix’ button that takes you to the right place.",
    },
    {
      heading: "Strategy detail",
      body:
        "Click any strategy in the list to see its current params, latest walk-forward summary + equity curve, and an editable notes field. Notes save when you click out of the field.",
    },
    {
      heading: "Archived",
      body:
        "Archived strategies are hidden by default — toggle ‘Show archived’ to surface them. Archive instead of delete to keep a graveyard of explored strategies without cluttering the list.",
    },
  ],
};

const SETTINGS: HelpTopic = {
  title: "Settings",
  intro:
    "Manage your data and broker connections. All keys are stored in your OS keychain (Windows Credential Locker / macOS Keychain) — never in plain text.",
  sections: [
    {
      heading: "Your data",
      body:
        "Five rows by data type: daily, intraday, smallcap, options, options-theta. Each row shows how many feather files you have on disk, total size, and date span. The ‘Download core ETFs’ button at the top fetches a curated ~12-symbol set via Alpaca → yfinance fallback.",
    },
    {
      heading: "Brokers",
      body:
        "Alpaca uses API key + secret (free paper accounts). Schwab requires a one-time OAuth flow (use the legacy schwab_auth.py script for now). Polygon and ThetaData are optional data providers.",
    },
    {
      heading: "Testing connections",
      body:
        "The ‘Test connection’ button actually hits the provider's API and reports the response. If it says ‘Connected — paper account #PA12345’, your keys are working.",
    },
  ],
};

const ONBOARDING: HelpTopic = {
  title: "Pick a strategy",
  intro:
    "Three starting templates to launch from. These are reasonable starting points, not magic formulas — Find is where you search for actually-good configurations.",
  sections: [
    {
      heading: "ETF Rotator (Balanced)",
      body:
        "Regime-aware rotation between leveraged ETFs (SOXL, TQQQ, UPRO) and defensives (TLT, GLD) based on the AGG vs BIL signal. Moderate risk; aims to be in 3× ETFs during bull markets and gold/treasuries during corrections.",
    },
    {
      heading: "ETF Rotator (Defensive)",
      body:
        "Same regime logic, but the bull side is SPY/QQQ (no leverage). Lower upside, much smaller drawdowns. A good first strategy to validate the framework before adding leverage.",
    },
    {
      heading: "Small Cap Volume Anomaly",
      body:
        "Scans ~500 small caps daily for volume surges (5× the 20-day average). Buys at next open, holds 5 days, equal-weight up to 4 positions. High risk, high turnover.",
    },
  ],
};

export const HELP_TOPICS: Record<string, HelpTopic> = {
  onboarding: ONBOARDING,
  analyze: ANALYZE,
  find: FIND,
  walkforward: WALKFORWARD,
  live: LIVE,
  settings: SETTINGS,
};
