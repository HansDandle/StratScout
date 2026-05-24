// Walk-Forward tab — honest out-of-sample validation.
//
// For each month in [start, end), the engine fuzzes on the prior N months,
// picks the best params, then paper-trades that month. The aggregate hit
// rate + equity curve is the user's evidence that the strategy is real
// (not just curve-fit).

import { useMemo, useState } from "react";
import { api, ApiError } from "../api";
import { useApp } from "../store";
import { Button, Card, MetricTile, Spinner } from "../components/ui";
import { NavChart } from "../components/NavChart";
import { pct, money, pctColor } from "../format";
import { GOALS, findGoal } from "../goals";
import { toCsv, downloadCsv, todayStamp } from "../csv";
import type { WalkForwardResponse, WalkForwardRowOut } from "../types";

const DEFAULT_TRAIN_MONTHS = 12;
const DEFAULT_VALIDATION_MONTHS = 24;

function shiftMonths(months: number): string {
  const d = new Date();
  d.setMonth(d.getMonth() + months);
  return d.toISOString().slice(0, 10);
}
function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

export function WalkForward() {
  const template = useApp((s) => s.template);
  const activeSaved = useApp((s) => s.activeSavedStrategy);
  const setActiveSaved = useApp((s) => s.setActiveSavedStrategy);
  const setView = useApp((s) => s.setView);

  const [start, setStart] = useState(shiftMonths(-DEFAULT_VALIDATION_MONTHS));
  const [end, setEnd] = useState(todayISO());
  const [trainMonths, setTrainMonths] = useState(DEFAULT_TRAIN_MONTHS);
  const [nTrials, setNTrials] = useState(150);
  const [workers, setWorkers] = useState(() => Math.max(3, (navigator.hardwareConcurrency ?? 8) - 1));
  const [goalId, setGoalId] = useState<string>("balanced");

  const [valCadence, setValCadence] = useState<"monthly" | "biweekly">("monthly");
  const [fastMode, setFastMode] = useState(false);
  const [useOptuna, setUseOptuna] = useState(false);
  const [useGp, setUseGp] = useState(false);
  const [gpPopulation, setGpPopulation] = useState(100);
  const [gpGenerations, setGpGenerations] = useState(30);
  const [aiSteerEvery, setAiSteerEvery] = useState(5);
  const [martingaleOn, setMartingaleOn] = useState(false);
  const [martingaleFactor, setMartingaleFactor] = useState(1.5);
  const [reserveCash, setReserveCash] = useState(10_000);
  const [mmRate, setMmRate] = useState(4.5);

  // Single-run state
  const [running, setRunning] = useState(false);
  const [progressCompleted, setProgressCompleted] = useState(0);
  const [progressTotal, setProgressTotal] = useState(0);
  const [progressRemaining, setProgressRemaining] = useState<number | null>(null);
  const [steeringLog, setSteeringLog] = useState<{excluded: string[]; reason: string}[]>([]);
  const [result, setResult] = useState<WalkForwardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Compare state
  const [comparing, setComparing] = useState(false);
  const [compareProgress, setCompareProgress] = useState<[number, number, number, number]>([0, 0, 0, 0]);
  const [compareResults, setCompareResults] = useState<[WalkForwardResponse | null, WalkForwardResponse | null]>([null, null]);
  const [compareError, setCompareError] = useState<string | null>(null);

  const goal = useMemo(() => findGoal(goalId), [goalId]);

  // Estimate runtime — purely informational. Each month-fuzz ≈ (n_trials × ~0.05s / workers)
  const monthCount = useMemo(() => {
    try {
      const a = new Date(start);
      const b = new Date(end);
      return Math.max(0, Math.round((b.getTime() - a.getTime()) / (30.44 * 86400 * 1000)));
    } catch {
      return 0;
    }
  }, [start, end]);

  const estSeconds = useMemo(() => {
    if (useGp) {
      // GP: population × generations × 3 backtests × ~80ms each, parallel across workers
      return Math.round((monthCount * gpPopulation * gpGenerations * 3 * 0.08) / Math.max(1, workers));
    }
    return Math.round((monthCount * nTrials * 0.05) / Math.max(1, workers));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [monthCount, nTrials, workers, useGp, gpPopulation, gpGenerations]);

  if (!template) {
    return (
      <div className="p-8">
        <p className="text-(--color-text-dim) mb-4">No strategy selected.</p>
        <Button onClick={() => setView("onboarding")}>Pick a template</Button>
      </div>
    );
  }

  if (template.kind === "smallcap") {
    return (
      <div className="max-w-3xl mx-auto p-6">
        <Card className="p-6 space-y-3">
          <h1 className="text-xl font-semibold">Walk-forward is ETF-only for now</h1>
          <p className="text-sm text-(--color-text-dim)">
            The walk-forward engine drives the rotator fuzzer month by month.
            The smallcap volume-anomaly strategy isn't wired into it yet — pick
            an ETF template to run honest out-of-sample validation.
          </p>
          <div className="flex gap-2">
            <Button onClick={() => setView("onboarding")}>Pick another template</Button>
            <Button variant="ghost" onClick={() => setView("analyze")}>Back to Analyze</Button>
          </div>
        </Card>
      </div>
    );
  }

  function buildReq(overrides: Partial<Parameters<typeof api.walkForwardStream>[0]> = {}) {
    return {
      start, end, train_months: trainMonths,
      n_trials: nTrials, workers,
      exclude: goal.exclude,
      starting_cash: 10_000,
      strategy_id: activeSaved?.id ?? undefined,
      val_weeks: valCadence === "biweekly" ? 2 : null,
      martingale_factor: martingaleOn ? martingaleFactor : 1.0,
      reserve_cash: martingaleOn ? reserveCash : 0,
      mm_rate_annual: martingaleOn ? mmRate / 100 : 0,
      fast_mode: fastMode,
      use_optuna: useOptuna && !useGp,
      use_gp: useGp,
      gp_population: gpPopulation,
      gp_generations: gpGenerations,
      ai_steer_every: aiSteerEvery,
      ...overrides,
    };
  }

  async function runWalkForward() {
    setRunning(true);
    setError(null);
    setResult(null);
    setCompareResults([null, null]);
    setProgressCompleted(0);
    setProgressTotal(monthCount);
    setProgressRemaining(null);
    setSteeringLog([]);
    try {
      const r = await api.walkForwardStream(
        buildReq(),
        (completed, total, _month, _elapsed, remaining) => {
          setProgressCompleted(completed);
          setProgressTotal(total);
          setProgressRemaining(remaining);
        },
        (excluded, reason) => {
          setSteeringLog(prev => [...prev, { excluded, reason }]);
        },
      );
      setResult(r);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    } finally {
      setRunning(false);
      setProgressCompleted(0);
      setProgressTotal(0);
      setProgressRemaining(null);
    }
  }

  async function runCompare() {
    setComparing(true);
    setCompareError(null);
    setCompareResults([null, null]);
    setResult(null);
    setCompareProgress([0, 0, 0, 0]);
    try {
      const [monthly, biweekly] = await Promise.all([
        api.walkForwardStream(
          buildReq({ val_weeks: null }),
          (c, t) => setCompareProgress((p) => [c, t, p[2], p[3]]),
        ),
        api.walkForwardStream(
          buildReq({ val_weeks: 2 }),
          (c, t) => setCompareProgress((p) => [p[0], p[1], c, t]),
        ),
      ]);
      setCompareResults([monthly, biweekly]);
    } catch (e) {
      setCompareError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    } finally {
      setComparing(false);
    }
  }

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-5">
      <header>
        <h1 className="text-2xl font-semibold">Walk-forward validation</h1>
        <p className="text-sm text-(--color-text-dim) max-w-2xl mt-1">
          Tests whether the <strong className="text-(--color-text)">{template.name}</strong>{" "}
          approach holds up out-of-sample. For each month in the window, the
          engine re-runs the fuzzer on the prior {trainMonths} months of training
          data, picks the best params it finds, and paper-trades that single
          month with them. Your specific leaderboard params are{" "}
          <em>not</em> used — params are re-discovered fresh each month.
        </p>
        <p className="text-xs text-(--color-text-dim) mt-2 max-w-2xl">
          To test a specific set of params on an out-of-sample window, set the
          date range in{" "}
          <button
            onClick={() => setView("analyze")}
            className="text-(--color-accent) hover:underline"
          >
            Analyze
          </button>{" "}
          to your forward period and run a backtest there.
        </p>
        {activeSaved && (
          <p className="text-xs text-(--color-accent) mt-2 flex items-center gap-2">
            <span>Results will be saved against strategy: <strong>{activeSaved.name}</strong></span>
            <button
              onClick={() => setActiveSaved(null)}
              className="text-(--color-text-dim) hover:text-(--color-text) leading-none"
              title="Clear — run without attaching to a strategy"
            >×</button>
          </p>
        )}
      </header>

      <Card className="p-4 space-y-4">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <DateField label="Validation start" value={start} onChange={setStart} />
          <DateField label="Validation end" value={end} onChange={setEnd} />
          <div>
            <label className="block text-xs text-(--color-text-dim) mb-1">
              Rolling train window
              <span className="ml-1 text-(--color-text-dim)" title="How many months of history the fuzzer trains on before each validation month">ⓘ</span>
            </label>
            <div className="flex items-center gap-1.5">
              <input
                type="number"
                value={trainMonths}
                onChange={(e) => setTrainMonths(Number(e.target.value))}
                min={3} max={36}
                className="bg-(--color-surface-2) border border-(--color-border) rounded px-3 py-1.5 text-sm tabular w-24"
              />
              <span className="text-xs text-(--color-text-dim)">months</span>
            </div>
          </div>
          {!useGp && (
            <div>
              <label className="block text-xs text-(--color-text-dim) mb-1">
                Fuzzer trials per month
                <span className="ml-1 text-(--color-text-dim)" title="How many param combinations the fuzzer tries when optimising each month's training window. Higher = better params found, slower run.">ⓘ</span>
              </label>
              <input
                type="number"
                value={nTrials}
                onChange={(e) => setNTrials(Number(e.target.value))}
                min={20} max={1000} step={10}
                className="bg-(--color-surface-2) border border-(--color-border) rounded px-3 py-1.5 text-sm tabular w-32"
              />
            </div>
          )}
        </div>
        <div className="flex items-end gap-4 flex-wrap">
          <NumberField label="Workers" value={workers} onChange={setWorkers} min={1} max={12} />
          <div>
            <label className="block text-xs text-(--color-text-dim) mb-1">
              Scoring goal
              <span className="ml-1 text-(--color-text-dim)" title="What the fuzzer optimises for when picking params each month">ⓘ</span>
            </label>
            <select
              value={goalId}
              onChange={(e) => setGoalId(e.target.value)}
              className="bg-(--color-surface-2) border border-(--color-border) rounded px-3 py-1.5 text-sm"
            >
              {GOALS.map((g) => (
                <option key={g.id} value={g.id}>{g.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-(--color-text-dim) mb-1">
              Reoptimize every
            </label>
            <div className="flex rounded overflow-hidden border border-(--color-border) text-sm">
              {(["monthly", "biweekly"] as const).map((c) => (
                <button
                  key={c}
                  onClick={() => setValCadence(c)}
                  className={`px-3 py-1.5 capitalize ${valCadence === c ? "bg-(--color-accent) text-white" : "bg-(--color-surface-2) text-(--color-text-dim) hover:text-(--color-text)"}`}
                >
                  {c}
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="block text-xs text-(--color-text-dim) mb-1" title="Full: 3 sub-window training (more overfit-resistant). Fast: single window (3× faster, fine for short training windows).">Training ⓘ</label>
            <div className="flex rounded overflow-hidden border border-(--color-border) text-sm">
              {([["Full", false], ["Fast", true]] as [string, boolean][]).map(([label, val]) => (
                <button key={label} onClick={() => setFastMode(val)}
                  className={`px-3 py-1.5 ${fastMode === val ? "bg-(--color-accent) text-white" : "bg-(--color-surface-2) text-(--color-text-dim) hover:text-(--color-text)"}`}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="block text-xs text-(--color-text-dim) mb-1" title="Random: explore+refine random search. Bayesian: TPE sampler (fewer trials). GP: genetic programming discovers if/else logic automatically.">Sampler ⓘ</label>
            <div className="flex rounded overflow-hidden border border-(--color-border) text-sm">
              {([["Random", false, false], ["Bayesian", true, false], ["GP", false, true]] as [string, boolean, boolean][]).map(([label, isOptuna, isGp]) => {
                const active = isGp ? useGp : (isOptuna ? (useOptuna && !useGp) : (!useOptuna && !useGp));
                return (
                  <button key={label} onClick={() => {
                    setUseGp(isGp);
                    setUseOptuna(isOptuna);
                    if (isOptuna) setNTrials(80);
                    if (!isOptuna && !isGp) setNTrials(150);
                  }}
                    className={`px-3 py-1.5 ${active ? "bg-(--color-accent) text-white" : "bg-(--color-surface-2) text-(--color-text-dim) hover:text-(--color-text)"}`}>
                    {label}
                  </button>
                );
              })}
            </div>
          </div>
          {useGp && (
            <div className="flex items-end gap-3 flex-wrap">
              <NumberField label="Population" value={gpPopulation} onChange={setGpPopulation} min={10} max={500} step={10} />
              <NumberField label="Generations" value={gpGenerations} onChange={setGpGenerations} min={5} max={200} step={5} />
            </div>
          )}
          <div className="flex items-end gap-3 flex-wrap">
            <NumberField label="AI steer every N months (0=off)" value={aiSteerEvery} onChange={setAiSteerEvery} min={0} max={20} step={1} />
          </div>
          <div>
            <label className="block text-xs text-(--color-text-dim) mb-1">Martingale</label>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setMartingaleOn((v) => !v)}
                className={`relative w-10 h-5 rounded-full transition-colors ${martingaleOn ? "bg-(--color-accent)" : "bg-(--color-border)"}`}
                title="Scale position size after losses"
              >
                <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${martingaleOn ? "translate-x-5" : "translate-x-0.5"}`} />
              </button>
              {martingaleOn && (
                <>
                  <input
                    type="number"
                    value={martingaleFactor}
                    onChange={(e) => setMartingaleFactor(Number(e.target.value))}
                    min={1.1} max={3.0} step={0.1}
                    className="bg-(--color-surface-2) border border-(--color-border) rounded px-2 py-1 text-sm tabular w-20"
                    title="Multiplier applied to position after each consecutive loss (capped at 4×)"
                  />
                  <span className="text-xs text-(--color-text-dim)">Reserve</span>
                  <input
                    type="number"
                    value={reserveCash}
                    onChange={(e) => setReserveCash(Number(e.target.value))}
                    min={0} step={1000}
                    className="bg-(--color-surface-2) border border-(--color-border) rounded px-2 py-1 text-sm tabular w-24"
                    title="Cash held in money market alongside deployed capital"
                  />
                  <span className="text-xs text-(--color-text-dim)">MM%</span>
                  <input
                    type="number"
                    value={mmRate}
                    onChange={(e) => setMmRate(Number(e.target.value))}
                    min={0} max={20} step={0.1}
                    className="bg-(--color-surface-2) border border-(--color-border) rounded px-2 py-1 text-sm tabular w-16"
                    title="Annual money market yield on reserve (%)"
                  />
                </>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-3 justify-end flex-wrap">
          {!running && !comparing && (
            <span className="text-xs text-(--color-text-dim) tabular">
              {monthCount} months × {nTrials} trials · est ~{Math.max(1, Math.round(estSeconds / 60))} min
            </span>
          )}
          <Button variant="ghost" onClick={runCompare} disabled={running || comparing || monthCount === 0}>
            {comparing ? <><Spinner /><span className="ml-2">Comparing…</span></> : "Compare cadences"}
          </Button>
          <Button onClick={runWalkForward} disabled={running || comparing || monthCount === 0}>
            {running ? <><Spinner /><span className="ml-2">Running</span></> : "Run walk-forward"}
          </Button>
        </div>
        {comparing && (
          <div className="space-y-2">
            {([["Monthly", compareProgress[0], compareProgress[1]], ["Biweekly", compareProgress[2], compareProgress[3]]] as const).map(([label, done, total]) => (
              <div key={label} className="space-y-1">
                <div className="flex justify-between text-xs text-(--color-text-dim)">
                  <span>{label}: {done} / {total || "…"}</span>
                  <span>{total ? `${Math.round((done / total) * 100)}%` : ""}</span>
                </div>
                <div className="w-full bg-(--color-surface) rounded-full h-1 overflow-hidden">
                  <div className="bg-(--color-accent) h-full rounded-full transition-all duration-300" style={{ width: `${total ? (done / total) * 100 : 0}%` }} />
                </div>
              </div>
            ))}
          </div>
        )}
        {running && progressTotal > 0 && (
          <div className="space-y-1.5">
            <div className="flex items-center justify-between text-xs text-(--color-text-dim)">
              <span>
                {progressCompleted} / {progressTotal} months complete
                {progressRemaining !== null && progressRemaining > 0 && (
                  <span className="ml-2 tabular">
                    · ~{progressRemaining < 60
                      ? `${progressRemaining}s`
                      : `${Math.ceil(progressRemaining / 60)}m`} remaining
                  </span>
                )}
              </span>
              <span className="tabular">{Math.round((progressCompleted / progressTotal) * 100)}%</span>
            </div>
            <div className="w-full bg-(--color-surface) rounded-full h-1.5 overflow-hidden">
              <div
                className="bg-(--color-accent) h-full rounded-full transition-all duration-300"
                style={{ width: `${(progressCompleted / progressTotal) * 100}%` }}
              />
            </div>
          </div>
        )}
        {steeringLog.length > 0 && (
          <div className="space-y-1 mt-1">
            {steeringLog.map((s, i) => (
              <div key={i} className="text-xs flex gap-2 items-start">
                <span className="text-(--color-accent) font-medium shrink-0">AI steered →</span>
                <span className="text-(--color-warn)">excluded {s.excluded.join(", ")}</span>
                <span className="text-(--color-text-dim) italic">{s.reason}</span>
              </div>
            ))}
          </div>
        )}
        {error && (
          <p className="text-(--color-neg) text-sm">{error}</p>
        )}
      </Card>

      {result && <SummaryPanel result={result} />}
      {result && <EquityChart result={result} />}
      {result && result.analysis && <AnalysisPanel analysis={result.analysis} />}
      {result && <MonthlyGrid rows={result.rows} starting_cash={result.starting_cash} />}
      {compareError && <p className="text-(--color-neg) text-sm">{compareError}</p>}
      {(compareResults[0] || compareResults[1]) && (
        <ComparePanel monthly={compareResults[0]} biweekly={compareResults[1]} />
      )}
    </div>
  );
}

// Compound monthly returns into the running strategy equity series; pair it
// with SPY's compound equity over the same months so the chart shows them
// side by side. End-of-month timestamps are placed at month_end - 1 day so
// the line sits inside the month, not at the start of the next.
function EquityChart({ result }: { result: WalkForwardResponse }) {
  const { stratIndex, stratValues, spyIndex, spyValues } = useMemo(() => {
    const stratIndex: string[] = [];
    const stratValues: number[] = [];
    const spyIndex: string[] = [];
    const spyValues: number[] = [];

    let strat = result.starting_cash;
    let spy = result.starting_cash;
    // Anchor point: the very start of the validation window
    if (result.rows.length > 0) {
      const first = result.rows[0].month;
      stratIndex.push(first);
      stratValues.push(strat);
      spyIndex.push(first);
      spyValues.push(spy);
    }
    for (const r of result.rows) {
      if (r.val_trades > 0) strat *= 1 + r.val_return_pct / 100;
      spy *= 1 + r.spy_return_pct / 100;
      // Position the data point at the end of the month
      const d = new Date(r.month);
      d.setMonth(d.getMonth() + 1);
      const stamp = d.toISOString().slice(0, 10);
      stratIndex.push(stamp);
      stratValues.push(strat);
      spyIndex.push(stamp);
      spyValues.push(spy);
    }
    return { stratIndex, stratValues, spyIndex, spyValues };
  }, [result]);

  if (stratValues.length === 0) return null;

  return (
    <Card className="p-4">
      <h2 className="text-sm font-medium text-(--color-text-dim) uppercase tracking-wider mb-3">
        Equity curve — strategy vs SPY buy-and-hold
      </h2>
      <NavChart
        series={[
          { label: "Strategy", index: stratIndex, values: stratValues },
          {
            label: "SPY buy & hold",
            index: spyIndex,
            values: spyValues,
            color: "#9aa0b0",
            dash: "dash",
          },
        ]}
        height={340}
      />
    </Card>
  );
}

function SummaryPanel({ result }: { result: WalkForwardResponse }) {
  const edge = result.final_equity - result.spy_equity;
  const stratPct = ((result.final_equity / result.starting_cash) - 1) * 100;
  const spyPct = ((result.spy_equity / result.starting_cash) - 1) * 100;

  const tone: "pos" | "neg" | "warn" =
    result.active_win_rate >= 60 ? "pos"
    : result.active_win_rate >= 40 ? "warn"
    : "neg";

  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
      <MetricTile
        label="Active win rate"
        value={`${result.active_win_rate.toFixed(0)}%`}
        tone={tone}
        hint={`${result.hits} / ${result.hits + result.losses} traded months`}
      />
      <MetricTile
        label="Strategy equity"
        value={money(result.final_equity)}
        tone={stratPct >= 0 ? "pos" : "neg"}
        hint={`${pct(stratPct)} of ${money(result.starting_cash)}`}
      />
      <MetricTile
        label="SPY same period"
        value={money(result.spy_equity)}
        hint={pct(spyPct)}
      />
      <MetricTile
        label="Edge vs SPY"
        value={money(edge)}
        tone={edge >= 0 ? "pos" : "neg"}
        hint={edge >= 0 ? "Strategy ahead" : "Strategy behind"}
      />
      <MetricTile
        label="No-trade months"
        value={String(result.cash_ok + result.missed_up)}
        hint={`${result.missed_up} missed up · ${result.cash_ok} cash-OK`}
      />
    </div>
  );
}

function MonthlyGrid({
  rows,
  starting_cash,
}: {
  rows: WalkForwardRowOut[];
  starting_cash: number;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);

  let equity = starting_cash;
  const equities: number[] = [];
  for (const r of rows) {
    if (r.val_trades > 0) equity *= 1 + r.val_return_pct / 100;
    equities.push(equity);
  }

  return (
    <Card className="overflow-hidden">
      <div className="px-4 py-3 border-b border-(--color-border) flex items-center justify-between">
        <div>
          <h2 className="text-sm font-medium text-(--color-text-dim) uppercase tracking-wider">
            Month-by-month results
          </h2>
          <p className="text-xs text-(--color-text-dim) mt-0.5">
            Click a row to see which params and pool the fuzzer chose that month.
          </p>
        </div>
        <button
          onClick={() => exportWalkForwardCsv(rows, equities)}
          className="text-xs text-(--color-text-dim) hover:text-(--color-text) underline-offset-2 hover:underline"
          title="Download month-by-month rows as CSV"
        >
          Export CSV
        </button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs text-(--color-text-dim) uppercase tracking-wider">
            <tr className="border-b border-(--color-border)">
              <th className="text-left px-4 py-2">Month</th>
              <th className="text-right px-4 py-2">SPY</th>
              <th className="text-right px-4 py-2">Strategy</th>
              <th className="text-right px-4 py-2">Worst DD</th>
              <th className="text-right px-4 py-2">Trades</th>
              <th className="text-right px-4 py-2">Equity</th>
              <th className="text-left px-4 py-2">Verdict</th>
              <th className="text-left px-4 py-2">Holding</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const isOpen = expanded === r.month;
              const isGpStrategy = r.params != null && "rules" in r.params;
              const pool = (r.params?.risk_on_pool as string[] | undefined) ?? [];
              const riskOffRising = (r.params?.risk_off_rising_pool as string[] | undefined) ?? [];
              const riskOffFalling = (r.params?.risk_off_falling_pool as string[] | undefined) ?? [];

              // GP: summarise the strategy description for the Holding column
              const gpDescription = isGpStrategy ? (() => {
                const rules = r.params!.rules as Array<{condition: Record<string,unknown>; action: Record<string,unknown>}> | undefined;
                if (!rules || rules.length === 0) return "else: default";
                const first = rules[0];
                const cond = first.condition as {lhs?: Record<string,unknown>; op?: string; rhs?: Record<string,unknown>};
                const lhsSym = (cond.lhs as {symbol?: string} | undefined)?.symbol ?? "?";
                return `if ${lhsSym}… → GP rule tree`;
              })() : null;

              return (
                <>
                  <tr
                    key={r.month}
                    onClick={() => setExpanded(isOpen ? null : r.month)}
                    className={`border-b border-(--color-border)/40 cursor-pointer hover:bg-(--color-surface-2)/40 ${isOpen ? "bg-(--color-surface-2)/60" : ""}`}
                  >
                    <td className="px-4 py-2 tabular">
                      <span className="mr-1 text-(--color-text-dim) text-xs">{isOpen ? "▾" : "▸"}</span>
                      {r.month.slice(0, 7)}
                    </td>
                    <td className={`px-4 py-2 text-right tabular ${pctColor(r.spy_return_pct)}`}>
                      {pct(r.spy_return_pct)}
                    </td>
                    <td className={`px-4 py-2 text-right tabular ${pctColor(r.val_return_pct)}`}>
                      {r.val_trades > 0 ? pct(r.val_return_pct) : "—"}
                    </td>
                    <td className="px-4 py-2 text-right tabular text-(--color-neg)">
                      {r.val_dd_pct !== 0 ? pct(r.val_dd_pct) : "—"}
                    </td>
                    <td className="px-4 py-2 text-right tabular text-(--color-text-dim)">
                      {r.val_trades}
                    </td>
                    <td className="px-4 py-2 text-right tabular">
                      {money(equities[i])}
                    </td>
                    <td className="px-4 py-2">
                      <VerdictBadge verdict={r.verdict} />
                    </td>
                    <td className="px-4 py-2 text-xs text-(--color-text-dim) max-w-[200px] truncate">
                      {isGpStrategy ? (gpDescription ?? "GP strategy") : (pool.length > 0 ? pool.join(", ") : "—")}
                    </td>
                  </tr>
                  {isOpen && r.params && (
                    <tr key={`${r.month}-detail`} className="border-b border-(--color-border)/40 bg-(--color-surface-2)/30">
                      <td colSpan={8} className="px-6 py-3">
                        {isGpStrategy ? (
                          <div className="text-xs">
                            <div className="text-(--color-text-dim) uppercase tracking-wider mb-2 font-medium">GP strategy logic</div>
                            <pre className="bg-(--color-surface) rounded p-3 text-(--color-text) overflow-x-auto leading-relaxed">
                              {JSON.stringify(r.params.rules, null, 2)}
                            </pre>
                          </div>
                        ) : (
                          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
                            <div>
                              <div className="text-(--color-text-dim) uppercase tracking-wider mb-1.5 font-medium">Risk-on pool</div>
                              <div className="flex flex-wrap gap-1">
                                {pool.length > 0 ? pool.map((s) => (
                                  <span key={s} className="px-2 py-0.5 rounded bg-(--color-pos)/15 text-(--color-pos) border border-(--color-pos)/30">{s}</span>
                                )) : <span className="text-(--color-text-dim)">—</span>}
                              </div>
                            </div>
                            <div>
                              <div className="text-(--color-text-dim) uppercase tracking-wider mb-1.5 font-medium">Risk-off rising</div>
                              <div className="flex flex-wrap gap-1">
                                {riskOffRising.length > 0 ? riskOffRising.map((s) => (
                                  <span key={s} className="px-2 py-0.5 rounded bg-(--color-warn)/15 text-(--color-warn) border border-(--color-warn)/30">{s}</span>
                                )) : <span className="text-(--color-text-dim)">—</span>}
                              </div>
                            </div>
                            <div>
                              <div className="text-(--color-text-dim) uppercase tracking-wider mb-1.5 font-medium">Risk-off falling</div>
                              <div className="flex flex-wrap gap-1">
                                {riskOffFalling.length > 0 ? riskOffFalling.map((s) => (
                                  <span key={s} className="px-2 py-0.5 rounded bg-(--color-neg)/15 text-(--color-neg) border border-(--color-neg)/30">{s}</span>
                                )) : <span className="text-(--color-text-dim)">—</span>}
                              </div>
                            </div>
                          </div>
                        )}
                        {r.note && (
                          <div className="mt-3 text-xs text-(--color-text-dim) italic border-l-2 border-(--color-accent)/40 pl-3">
                            {r.note}
                          </div>
                        )}
                        <details className="mt-3">
                          <summary className="text-xs text-(--color-text-dim) cursor-pointer hover:text-(--color-text)">
                            Raw params JSON
                          </summary>
                          <pre className="mt-2 text-xs text-(--color-text-dim) bg-(--color-surface) rounded p-3 overflow-x-auto">
                            {JSON.stringify(r.params, null, 2)}
                          </pre>
                        </details>
                      </td>
                    </tr>
                  )}
                </>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function ComparePanel({
  monthly,
  biweekly,
}: {
  monthly: WalkForwardResponse | null;
  biweekly: WalkForwardResponse | null;
}) {
  const series = useMemo(() => {
    const out = [];
    const colors = ["#7c9ef8", "#f8a14f"];
    for (const [label, result, color] of [
      ["Monthly", monthly, colors[0]],
      ["Biweekly", biweekly, colors[1]],
    ] as [string, WalkForwardResponse | null, string][]) {
      if (!result) continue;
      const index: string[] = [];
      const values: number[] = [];
      let eq = result.starting_cash;
      if (result.rows.length > 0) { index.push(result.rows[0].month); values.push(eq); }
      for (const r of result.rows) {
        if (r.val_trades > 0) eq *= 1 + r.val_return_pct / 100;
        const d = new Date(r.month); d.setMonth(d.getMonth() + 1);
        index.push(d.toISOString().slice(0, 10));
        values.push(eq);
      }
      out.push({ label, index, values, color });
    }
    return out;
  }, [monthly, biweekly]);

  if (series.length === 0) return null;

  const stats = [
    { label: "Monthly", r: monthly },
    { label: "Biweekly", r: biweekly },
  ].filter((x) => x.r) as { label: string; r: WalkForwardResponse }[];

  return (
    <Card className="p-4 space-y-4">
      <h2 className="text-sm font-medium text-(--color-text-dim) uppercase tracking-wider">
        Cadence comparison — equity curves
      </h2>
      <NavChart series={series} height={300} />
      <div className="grid grid-cols-2 gap-4">
        {stats.map(({ label, r }) => {
          const pct = ((r.final_equity / r.starting_cash) - 1) * 100;
          return (
            <div key={label} className="space-y-1">
              <div className="text-xs font-medium text-(--color-text-dim) uppercase">{label}</div>
              <div className="grid grid-cols-3 gap-2 text-sm">
                <MetricTile label="Final equity" value={money(r.final_equity)} hint={`${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`} tone={pct >= 0 ? "pos" : "neg"} />
                <MetricTile label="Win rate" value={`${r.active_win_rate.toFixed(0)}%`} hint={`${r.hits}W / ${r.losses}L`} tone={r.active_win_rate >= 60 ? "pos" : r.active_win_rate >= 40 ? "warn" : "neg"} />
                <MetricTile label="Periods" value={String(r.n_months)} hint={`${r.missed_up} missed-up`} />
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function AnalysisPanel({ analysis }: { analysis: NonNullable<WalkForwardResponse["analysis"]> }) {
  return (
    <Card>
      <div className="p-4 space-y-3">
        <div className="text-sm font-semibold text-(--color-text)">AI Analysis</div>
        {analysis.win_pattern && (
          <div>
            <div className="text-xs text-(--color-pos) uppercase tracking-wider mb-1 font-medium">Win pattern</div>
            <p className="text-sm text-(--color-text)">{analysis.win_pattern}</p>
          </div>
        )}
        {analysis.loss_pattern && (
          <div>
            <div className="text-xs text-(--color-neg) uppercase tracking-wider mb-1 font-medium">Loss pattern</div>
            <p className="text-sm text-(--color-text)">{analysis.loss_pattern}</p>
          </div>
        )}
        {analysis.next_steps && (
          <div>
            <div className="text-xs text-(--color-text-dim) uppercase tracking-wider mb-1 font-medium">What to try next</div>
            {Array.isArray(analysis.next_steps)
              ? <ul className="text-sm text-(--color-text) space-y-1 list-disc list-inside">{(analysis.next_steps as string[]).map((s, i) => <li key={i}>{s}</li>)}</ul>
              : <p className="text-sm text-(--color-text) whitespace-pre-line">{analysis.next_steps}</p>
            }
          </div>
        )}
      </div>
    </Card>
  );
}

function VerdictBadge({ verdict }: { verdict: string }) {
  const style = {
    HIT: "bg-(--color-pos)/15 text-(--color-pos) border-(--color-pos)/40",
    LOSS: "bg-(--color-neg)/15 text-(--color-neg) border-(--color-neg)/40",
    "MISSED-UP": "bg-(--color-warn)/15 text-(--color-warn) border-(--color-warn)/40",
    "cash-ok": "bg-(--color-text-dim)/15 text-(--color-text-dim) border-(--color-text-dim)/40",
  }[verdict] ?? "bg-(--color-text-dim)/15 text-(--color-text-dim) border-(--color-text-dim)/40";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 text-xs rounded-full border ${style}`}>
      {verdict}
    </span>
  );
}

function DateField({
  label, value, onChange,
}: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div>
      <label className="block text-xs text-(--color-text-dim) mb-1">{label}</label>
      <input
        type="date"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="bg-(--color-surface-2) border border-(--color-border) rounded px-3 py-1.5 text-sm tabular w-full"
      />
    </div>
  );
}

function exportWalkForwardCsv(rows: WalkForwardRowOut[], equities: number[]): void {
  const headers = [
    "month", "spy_return_pct", "val_return_pct", "val_dd_pct",
    "val_trades", "verdict", "equity", "risk_on_pool", "risk_off_rising_pool", "risk_off_falling_pool",
  ];
  const data = rows.map((r, i) => [
    r.month, r.spy_return_pct.toFixed(2),
    r.val_return_pct.toFixed(2), r.val_dd_pct.toFixed(2),
    r.val_trades, r.verdict, (equities[i] ?? 0).toFixed(2),
    ((r.params?.risk_on_pool as string[] | undefined) ?? []).join(" "),
    ((r.params?.risk_off_rising_pool as string[] | undefined) ?? []).join(" "),
    ((r.params?.risk_off_falling_pool as string[] | undefined) ?? []).join(" "),
  ]);
  downloadCsv(`stratscout-walkforward-${todayStamp()}.csv`, toCsv(headers, data));
}

function NumberField({
  label, value, onChange, min, max, step = 1,
}: {
  label: string; value: number;
  onChange: (n: number) => void;
  min: number; max: number; step?: number;
}) {
  return (
    <div>
      <label className="block text-xs text-(--color-text-dim) mb-1">{label}</label>
      <input
        type="number"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        min={min} max={max} step={step}
        className="bg-(--color-surface-2) border border-(--color-border) rounded px-3 py-1.5 text-sm tabular w-32"
      />
    </div>
  );
}
