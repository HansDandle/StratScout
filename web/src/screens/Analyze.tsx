// Analyze screen — run a backtest, see results, compare to baselines.

import { useEffect, useState } from "react";
import { GitBranch } from "lucide-react";
import { api, ApiError } from "../api";
import { useApp } from "../store";
import { pct, money, pctColor } from "../format";
import { NavChart } from "../components/NavChart";
import { Button, Card, MetricTile, RiskBadge, Spinner } from "../components/ui";
import { paramLabel, paramHint } from "../paramHints";
import type { BacktestResponse, BaselineSeries, StrategyKind, StrategyRow } from "../types";

const BASELINE_OPTIONS: Array<{ symbol: string; label: string; color: string }> = [
  { symbol: "SPY",  label: "SPY",  color: "#9aa0b0" },
  { symbol: "QQQ",  label: "QQQ",  color: "#5eead4" },
  { symbol: "TQQQ", label: "TQQQ", color: "#fbbf24" },
  { symbol: "TLT",  label: "TLT",  color: "#60a5fa" },
  { symbol: "GLD",  label: "GLD",  color: "#facc15" },
];

const DATE_PRESETS: Array<{ label: string; start: string; end: string }> = [
  { label: "Last 1Y",  start: shift(-1),  end: today() },
  { label: "Last 3Y",  start: shift(-3),  end: today() },
  { label: "Last 5Y",  start: shift(-5),  end: today() },
  { label: "Since 2020", start: "2020-01-01", end: today() },
];

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function shift(yearsBack: number): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() + yearsBack);
  return d.toISOString().slice(0, 10);
}


