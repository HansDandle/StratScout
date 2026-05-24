# StratScout

A workbench for finding algorithmic trading strategies, validating them honestly, and putting them on autopilot — all on your own machine, with your own data and API keys.

The audience is a relatively novice quant: someone comfortable with Python and finance fundamentals but not necessarily writing custom fuzzers or backtest engines from scratch. Bring your data and broker keys, pick a starting template, find a configuration that survives walk-forward validation, and put it on paper or live trade.

The legacy CLIs in the parent directory (`etf_dashboard.py`, `walk_forward.py`, `live_trader.py`, etc.) still work and are unchanged — StratScout is being built alongside them.

---

## Table of contents

- [The end-to-end flow](#the-end-to-end-flow)
- [Tab-by-tab guide](#tab-by-tab-guide)
  - [Onboarding](#1-onboarding-pick-a-starting-strategy)
  - [Settings](#2-settings-keys--data)
  - [Analyze](#3-analyze-a-single-backtest--baselines)
  - [Find](#4-find-fuzz-for-better-parameters)
  - [Walk-forward](#5-walk-forward-honest-out-of-sample-validation)
  - [Live](#6-live-trade-mode--preflight-gating)
- [Key concepts explained](#key-concepts-explained)
- [Data model](#data-model)
- [Architecture](#architecture)
- [API surface](#api-surface)
- [Storage and state](#storage-and-state)
- [Environment variables](#environment-variables)
- [What's missing](#whats-missing-roadmap)
- [Quick start (dev)](#quick-start-dev)

---

## The end-to-end flow

```
Onboarding ─► Settings (keys + data) ─► Analyze ─► Find ─► Walk-forward ─► Live
   pick a            connect            single     parameter   honest         set
   template          brokers + data      backtest    search     OOS test       trading
                     (one-time)
```

A user opens StratScout. The top-right shows their API connection status. They:

1. **Pick a template** from the Onboarding screen (ETF Balanced, ETF Defensive, or Small-cap Momentum). These are reasonable starting strategies, not magic formulas.
2. **Connect data + brokers** in Settings (auto-imports any existing `.env` keys). The Data panel shows what they have on disk; one click downloads the core ETF universe via Alpaca → yfinance fallback.
3. **Analyze** a single backtest on the picked strategy — see equity curve, drawdown, comparisons against SPY/QQQ/TQQQ/TLT/GLD, save the strategy.
4. **Find** better params via random + refinement search (fuzz). Each result is scored on train and forward windows. Click a row to load that param set into Analyze.
5. **Walk-forward** validate: train month-by-month over the last 1-3 years and paper-trade each subsequent month. The hit rate is your honest out-of-sample estimate.
6. **Live**: each saved strategy has an Off / Paper / Live toggle. Live is gated by preflight checks (walk-forward passed, drawdown bounded, risk acknowledged). When you flip the toggle, the strategy is registered for daily rebalancing.

Every fuzz, every walk-forward, and every strategy save is **persisted** to local SQLite — close the app, come back tomorrow, everything is still there.

---

## Tab-by-tab guide

### 1. Onboarding — pick a starting strategy

Three template cards, each with a one-paragraph description, a risk badge (Low / Moderate / High), and a click-to-select.

- **ETF Rotator (Balanced)** — regime-aware rotation among 3× leveraged ETFs (SOXL, TQQQ, UPRO, TECL) when AGG outperforms BIL, otherwise rotates to defensives (TLT, GLD, BTAL, XLP). Holds 2 positions, daily rebalance.
- **ETF Rotator (Defensive)** — same regime logic but the bull side is SPY/QQQ (no leverage). Smaller upside, much smaller drawdowns. Good first strategy.
- **Small Cap Volume Anomaly** — scans ~500 small-caps daily for volume surges (5× their 20-day average). Buy at next open, hold 5 days, equal-weight up to 4 positions.

These templates are starting points, not finished products. Use Find to search the parameter space around them.

### 2. Settings — keys + data

Two panels:

**Your data.** Five rows by data type:
- `daily` — ETF/stock daily OHLCV (used by the ETF rotator + walk-forward)
- `intraday` — 15-minute bars (for intraday strategies, optional)
- `smallcap` — small-cap daily universe (~500 symbols)
- `options` — Alpaca per-contract option chains (deferred — options strategies aren't validated yet)
- `options-theta` — ThetaData per-day option-chain snapshots (optional)

Each row shows the file count, total size on disk, earliest/latest bar dates, and a one-line description of which strategies use it. A green dot means data present; gray = empty.

The **"Download core ETFs"** button at the top fetches a curated ~12-symbol set (SPY, QQQ, TQQQ, UPRO, SOXL, TECL, AGG, BIL, TLT, GLD, SLV, XLP). It tries Alpaca first (uses your saved key), falls back to yfinance (no key needed). The post-download report tells you which source served each symbol and which failed.

**Connections.** One card per provider:
- **Alpaca** — required fields: `api_key`, `api_secret`, `paper` (true/false). Used for free daily-bar downloads and paper-trade execution.
- **Charles Schwab** — `app_key`, `app_secret`, `refresh_token`, `account_number`. For real-money execution. Requires a one-time OAuth flow (still uses the legacy `schwab_auth.py` script for now).
- **Polygon (Massive)** — `api_key`. Optional, higher-quality historical bars going further back.
- **ThetaData** — no keys required; tests connectivity to a local ThetaTerminal process.

Each card:
- Shows a **Connected** / **Missing keys** badge
- Inputs are password-masked. The current value is never shown — type a new value to overwrite.
- **Save** writes to the OS keychain (Windows Credential Locker / macOS Keychain). Falls back to `~/.stratscout/credentials.json` if keychain is unavailable.
- **Test connection** actually hits the provider's API (Alpaca `/v2/account`, Schwab token refresh, Polygon reference endpoint, ThetaTerminal localhost ping) and reports the verbatim response.
- **Clear** removes an individual field. There's also a "Get keys →" link to the provider's signup page.

`.env` keys are auto-imported on first run (`ALPACA_API_KEY` etc.). They're treated as a migration source — once you save into the keychain, the keychain copy wins.

### 3. Analyze — a single backtest + baselines

The "what does this strategy do?" view. Picking a template auto-runs a backtest over the last 3 years.

**Header:** strategy name, risk badge, and a `SaveBar` for persistence — "Save strategy" if not saved, or "Update / Save copy / Activate →" once saved.

**Controls bar:** date pickers for the backtest window, starting cash, four preset shortcut buttons (Last 1Y / 3Y / 5Y / Since 2020), and a "Run backtest" button.

**Results row:** five metric tiles
- Total return ($ from starting cash)
- CAGR (annualized)
- Worst drawdown
- Trade count
- Final portfolio value

**NAV chart:** Plotly time series with the strategy line in accent purple. The toolbar lets you toggle baselines:
- SPY, QQQ, TQQQ, TLT, GLD chip selectors (each fetches buy-and-hold NAV from `/baselines`)
- "Pool buy & hold" overlay (the BnH return of the strategy's own pool, as a dashed line)
- Hover tooltips show "Aug 14 2024 — $12,847" per series

A summary row beneath the chart shows each visible series' total return so you can see at a glance whether the strategy beat the baselines.

If you arrived from clicking a Find leaderboard row, a purple banner says *"Custom params loaded — Leaderboard pick (score 28.2)"* with a "Reset to template defaults" button.

### 4. Find — fuzz for better parameters

This is where you search for actually-good strategies. Five stacked panels:

**4a. "What are you looking for?" — goal presets.**
Four chips: **Steady / Balanced / Aggressive / Custom**. Each preset configures three things at once:
- An `exclude` list of symbols the fuzzer can't pick (e.g., Steady excludes all 3× leveraged ETFs and crypto)
- An `explore` ratio (random search vs. refinement of known winners)
- Default result filters (min CAGR, max drawdown, min trades)

The four filter inputs below let you tweak any threshold individually:
- **Min train CAGR** — hide rows whose training CAGR is too low
- **Min forward CAGR** — hide rows whose held-out window CAGR is too low
- **Max drawdown** — positive magnitude input ("40" = filter to results worse than -40%)
- **Min trades** — exclude inactive strategies

Filters are post-hoc and client-side: adjust them at any time without re-running. The leaderboard re-filters instantly.

**4b. Your data.** Five-tile summary: symbol count, data-span in years, earliest/latest bar, walk-forward-ready count. If you have no daily data, this panel becomes a "No data yet — open Settings" call-to-action.

**4c. Train / forward windows.** Four date pickers (train start/end, forward start/end). The smart suggester picks defaults *restricted to the symbols this template actually uses* — so a template that doesn't touch FNGU/IBIT/BITX gets a much wider window than the raw 52-symbol intersection. Notes panel explains its reasoning ("Train: 87mo / Forward: 12mo / Data span: 99mo"). One click resets to suggestion.

**4d. Fuzz controls.** Trials slider (10-2000), worker count (1-12), explore-vs-refine ratio. The big **Run fuzz** button kicks it off.

**4e. Saved fuzz runs (persistent leaderboard).** Collapsible card showing every fuzz run you've ever done — id, timestamp, label, goal preset, run window, top score. Click any row to re-load its leaderboard. The **"All-time top"** button at the top loads the top-100 results across every saved run, regardless of when they ran. Each row has a small `✕` for deletion.

This is the key persistence feature: runs survive page refreshes, app restarts, full reboots. You can pick a strategy from a run two weeks ago.

**4f. Leaderboard.** Sortable table:
| Rank | Score | Train CAGR | Train DD | Forward CAGR | Forward DD | Trades | Pool |

Each row is clickable → loads those exact params into Analyze and re-runs the backtest against your current Analyze window. The header has a **"Show all (bypass filters)"** toggle for when filters are hiding everything. When filters hide everything, a **Filter diagnostics** banner appears showing the distribution of every metric (min · median · max) across the unfiltered results, plus per-filter counts ("100 hidden by Max drawdown -10%"). This is how a novice diagnoses "why am I seeing 0 results?".

**4g. Session log.** Collapsible at the very bottom. Logs each fuzz start, completion (with elapsed time + top score + persisted run id), filter pass count, and any errors. Cleared on demand.

### 5. Walk-forward — honest out-of-sample validation

This is where you find out whether a strategy is *real* or just curve-fit.

For each month M in the validation window, the engine:
1. Fuzzes N trials on the prior `train_months` of data
2. Picks the best-scoring params from that fuzz
3. Paper-trades **only month M** with those params
4. Moves on to month M+1 and repeats — using a *fresh* fuzz each time

The aggregate hit rate is the user's evidence the strategy generalizes. There's no information leak from the validation period into training, because the training window slides forward as the validation window slides forward.

**Controls:** validation start/end, train-months (rolling window size, default 12), trials per month (200), workers, goal preset, starting cash. The header shows an estimated runtime (`24 months · est ~4 min`).

**Results:**
- **5 metric tiles**: active win rate (gating green/yellow/red), strategy equity vs SPY equity, edge over SPY, no-trade months breakdown (cash-OK vs missed-up)
- **Month-by-month grid**: each row shows SPY return, strategy return, worst DD, trade count, running compounded equity, and a verdict badge (HIT / LOSS / MISSED-UP / cash-ok)

If a saved strategy is currently active (from the Live tab or `Save bar`), the walk-forward result is automatically attached to it — that's what the preflight check reads to decide whether Live activation is allowed.

### 6. Live — trade mode + preflight gating

The trade-execution control center.

**My strategies list:** every saved strategy from your SQLite store. Click one to make it active; the entire screen below updates to that strategy.

**Trade mode toggle:** Off / Paper / Live. Live is **physically disabled** until preflight passes (the button doesn't even register clicks). Paper is always available given a connected broker. Off means the strategy is registered but inactive.

Currently the toggle persists state to the DB only — actual order placement is the next implementation step (see Roadmap below). The architecture is fully wired (`BrokerAdapter` interface, Schwab + Alpaca implementations, `live_trader.py` reference logic).

**Preflight checks panel:** a checklist of conditions Live activation requires:

1. **Walk-forward validation run** — at least one walk-forward must have been saved for this strategy. The "Fix" button jumps to the Walk-forward tab.
2. **Active win rate ≥ 50% over ≥ 12 traded months** — out-of-sample evidence the strategy actually works.
3. **Worst monthly drawdown ≤ 35%** — bounds the worst observed monthly loss.
4. **30+ days of paper trading recorded** — currently informational (paper engine ships next).
5. **Risk disclosure acknowledged** — clicking the "Acknowledge risk" button adds an `ACK_RISK` token to the strategy's notes and re-evaluates preflight.

Each failing check shows a hint and a "Fix" action that takes the user directly to the right place.

---

## Key concepts explained

These are the concepts a novice quant should understand to use the tool well.

### Backtest

Replay a strategy's rules over historical data to compute a hypothetical equity curve. The engine reads from local feather files only — no API calls, no live data — so it's fully reproducible and offline-safe. A typical ETF backtest over 3 years runs in ~50ms.

### Fuzzing (parameter search)

Random + refinement search over the strategy's parameter space:
- 60% of trials: sample fresh params at random
- 40% of trials: pick a known winner and jitter its numeric params by 10-40%

This is the simplest possible search that works for our setup (mostly-categorical params, fast backtests). Each trial scores on weighted geometric mean of train + forward CAGR, penalized for drawdown, idle windows, and overtrading.

### Train / forward split

The most basic out-of-sample test:
- **Train window**: the fuzzer optimizes against this period
- **Forward window**: held out, only scored — never seen during optimization

If the strategy looks great in train and terrible in forward, it's overfit.

### Walk-forward validation

A *much* stronger test than a single train/forward split. Instead of one fixed validation period, you slide both windows forward through history one month at a time. For each month M:

```
[ train: 12 months back ─────► ] [ validate: month M ]
                          [ train: 12 months back ─────► ] [ validate: month M+1 ]
                                                       (window slides)
```

You end up with N validation months, each with its own training data. The hit rate across them is your strategy's *out-of-sample* track record. This is much harder to game than a single split — and far closer to how you'd actually deploy the strategy.

### Active win rate vs raw hit rate

For strategies that sometimes sit in cash:
- **Active win rate** = `hits / (hits + losses)` — wins per traded month
- **Raw hit rate** = `hits / total months` — wins per *all* months including cash

Active win rate is the right metric. A strategy that correctly stays in cash during a bear market scored as a "miss" by raw hit rate but is doing the right thing.

### Preflight checks

Hard gates between you and live-money trading. The system intentionally makes Live activation *difficult* — you have to walk-forward-validate, observe drawdown bounds, and explicitly acknowledge risk. The default is "no, you may not lose money today."

---

## Data model

All persistent state lives in:

1. **OS keychain** — broker / data-provider credentials (per-user, encrypted at rest by the OS)
2. **Local SQLite** at `<data_dir>/stratscout.db` (desktop) or `<project_root>/stratscout.db` (dev)
3. **Local feather files** under `<data_dir>/{daily,15min,smallcap,options}/`

SQLite tables:

```
strategies          saved strategies (id, name, kind, params JSON, trade_mode, archived, notes)
walk_forward_runs   one row per WF execution (id, strategy_id, month-by-month rows as JSON)
preflight_checks    audit trail of every preflight evaluation
fuzz_runs           one row per fuzz session (window, n_runs, top_score, goal, label, elapsed)
fuzz_results        one row per individual trial (run_id, score, metrics, params JSON)
```

Indexes on `fuzz_results(run_id, score DESC)` and `fuzz_results(score DESC)` make per-run and cross-run leaderboards instant.

Universe definitions live in [`engine/data/universes.py`](engine/data/universes.py):

- **ANCHORS** — AGG, BIL, TLT (always loaded for regime detection)
- **RISK_ON_POOL** — 24 leveraged + crypto + miner ETFs the optimizer can pick from
- **RISK_OFF_RISING_POOL** — 5 short / inverse ETFs
- **RISK_OFF_FALLING_POOL** — 8 defensive ETFs (gold, treasuries, staples)

The Find tab's window suggester restricts the data-coverage intersection to the symbols the *current template* uses, so newer crypto ETFs (CONL, IBIT, FNGU) don't squash your usable history.

---

## Architecture

```
stratscout/
├── engine/                        ← pure Python; shared by all deployment modes
│   ├── backtest/
│   │   ├── core.py                ← rebalance_positions, value_of_portfolio, etc.
│   │   ├── etf.py                 ← regime-rotation strategy + backtest runner
│   │   └── smallcap.py            ← volume-anomaly strategy
│   ├── fuzzers/
│   │   ├── etf.py                 ← legacy multiprocessing fuzzer (CLI-callable)
│   │   ├── session.py             ← clean run_etf_fuzz() wrapper for the API
│   │   ├── walk_forward_etf.py    ← legacy WF CLI
│   │   └── walk_forward_session.py← clean run_etf_walk_forward() wrapper
│   ├── brokers/
│   │   ├── base.py                ← BrokerAdapter Protocol + Quote/Position/Account
│   │   ├── alpaca.py              ← AlpacaAdapter (free paper accounts)
│   │   └── schwab.py              ← SchwabAdapter (real-money OAuth)
│   ├── data/
│   │   ├── universes.py           ← symbol pools
│   │   ├── inventory.py           ← per-symbol coverage + per-category summary
│   │   ├── windows.py             ← smart train/forward window suggester
│   │   └── fetch.py               ← Alpaca → yfinance daily-bar downloader
│   ├── credentials.py             ← OS keychain + .env import; per-provider test()
│   ├── strategies.py              ← SQLite CRUD for strategies + walk-forward runs
│   ├── fuzz_store.py              ← SQLite CRUD for fuzz runs + cross-run leaderboard
│   ├── preflight.py               ← evaluates the live-activation checklist
│   ├── settings.py                ← desktop / web / dev path resolution
│   └── jobs.py                    ← JobRunner abstraction (LocalPool / Serial / Modal)
├── api/
│   ├── app.py                     ← FastAPI app with all endpoints
│   └── schemas.py                 ← Pydantic request/response models
├── web/                           ← Vite + React + TypeScript + Tailwind + Plotly
│   └── src/
│       ├── screens/               ← Onboarding, Analyze, Find, WalkForward, Live, Settings
│       ├── components/            ← NavChart, MetricTile, Button, RiskBadge, Spinner, Card
│       ├── api.ts                 ← typed fetch client
│       ├── store.ts               ← Zustand app state (view, template, saved strategy)
│       ├── types.ts               ← TS mirrors of the Pydantic schemas
│       ├── goals.ts               ← Search goal presets (Steady/Balanced/Aggressive/Custom)
│       ├── templates.ts           ← Starting-point strategies
│       └── format.ts              ← pct/money/pctColor formatters (tabular numerals)
├── desktop/                       ← Tauri shell (placeholder; needs Rust)
├── agent/                         ← BYOC daemon for free-tier web mode (placeholder)
└── tests/                         ← pytest (66 passing)
```

The same Python engine runs in three deployment modes (only one shipping today):
- **Dev mode** — both servers in separate terminals on localhost (today)
- **Desktop** — Tauri shell bundles the React build + Python sidecar into a single signed installer
- **Web** — Next.js frontend on Vercel + FastAPI on Fly.io + Modal for cloud compute

---

## API surface

All endpoints are served by the FastAPI app at `http://127.0.0.1:8765`. The Vite dev server proxies `/api/*` to it.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | service status, data dir, mode |
| POST | `/backtest` | run a single backtest, return NAV + perf summary |
| POST | `/baselines` | buy-and-hold NAV for arbitrary symbols (compare-against feature) |
| GET | `/data/inventory` | per-symbol coverage (date range, bar count, stale flag) |
| GET | `/data/categories` | five-row summary by data type (daily / intraday / smallcap / options) |
| POST | `/data/suggest-fuzz-window` | smart-default train/forward window for a given symbol set |
| POST | `/data/suggest-walk-forward` | smart-default walk-forward plan |
| POST | `/data/download` | download daily bars for a list of symbols (Alpaca → yfinance) |
| GET | `/settings/credentials` | list providers + which fields are populated (never returns secrets) |
| PUT | `/settings/credentials` | save one credential field to keychain |
| DELETE | `/settings/credentials/{provider}/{field}` | clear a credential |
| POST | `/settings/credentials/{provider}/test` | verify keys by hitting the provider |
| POST | `/fuzz` | run a fuzz session, persist every result, return leaderboard |
| GET | `/fuzz/runs` | list saved fuzz runs (recent first) |
| GET | `/fuzz/runs/{id}` | full leaderboard for one past run |
| DELETE | `/fuzz/runs/{id}` | remove a saved run + cascading results |
| PATCH | `/fuzz/runs/{id}` | rename a saved run |
| GET | `/fuzz/leaderboard` | top-N across every saved run (all-time view) |
| GET | `/strategies` | list saved strategies |
| POST | `/strategies` | create |
| GET | `/strategies/{id}` | read one |
| PATCH | `/strategies/{id}` | update name / params / trade_mode / notes / archived |
| DELETE | `/strategies/{id}` | hard-delete |
| GET | `/strategies/{id}/preflight` | run the live-activation checklist |
| GET | `/strategies/{id}/walk-forward/latest` | most recent saved walk-forward for the strategy |
| GET | `/strategies/{id}/orders` | trade-order activity log |
| POST | `/strategies/{id}/run-now` | dry-run target computation (records intent, no broker call) |
| POST | `/walk-forward` | run walk-forward validation; optionally save against `strategy_id` |

---

## Storage and state

| What | Where | Why |
|---|---|---|
| Broker API keys | OS keychain via `keyring` | encrypted at rest by the OS |
| Strategy params, walk-forward runs, fuzz runs | SQLite `stratscout.db` | ACID, indexed, browsable with any SQLite tool |
| Daily / intraday / smallcap bars | feather files under `<data_dir>/` | columnar, fast, zero deserialization overhead |
| Browser UI state | Zustand (in-memory) + URL view param | rehydrated from the API on refresh |
| Migration source | `.env` at project root | imported once into keychain on first run |

There is **no cloud dependency** for the desktop / dev modes. You can run StratScout fully offline as long as your daily feather files are populated.

---

## Environment variables

| Var | Values | Purpose |
|---|---|---|
| `STRATSCOUT_DATA_DIR` | absolute path | Override the data root (default: project-root `data/`) |
| `STRATSCOUT_MODE` | `desktop` \| `web-api` \| (unset) | Picks default paths and CORS posture |
| `STRATSCOUT_RUNNER` | `local` \| `serial` \| `modal` | Job execution backend (Modal lands in Phase 3) |
| `STRATSCOUT_API_HOST` | host | API bind host (default `127.0.0.1`) |
| `STRATSCOUT_API_PORT` | port | API bind port (default `8765`) |
| `STRATSCOUT_CORS_ORIGINS` | comma-list | Allowed origins in web mode |

---

## What's missing (roadmap)

Honest gap analysis for a novice-friendly tool. Listed roughly in order of user value.

### High-value gaps

1. **Daily / scheduled execution.** Paper and live order placement now run from the Live tab on demand — pick a mode, click run, the trader diffs target-vs-current positions and places market orders through the connected broker. What's still missing is a cron-style scheduler that fires every strategy with `trade_mode ∈ {paper, live}` at market open without the user clicking. EventBridge / a local Windows scheduled task is the next layer.
2. **Walk-forward parity for smallcap.** Find now supports smallcap fuzzing; Walk-forward is still ETF-only because the WF loop hard-codes the rotator engine. Wiring smallcap through the WF session wrapper is straightforward — the smallcap engine already exposes `find_signals` + `run_backtest`.
3. **Trade activity P/L + kill switch.** The activity panel shows order rows + status; the next pass adds realized P/L per closed trade and a single button to immediately set the strategy to `off` (cancelling any in-flight order).
4. **Smart symbol search.** The CoveragePanel "Add symbol" input downloads anything you type — but it doesn't validate the ticker exists first. A small autocomplete backed by a symbol list (or yfinance-Search) would prevent typos from producing empty feathers.

### Medium-value polish

5. **In-app help expansion.** The `?` button per tab opens a slide-out help drawer; the next pass adds inline contextual hints (e.g. "this score is high — explain why" tied to leaderboard rows) and ASCII diagrams of the walk-forward windows.
6. **Backup / restore.** A single "Export StratScout state" button that dumps the SQLite DB + a manifest of which keychain entries exist (without their values) to a portable archive. Critical for anyone who depends on the tool.
7. **Notifications.** Email or push when a live strategy hits its drawdown circuit breaker, when a Schwab refresh token is about to expire, when an order is rejected.
8. **Strategy versioning.** Today's "Save copy" preserves prior params, but there's no diff view. Strategy detail could show "version 3 — changed `n_risk_on` from 2 → 3 on 2026-04-01" to make refinement legible.

### Infrastructure / shipping

14. **Tauri desktop shell.** Bundles the React build + Python sidecar into a single signed installer for Windows/Mac. Needs `rustup` installed; then `cd stratscout/desktop && npm create tauri-app`.
15. **Code signing.** Once Tauri builds, Windows Defender SmartScreen will scare users unless the installer is signed. ~$200/year cert.
16. **/fuzz SSE progress.** Today big fuzz runs block the request for 60+ seconds. Server-Sent Events would let the UI stream progress per-trial.
17. **Multi-tenant web mode.** Postgres swap-in for `strategies.py`, Clerk auth, per-user S3 prefixes for feather files, Modal for cloud compute. Plan exists but not started.

### Does the overall design make sense for a novice quant?

Yes — the end-to-end loop closes. A novice can sign in, connect a broker, pick a template, fuzz + refine, validate via walk-forward, save the strategy, and trigger paper or live execution from the Live tab. The remaining sharp edge is *automation*: today execution is one-click, not on a cron. Until that scheduler ships, the novice has to remember to click "Run paper" each market open. Everything else for the persona is covered:

- BYOK data + brokers — ✅ Settings
- Pick a starting strategy — ✅ Onboarding templates (ETF + smallcap)
- Visualize against baselines — ✅ Analyze chart with SPY/QQQ/TQQQ overlays + saved-strategy overlays (Compare)
- Edit pools without leaving the screen — ✅ Inline ticker-chip editor in Analyze
- Search the param space — ✅ Find: goals + filters + persistent runs + refine actions + CSV export
- Pick which symbols drive the window — ✅ CoveragePanel with multi-select + per-symbol "Download more"
- Validate honestly — ✅ Walk-forward with equity-curve chart + CSV export
- Inspect a strategy — ✅ Live's strategy detail panel (params, latest WF, notes, activity)
- Gate live activation — ✅ Preflight checks
- Place real orders — ✅ Live tab "Run paper / Run live" via Alpaca + Schwab adapters
- Persistent state — ✅ SQLite + keychain
- Per-tab help — ✅ `?` button opens a slide-out help drawer
- Manage clutter — ✅ Archive / restore from Live

---

## Quick start (dev)

You need Python 3.11+, Node 20+, and your daily feather files populated (use the existing `download_data.py` or the in-app **Download core ETFs** button in Settings).

```powershell
# Terminal 1 — start the FastAPI service on 127.0.0.1:8765
cd c:\Code\algo-trading-schwab
python -m stratscout.api.app

# Terminal 2 — start the React UI on 127.0.0.1:5173
cd c:\Code\algo-trading-schwab\stratscout\web
npm install     # first time only
npm run dev
```

Then open http://127.0.0.1:5173. The Vite dev server proxies `/api/*` to the FastAPI service on 8765, so the UI talks to the engine over HTTP exactly the way it will in production.

### Run tests

```powershell
cd c:\Code\algo-trading-schwab
python -m pytest stratscout/tests
```

Currently 66 passing across:
- Backtest golden-output parity vs legacy CLI
- API endpoint round-trips (health, backtest, baselines, inventory, fuzz, walk-forward, strategies, preflight)
- Broker adapter protocol compliance (Alpaca + Schwab)
- Credentials storage round-trip
- Data inventory + smart window suggester
- Fuzz session + walk-forward session wrappers
- Persistent fuzz run save/list/delete + cross-run leaderboard
- Strategy CRUD + preflight state transitions
- JobRunner factory (local / serial / modal)