export function Analyze() {
  const template = useApp((s) => s.template);
  const lastResult = useApp((s) => s.lastResult);
  const lastWindow = useApp((s) => s.lastWindow);
  const setLastResult = useApp((s) => s.setLastResult);
  const setView = useApp((s) => s.setView);
  const customParams = useApp((s) => s.customParams);
  const customParamsLabel = useApp((s) => s.customParamsLabel);
  const setCustomParams = useApp((s) => s.setCustomParams);
  const activeSaved = useApp((s) => s.activeSavedStrategy);
  const setActiveSaved = useApp((s) => s.setActiveSavedStrategy);
  const setRefineSeedParams = useApp((s) => s.setRefineSeedParams);

  const [start, setStart] = useState(lastWindow?.start ?? shift(-3));
  const [end, setEnd] = useState(lastWindow?.end ?? today());
  const [cash, setCash] = useState(10_000);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showBnh, setShowBnh] = useState(true);
  const [selectedBaselines, setSelectedBaselines] = useState<Set<string>>(new Set(["SPY"]));
  const [baselineCache, setBaselineCache] = useState<Record<string, BaselineSeries>>({});
  const [savedStrategies, setSavedStrategies] = useState<StrategyRow[]>([]);
  const [comparisons, setComparisons] = useState<
    Record<number, { row: StrategyRow; result: BacktestResponse | null; loading: boolean; error?: string }>
  >({});

  // Pre-load the saved-strategies list once so the comparison picker has something to show.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const r = await api.listStrategies();
        if (!cancelled) setSavedStrategies(r.strategies);
      } catch {
        /* non-fatal */
      }
    })();
    return () => { cancelled = true; };
  }, []);

  async function addComparison(s: StrategyRow) {
    if (s.kind !== (template?.kind ?? "etf")) return; // mismatched engines won't compare cleanly
    if (comparisons[s.id]) return;
    setComparisons((m) => ({ ...m, [s.id]: { row: s, result: null, loading: true } }));
    try {
      const r = await api.backtest({
        strategy_kind: s.kind as StrategyKind,
        params: s.params,
        start, end, cash,
      });
      setComparisons((m) => ({ ...m, [s.id]: { row: s, result: r, loading: false } }));
    } catch (e) {
      setComparisons((m) => ({
        ...m,
        [s.id]: { row: s, result: null, loading: false, error: e instanceof ApiError ? e.message : String(e) },
      }));
    }
  }

  function removeComparison(id: number) {
    setComparisons((m) => {
      const next = { ...m };
      delete next[id];
      return next;
    });
  }

  // Auto-run a backtest the first time this screen mounts (smooth onboarding),
  // and any time customParams changes (i.e. a row was loaded from Find).
  useEffect(() => {
    if (template && !running) {
      void runBacktest();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customParams]);

  if (!template) {
    return (
      <div className="p-8">
        <p className="text-(--color-text-dim) mb-4">No strategy selected.</p>
        <Button onClick={() => setView("onboarding")}>Pick a template</Button>
      </div>
    );
  }

  async function runBacktest() {
    if (!template) return;
    setRunning(true);
    setError(null);
    // Window changed → drop cached baselines + comparison results, they're for the old window
    setBaselineCache({});
    try {
      const effectiveParams = customParams ?? template.defaultParams;
      const [result, base] = await Promise.all([
        api.backtest({
          strategy_kind: template.kind,
          params: effectiveParams,
          start,
          end,
          cash,
        }),
        api.baselines({
          symbols: Array.from(selectedBaselines),
          start, end, cash,
        }).catch(() => ({ baselines: [] })), // soft-fail; missing data shouldn't break the backtest
      ]);
      setLastResult(result, { start, end });
      const cache: Record<string, BaselineSeries> = {};
      for (const b of base.baselines) cache[b.symbol] = b;
      setBaselineCache(cache);
      // Re-run any active comparisons against the new window so the chart stays consistent.
      const ids = Object.keys(comparisons).map(Number);
      if (ids.length > 0) {
        const next: typeof comparisons = {};
        for (const id of ids) next[id] = { ...comparisons[id], result: null, loading: true };
        setComparisons(next);
        for (const id of ids) {
          const s = comparisons[id].row;
          void api
            .backtest({ strategy_kind: s.kind as StrategyKind, params: s.params, start, end, cash })
            .then((r) =>
              setComparisons((m) => ({ ...m, [id]: { row: s, result: r, loading: false } })),
            )
            .catch((e) =>
              setComparisons((m) => ({
                ...m,
                [id]: { row: s, result: null, loading: false, error: e instanceof ApiError ? e.message : String(e) },
              })),
            );
        }
      }
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    } finally {
      setRunning(false);
    }
  }

  async function toggleBaseline(symbol: string) {
    const next = new Set(selectedBaselines);
    if (next.has(symbol)) {
      next.delete(symbol);
      setSelectedBaselines(next);
      return;
    }
    next.add(symbol);
    setSelectedBaselines(next);
    // Lazy-fetch only the one that was just added (rest are cached)
    if (!baselineCache[symbol] && lastWindow) {
      try {
        const r = await api.baselines({
          symbols: [symbol],
          start: lastWindow.start,
          end: lastWindow.end,
          cash,
        });
        const added = r.baselines[0];
        if (added) setBaselineCache((c) => ({ ...c, [symbol]: added }));
      } catch {
        // ignored — baseline just won't render
      }
    }
  }

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-5">
      {/* Header: strategy name, risk, trade-mode toggle */}
      <header className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <button
            onClick={() => setView("onboarding")}
            className="text-xs text-(--color-text-dim) hover:text-(--color-text) mb-1"
          >
            ← Change template
          </button>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold">{template.name}</h1>
            <RiskBadge level={template.riskLabel} />
          </div>
          <p className="text-sm text-(--color-text-dim) mt-1 max-w-2xl">
            {template.description}
          </p>
        </div>

        <SaveBar
          template={template}
          customParams={customParams}
          savedStrategy={activeSaved}
          onSaved={(s) => setActiveSaved(s)}
          onGoLive={() => setView("live")}
        />
      </header>

      {customParams && (
        <Card className="p-3 border-(--color-accent)/50 bg-(--color-accent-dim)/30 flex items-center justify-between gap-3">
          <span className="text-sm">
            <span className="text-(--color-accent) font-medium">Custom params loaded</span>
            <span className="text-(--color-text-dim)"> — {customParamsLabel ?? "from leaderboard"}</span>
          </span>
          <button
            onClick={() => setCustomParams(null)}
            className="text-xs text-(--color-text-dim) hover:text-(--color-text)"
          >
            Reset to template defaults
          </button>
        </Card>
      )}

      {/* Controls */}
      <Card className="p-4 flex flex-wrap items-end gap-4">
        <div>
          <label className="block text-xs text-(--color-text-dim) mb-1">From</label>
          <input
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
            className="bg-(--color-surface-2) border border-(--color-border) rounded px-3 py-1.5 text-sm tabular"
          />
        </div>
        <div>
          <label className="block text-xs text-(--color-text-dim) mb-1">To</label>
          <input
            type="date"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
            className="bg-(--color-surface-2) border border-(--color-border) rounded px-3 py-1.5 text-sm tabular"
          />
        </div>
        <div>
          <label className="block text-xs text-(--color-text-dim) mb-1">Starting cash</label>
          <input
            type="number"
            value={cash}
            onChange={(e) => setCash(Number(e.target.value))}
            step={1000}
            min={100}
            className="bg-(--color-surface-2) border border-(--color-border) rounded px-3 py-1.5 text-sm tabular w-32"
          />
        </div>

        <div className="flex items-center gap-2">
          {DATE_PRESETS.map((p) => (
            <button
              key={p.label}
              onClick={() => {
                setStart(p.start);
                setEnd(p.end);
              }}
              className="text-xs px-2 py-1 rounded border border-(--color-border) hover:bg-(--color-surface-2) text-(--color-text-dim)"
            >
              {p.label}
            </button>
          ))}
        </div>

        <div className="ml-auto flex items-center gap-2">
          <Button onClick={runBacktest} disabled={running}>
            {running ? <Spinner /> : "Run backtest"}
          </Button>
        </div>
      </Card>

      {error && (
        <Card className="p-4 border-(--color-neg)/50 bg-(--color-neg)/10">
          <span className="text-(--color-neg) text-sm">{error}</span>
        </Card>
      )}

      <ParametersCard
        params={customParams ?? template.defaultParams}
        onChange={(next) => {
          // Editing the pool params writes a custom set + re-runs the backtest
          // so the user immediately sees the impact.
          setCustomParams(next, "Edited pools");
        }}
      />

      {/* Results */}
      {lastResult && (
        <>
          <div className="flex flex-wrap gap-3">
            <MetricTile
              label="Total return"
              value={pct(lastResult.perf.total_return_pct)}
              tone={lastResult.perf.total_return_pct >= 0 ? "pos" : "neg"}
              hint={`from ${money(cash)}`}
            />
            <MetricTile
              label="CAGR"
              value={pct(lastResult.perf.cagr_pct)}
              tone={lastResult.perf.cagr_pct >= 0 ? "pos" : "neg"}
              hint="annualized"
            />
            <MetricTile
              label="Worst drawdown"
              value={pct(lastResult.perf.max_drawdown_pct)}
              tone="neg"
              hint="peak to trough"
            />
            <MetricTile
              label="Trades"
              value={String(lastResult.n_trades)}
              hint={`over ${start} → ${end}`}
            />
            <MetricTile
              label="Final value"
              value={money(lastResult.nav_values.at(-1) ?? cash)}
            />
          </div>

          <Card className="p-4">
            <div className="flex items-center justify-between mb-3 flex-wrap gap-3">
              <h2 className="text-sm font-medium text-(--color-text-dim) uppercase tracking-wider">
                Portfolio value vs. baselines
              </h2>
              <div className="flex items-center gap-2 flex-wrap">
                <button
                  onClick={() => {
                    const params = customParams ?? template.defaultParams;
                    setRefineSeedParams([params], `refine of ${template.name}`);
                    setView("find");
                  }}
                  className="flex items-center gap-1.5 text-xs px-2.5 py-1 rounded border transition-colors hover:bg-white/5"
                  style={{ borderColor: "var(--color-border)", color: "var(--color-text-dim)" }}
                  title="Send current params to Find as refinement seeds"
                >
                  <GitBranch size={12} />
                  Refine in Find
                </button>
              <CompareStrategyPicker
                template={template}
                savedStrategies={savedStrategies}
                activeSavedId={activeSaved?.id ?? null}
                comparisons={comparisons}
                onAdd={addComparison}
                onRemove={removeComparison}
              />
              </div>
            </div>
            <div className="flex items-center justify-end mb-3 flex-wrap gap-3">
              <div className="flex items-center gap-3 flex-wrap">
                <span className="text-xs text-(--color-text-dim)">Compare:</span>
                {BASELINE_OPTIONS.map((opt) => {
                  const on = selectedBaselines.has(opt.symbol);
                  return (
                    <button
                      key={opt.symbol}
                      onClick={() => toggleBaseline(opt.symbol)}
                      className={`px-2 py-0.5 text-xs rounded border transition ${
                        on
                          ? "border-(--color-accent) bg-(--color-accent-dim) text-(--color-text)"
                          : "border-(--color-border) text-(--color-text-dim) hover:text-(--color-text)"
                      }`}
                      style={on ? { borderColor: opt.color, color: opt.color } : undefined}
                    >
                      {opt.label}
                    </button>
                  );
                })}
                <label className="flex items-center gap-1.5 text-xs cursor-pointer ml-2 pl-2 border-l border-(--color-border)">
                  <input
                    type="checkbox"
                    checked={showBnh}
                    onChange={(e) => setShowBnh(e.target.checked)}
                    className="accent-(--color-accent)"
                  />
                  <span className={showBnh ? "" : "text-(--color-text-dim)"}>
                    Pool buy &amp; hold
                  </span>
                </label>
              </div>
            </div>

            <NavChart
              series={[
                {
                  label: template.name,
                  index: lastResult.nav_index,
                  values: lastResult.nav_values,
                },
                ...(showBnh && lastResult.bnh_values
                  ? [
                      {
                        label: "Strategy pool B&H",
                        index: lastResult.nav_index,
                        values: lastResult.bnh_values,
                        color: "#9aa0b0",
                        dash: "dash" as const,
                      },
                    ]
                  : []),
                ...Array.from(selectedBaselines).flatMap((sym) => {
                  const b = baselineCache[sym];
                  if (!b) return [];
                  const color = BASELINE_OPTIONS.find((o) => o.symbol === sym)?.color;
                  return [
                    {
                      label: b.label,
                      index: b.index,
                      values: b.values,
                      color,
                      dash: "dot" as const,
                    },
                  ];
                }),
                ...Object.entries(comparisons).flatMap(([id, c], i) => {
                  if (!c.result) return [];
                  const palette = ["#f472b6", "#34d399", "#fb923c", "#a78bfa", "#22d3ee"];
                  return [
                    {
                      label: c.row.name,
                      index: c.result.nav_index,
                      values: c.result.nav_values,
                      color: palette[Number(id) % palette.length === 0 ? i % palette.length : Number(id) % palette.length],
                    },
                  ];
                }),
              ]}
            />

            <div className="mt-4 pt-4 border-t border-(--color-border) text-sm flex flex-wrap gap-x-6 gap-y-1">
              <span className="text-(--color-text-dim)">
                {template.name}:{" "}
                <strong className={`${pctColor(lastResult.perf.total_return_pct)} tabular`}>
                  {pct(lastResult.perf.total_return_pct)}
                </strong>
              </span>
              {showBnh && lastResult.bnh_values && (
                <span className="text-(--color-text-dim)">
                  Pool B&amp;H:{" "}
                  <strong
                    className={`${pctColor(((lastResult.bnh_values.at(-1) ?? cash) / cash - 1) * 100)} tabular`}
                  >
                    {pct(((lastResult.bnh_values.at(-1) ?? cash) / cash - 1) * 100)}
                  </strong>
                </span>
              )}
              {Array.from(selectedBaselines).map((sym) => {
                const b = baselineCache[sym];
                if (!b) return null;
                return (
                  <span key={sym} className="text-(--color-text-dim)">
                    {b.label}:{" "}
                    <strong className={`${pctColor(b.total_return_pct)} tabular`}>
                      {pct(b.total_return_pct)}
                    </strong>
                  </span>
                );
              })}
              {Object.values(comparisons).map((c) => {
                if (!c.result) return null;
                return (
                  <span key={c.row.id} className="text-(--color-text-dim)">
                    {c.row.name}:{" "}
                    <strong className={`${pctColor(c.result.perf.total_return_pct)} tabular`}>
                      {pct(c.result.perf.total_return_pct)}
                    </strong>
                  </span>
                );
              })}
            </div>
          </Card>
        </>
      )}

      {!lastResult && !running && !error && (
        <Card className="p-8 text-center">
          <p className="text-(--color-text-dim)">
            Pick a window and click <strong>Run backtest</strong>.
          </p>
        </Card>
      )}
    </div>
  );
}

// Persisted strategy save controls. Save as new, update existing, jump to Live.
function SaveBar({
  template,
  customParams,
  savedStrategy,
  onSaved,
  onGoLive,
}: {
  template: { name: string; kind: string; defaultParams: Record<string, unknown> };
  customParams: Record<string, unknown> | null;
  savedStrategy: import("../types").StrategyRow | null;
  onSaved: (s: import("../types").StrategyRow) => void;
  onGoLive: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function saveAsNew() {
    setBusy(true);
    setErr(null);
    try {
      const defaultName = customParams
        ? `${template.name} (custom)`
        : template.name;
      const name = window.prompt("Name this strategy:", defaultName);
      if (!name) {
        setBusy(false);
        return;
      }
      const params = customParams ?? template.defaultParams;
      const s = await api.createStrategy({
        name,
        kind: template.kind,
        params,
        notes: "",
      });
      onSaved(s);
    } catch (e) {
      setErr(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function update() {
    if (!savedStrategy) return;
    setBusy(true);
    setErr(null);
    try {
      const params = customParams ?? template.defaultParams;
      const updated = await api.updateStrategy(savedStrategy.id, { params });
      onSaved(updated);
    } catch (e) {
      setErr(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1.5">
      <div className="flex items-center gap-2">
        {savedStrategy ? (
          <>
            <span className="text-xs text-(--color-text-dim)">
              Saved as <strong className="text-(--color-text)">{savedStrategy.name}</strong>
            </span>
            <Button size="sm" variant="ghost" onClick={update} disabled={busy}>
              Update
            </Button>
            <Button size="sm" variant="ghost" onClick={saveAsNew} disabled={busy}>
              Save copy
            </Button>
            <Button size="sm" onClick={onGoLive}>
              Activate →
            </Button>
          </>
        ) : (
          <Button size="sm" onClick={saveAsNew} disabled={busy}>
            {busy ? <Spinner /> : "Save strategy"}
          </Button>
        )}
      </div>
      {err && <span className="text-xs text-(--color-neg)">{err}</span>}
    </div>
  );
}

// Pick saved strategies to overlay on the NAV chart for side-by-side comparison.
// Only same-kind strategies are eligible (engines aren't interchangeable).
function CompareStrategyPicker({
  template,
  savedStrategies,
  activeSavedId,
  comparisons,
  onAdd,
  onRemove,
}: {
  template: { name: string; kind: string };
  savedStrategies: StrategyRow[];
  activeSavedId: number | null;
  comparisons: Record<number, { row: StrategyRow; result: BacktestResponse | null; loading: boolean }>;
  onAdd: (s: StrategyRow) => void;
  onRemove: (id: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const eligible = savedStrategies.filter(
    (s) => s.kind === template.kind && !comparisons[s.id] && s.id !== activeSavedId,
  );
  const active = Object.values(comparisons);

  return (
    <div className="flex items-center gap-2 flex-wrap">
      {active.map((c) => (
        <span
          key={c.row.id}
          className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded border border-(--color-border) bg-(--color-surface-2)/60"
        >
          {c.loading ? <Spinner /> : null}
          {c.row.name}
          <button
            onClick={() => onRemove(c.row.id)}
            className="text-(--color-text-dim) hover:text-(--color-neg) ml-1"
            aria-label="Remove comparison"
            title="Remove"
          >
            ×
          </button>
        </span>
      ))}
      <div className="relative">
        <button
          onClick={() => setOpen((v) => !v)}
          disabled={eligible.length === 0}
          title={eligible.length === 0 ? "No other saved strategies of this kind" : "Compare against a saved strategy"}
          className="text-xs px-2 py-1 rounded border border-(--color-border) text-(--color-text-dim) hover:text-(--color-text) hover:border-(--color-text-dim) disabled:opacity-40 disabled:cursor-not-allowed"
        >
          + Compare against saved
        </button>
        {open && eligible.length > 0 && (
          <div
            className="absolute right-0 mt-1 z-10 bg-(--color-surface) border border-(--color-border) rounded-md shadow-lg min-w-[220px] max-h-72 overflow-y-auto"
          >
            <ul className="divide-y divide-(--color-border)/40">
              {eligible.map((s) => (
                <li key={s.id}>
                  <button
                    onClick={() => { onAdd(s); setOpen(false); }}
                    className="w-full px-3 py-2 text-left hover:bg-(--color-surface-2)/60"
                  >
                    <div className="text-sm">{s.name}</div>
                    <div className="text-xs text-(--color-text-dim)">
                      {s.kind} · updated {new Date(s.updated_at + "Z").toLocaleDateString()}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

// Collapsible parameter table. Pool params (arrays) render as ticker chips so
// they're readable; scalars render as key→value. Hovering the label surfaces a
// plain-English explanation for each knob.
//
// When onChange is provided, pool params become editable inline — users can
// add or remove tickers and the change is propagated back to the parent which
// stores it in customParams + re-runs the backtest.
function ParametersCard({
  params,
  onChange,
}: {
  params: Record<string, unknown>;
  onChange?: (next: Record<string, unknown>) => void;
}) {
  const [open, setOpen] = useState(false);
  const keys = Object.keys(params);
  const pools = keys.filter((k) => Array.isArray(params[k]));
  const scalars = keys.filter((k) => !Array.isArray(params[k]));
  const updatePool = (poolKey: string, next: string[]) => {
    if (!onChange) return;
    onChange({ ...params, [poolKey]: next });
  };
  return (
    <Card className="overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full px-4 py-3 flex items-center justify-between hover:bg-(--color-surface-2)/40"
      >
        <h2 className="text-sm font-medium text-(--color-text-dim) uppercase tracking-wider">
          Parameters — {keys.length}
        </h2>
        <span className="text-xs text-(--color-text-dim)">
          {open ? "Hide" : "Show"} {open ? "▴" : "▾"}
        </span>
      </button>
      {open && (
        <div className="px-4 pb-4 space-y-4 border-t border-(--color-border)">
          {scalars.length > 0 && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-1.5 pt-3">
              {scalars.map((k) => (
                <ParamRow key={k} k={k} value={params[k]} />
              ))}
            </div>
          )}
          {pools.length > 0 && (
            <div className="space-y-3 pt-1">
              {pools.map((k) => (
                <PoolRow
                  key={k}
                  k={k}
                  symbols={(params[k] as unknown[]).map(String)}
                  onChange={onChange ? (next) => updatePool(k, next) : undefined}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function ParamRow({ k, value }: { k: string; value: unknown }) {
  const hint = paramHint(k);
  const display =
    typeof value === "boolean" ? (value ? "yes" : "no") : String(value);
  return (
    <div className="flex items-baseline justify-between gap-2 text-sm">
      <span
        className="text-(--color-text-dim) cursor-help"
        title={hint || k}
      >
        {paramLabel(k)}
      </span>
      <span className="tabular font-medium">{display}</span>
    </div>
  );
}

// Pool of tickers — editable when onChange is provided. Add via small text input,
// remove via × on each chip. New tickers are uppercased + deduped before applying.
function PoolRow({
  k,
  symbols,
  onChange,
}: {
  k: string;
  symbols: string[];
  onChange?: (next: string[]) => void;
}) {
  const hint = paramHint(k);
  const editable = !!onChange;
  const [draft, setDraft] = useState("");

  function applyDraft() {
    if (!onChange) return;
    const toks = draft
      .toUpperCase()
      .split(/[\s,]+/)
      .map((t) => t.trim())
      .filter(Boolean);
    if (toks.length === 0) return;
    const set = new Set(symbols);
    for (const t of toks) set.add(t);
    onChange(Array.from(set));
    setDraft("");
  }

  function remove(symbol: string) {
    if (!onChange) return;
    onChange(symbols.filter((s) => s !== symbol));
  }

  return (
    <div>
      <div
        className="text-xs uppercase tracking-wider text-(--color-text-dim) mb-1 cursor-help"
        title={hint || k}
      >
        {paramLabel(k)} — {symbols.length}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {symbols.map((s) => (
          <span
            key={s}
            className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded border border-(--color-border) bg-(--color-surface-2)/40 tabular"
          >
            {s}
            {editable && (
              <button
                onClick={() => remove(s)}
                className="text-(--color-text-dim) hover:text-(--color-neg) ml-0.5"
                aria-label={`Remove ${s}`}
                title="Remove"
              >
                ×
              </button>
            )}
          </span>
        ))}
        {editable && (
          <span className="inline-flex items-center gap-1">
            <input
              type="text"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") applyDraft();
              }}
              placeholder="add ticker…"
              className="bg-(--color-surface-2) border border-(--color-border) rounded px-2 py-0.5 text-xs tabular w-32"
            />
            <button
              onClick={applyDraft}
              disabled={!draft.trim()}
              className="text-xs px-2 py-0.5 rounded border border-(--color-border) text-(--color-text-dim) hover:text-(--color-text) hover:border-(--color-text-dim) disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Add
            </button>
          </span>
        )}
      </div>
    </div>
  );
}
