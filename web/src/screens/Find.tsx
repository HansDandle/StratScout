// Find tab — explore parameter space via fuzzing, sort the leaderboard,
// click any row to load that param set into Analyze.
//
// Sections:
//   1) Data inventory summary (what symbols / how much history)
//   2) Window picker (smart-suggested + user override)
//   3) Fuzz controls (n_runs, workers, explore)
//   4) Leaderboard

import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import { useApp } from "../store";
import { Button, Card, Spinner } from "../components/ui";
import { pct, pctColor } from "../format";
import { GOALS, findGoal, type SearchGoal } from "../goals";
import { templateSymbols } from "../templates";
import { toCsv, downloadCsv, todayStamp } from "../csv";
import type {
  FuzzResultRow,
  FuzzRunMetaRow,
  InventoryResponse,
  SuggestFuzzWindowResponse,
} from "../types";

export function Find() {
  const template = useApp((s) => s.template);
  const setView = useApp((s) => s.setView);
  const setCustomParams = useApp((s) => s.setCustomParams);
  const refineSeedParams = useApp((s) => s.refineSeedParams);
  const refineSeedLabel = useApp((s) => s.refineSeedLabel);
  const setRefineSeedParams = useApp((s) => s.setRefineSeedParams);

  const [inv, setInv] = useState<InventoryResponse | null>(null);
  const [winSugg, setWinSugg] = useState<SuggestFuzzWindowResponse | null>(null);
  // Symbols whose date ranges drive the window suggester. Defaults to the
  // template's universe; the CoveragePanel lets the user toggle.
  const [selectedSymbols, setSelectedSymbols] = useState<Set<string>>(new Set());

  const [trainStart, setTrainStart] = useState("");
  const [trainEnd, setTrainEnd] = useState("");
  const [fwdStart, setFwdStart] = useState("");
  const [fwdEnd, setFwdEnd] = useState("");

  const [nRuns, setNRuns] = useState(100);
  const [workers, setWorkers] = useState(4);
  const [explore, setExplore] = useState(0.6);

  // Goal preset + per-filter overrides
  const [goalId, setGoalId] = useState<string>("balanced");
  const goal = useMemo(() => findGoal(goalId), [goalId]);
  const [filters, setFilters] = useState(goal.filters);
  // Track whether the user has touched the filters since selecting the goal
  const [filtersTouched, setFiltersTouched] = useState(false);

  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rows, setRows] = useState<FuzzResultRow[]>([]);
  const [completed, setCompleted] = useState(0);
  // Indices into `rows` (the current leaderboard) chosen as refinement seeds.
  // Reset whenever the leaderboard changes underneath us.
  const [selectedSeeds, setSelectedSeeds] = useState<Set<number>>(new Set());

  // Session event log — what happened, when. Capped so it can't grow forever.
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const logEvent = (e: Omit<SessionEvent, "ts">) =>
    setEvents((prev) =>
      [{ ts: new Date().toISOString().slice(11, 19), ...e }, ...prev].slice(0, 60),
    );

  // Persistent run history — runs saved in the SQLite DB
  const [runs, setRuns] = useState<FuzzRunMetaRow[]>([]);
  const [activeRunId, setActiveRunId] = useState<number | null>(null);
  const [showAllTime, setShowAllTime] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);

  async function refreshRuns(autoLoadLatest = false) {
    try {
      const r = await api.fuzzRuns(20);
      setRuns(r.runs);
      // On initial mount: if the leaderboard is empty, auto-load the most recent run
      if (autoLoadLatest && r.runs.length > 0) {
        void loadHistoricalRun(r.runs[0].id);
      }
    } catch {
      /* non-fatal */
    }
  }

  useEffect(() => {
    void refreshRuns(true);
  }, []);

  // When "Refine this" is triggered from Analyze, auto-run a refine on mount.
  useEffect(() => {
    if (!refineSeedParams || refineSeedParams.length === 0) return;
    const seeds = refineSeedParams;
    const label = refineSeedLabel ?? `refine of ${seeds.length} seeds`;
    setRefineSeedParams(null); // consume immediately so we don't re-trigger
    void runRefine(seeds, label, `${seeds.length} seed${seeds.length === 1 ? "" : "s"} from Analyze`);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refineSeedParams]);

  async function loadHistoricalRun(id: number) {
    setLoadingHistory(true);
    setShowAllTime(false);
    setError(null);
    try {
      const r = await api.fuzzRunDetail(id);
      setRows(r.results);
      setCompleted(r.meta.completed);
      setActiveRunId(id);
      logEvent({
        level: "info",
        message: `Loaded run #${id} (${r.meta.completed}/${r.meta.n_runs}, top ${r.meta.top_score?.toFixed(1) ?? "?"})`,
      });
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    } finally {
      setLoadingHistory(false);
    }
  }

  async function loadAllTime() {
    setLoadingHistory(true);
    setError(null);
    try {
      const r = await api.fuzzLeaderboard(100, template?.kind ?? "etf");
      // Project leaderboard entries into the FuzzResultRow shape the table renders
      const projected: FuzzResultRow[] = r.entries.map((e) => ({
        score: e.score,
        train_return_pct: e.train_return_pct,
        train_cagr_pct: e.train_cagr_pct,
        train_dd_pct: e.train_dd_pct,
        fwd_return_pct: e.fwd_return_pct,
        fwd_cagr_pct: e.fwd_cagr_pct,
        fwd_dd_pct: e.fwd_dd_pct,
        n_trades: e.n_trades,
        params: e.params,
      }));
      setRows(projected);
      setCompleted(r.entries.length);
      setActiveRunId(null);
      setShowAllTime(true);
      logEvent({
        level: "info",
        message: `All-time leaderboard: top ${r.entries.length} across every saved run`,
      });
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    } finally {
      setLoadingHistory(false);
    }
  }

  async function deleteRun(id: number) {
    if (!window.confirm("Delete this run and all its results?")) return;
    try {
      await api.deleteFuzzRun(id);
      if (activeRunId === id) {
        setRows([]);
        setActiveRunId(null);
      }
      await refreshRuns();
      logEvent({ level: "info", message: `Deleted run #${id}` });
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    }
  }

  // Initial load: inventory + selection default (template's symbols).
  // The window suggestion is fired separately from the selection effect below
  // so the same code path handles "initial load" and "user toggled a symbol".
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const i = await api.inventory();
        if (cancelled) return;
        setInv(i);
        if (template) setSelectedSymbols(new Set(templateSymbols(template)));
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e.message : String(e));
      }
    })();
    return () => { cancelled = true; };
  }, [template]);

  // Re-suggest window whenever the user-selected symbol set changes.
  useEffect(() => {
    if (!template || selectedSymbols.size === 0) return;
    let cancelled = false;
    void (async () => {
      try {
        const w = await api.suggestFuzzWindow({
          strategy_kind: template?.kind ?? "etf",
          fwd_months: 12,
          required_symbols: Array.from(selectedSymbols),
        });
        if (cancelled) return;
        setWinSugg(w);
        if (w.available && w.train_start) {
          setTrainStart(w.train_start);
          setTrainEnd(w.train_end!);
          setFwdStart(w.fwd_start!);
          setFwdEnd(w.fwd_end!);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e.message : String(e));
      }
    })();
    return () => { cancelled = true; };
  }, [template, selectedSymbols]);

  // Helper used by the coverage panel's "Download more" / "Add symbol" actions
  // to refresh the inventory after a download finishes.
  async function refreshInventory() {
    try {
      const i = await api.inventory();
      setInv(i);
    } catch {
      /* non-fatal */
    }
  }

  // Reset filters when goal preset changes (unless user has manually overridden)
  useEffect(() => {
    if (!filtersTouched) setFilters(goal.filters);
    setExplore(goal.explore);
  }, [goal, filtersTouched]);

  // Apply leaderboard filters client-side so users can adjust without re-running
  const filteredRows = useMemo(() => {
    return rows.filter(
      (r) =>
        r.train_cagr_pct >= filters.minTrainCAGR &&
        r.fwd_cagr_pct >= filters.minFwdCAGR &&
        Math.min(r.train_dd_pct, r.fwd_dd_pct) >= filters.maxWorstDD &&
        r.n_trades >= filters.minTrades,
    );
  }, [rows, filters]);

  async function runFuzz() {
    setRunning(true);
    setError(null);
    setRows([]);
    setCompleted(0);
    setShowAllTime(false);
    const t0 = performance.now();
    logEvent({
      level: "info",
      message: `Fuzz started: ${nRuns} trials, ${workers} workers, goal=${goal.name}, exclude=${goal.exclude.length}`,
    });
    try {
      const r = await api.fuzz({
        strategy_kind: template?.kind ?? "etf",
        train_start: trainStart,
        train_end: trainEnd,
        fwd_start: fwdStart,
        fwd_end: fwdEnd,
        n_runs: nRuns,
        workers,
        explore,
        exclude: goal.exclude,
        goal_id: goal.id,
      });
      setRows(r.results);
      setCompleted(r.completed);
      setActiveRunId(r.run_id);
      void refreshRuns();
      const elapsed = ((performance.now() - t0) / 1000).toFixed(1);
      logEvent({
        level: "info",
        message: `Fuzz completed in ${elapsed}s — ${r.completed}/${nRuns} runs, ${r.failed} failed, top score ${r.results[0]?.score?.toFixed(1) ?? "?"}${r.run_id ? ` (saved as #${r.run_id})` : ""}`,
      });
      const passing = r.results.filter((row) =>
        row.train_cagr_pct >= filters.minTrainCAGR &&
        row.fwd_cagr_pct >= filters.minFwdCAGR &&
        Math.min(row.train_dd_pct, row.fwd_dd_pct) >= filters.maxWorstDD &&
        row.n_trades >= filters.minTrades,
      ).length;
      logEvent({
        level: passing === 0 ? "warn" : "info",
        message: `Filters: ${passing}/${r.results.length} pass current filters`,
      });
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.status}: ${e.message}` : String(e);
      setError(msg);
      logEvent({ level: "error", message: `Fuzz failed: ${msg}` });
    } finally {
      setRunning(false);
    }
  }

  function loadRowIntoAnalyze(row: FuzzResultRow) {
    setCustomParams(row.params, `Leaderboard pick (score ${row.score.toFixed(1)})`);
    setView("analyze");
  }

  // Whenever the leaderboard rows are replaced (new run, load saved run,
  // all-time view) the seed-selection indices no longer point at anything
  // meaningful — clear them.
  useEffect(() => {
    setSelectedSeeds(new Set());
  }, [rows]);

  async function runRefine(
    seeds: Record<string, unknown>[],
    label: string,
    contextDescription: string,
  ) {
    if (seeds.length === 0) return;
    setRunning(true);
    setError(null);
    setCompleted(0);
    setShowAllTime(false);
    const t0 = performance.now();
    logEvent({ level: "info", message: `Refining ${contextDescription} (${nRuns} trials)…` });
    try {
      const r = await api.fuzz({
        strategy_kind: template?.kind ?? "etf",
        train_start: trainStart, train_end: trainEnd,
        fwd_start: fwdStart, fwd_end: fwdEnd,
        n_runs: nRuns, workers,
        // explore is ignored server-side when seed_params is set, but pass for
        // audit completeness.
        explore,
        exclude: goal.exclude,
        goal_id: goal.id,
        label,
        seed_params: seeds,
      });
      setRows(r.results);
      setCompleted(r.completed);
      setActiveRunId(r.run_id);
      void refreshRuns();
      const elapsed = ((performance.now() - t0) / 1000).toFixed(1);
      logEvent({
        level: "info",
        message: `Refine completed in ${elapsed}s — ${r.completed}/${nRuns}, top ${r.results[0]?.score?.toFixed(1) ?? "?"}${r.run_id ? ` (saved as #${r.run_id})` : ""}`,
      });
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.status}: ${e.message}` : String(e);
      setError(msg);
      logEvent({ level: "error", message: `Refine failed: ${msg}` });
    } finally {
      setRunning(false);
    }
  }

  async function refineSelected() {
    const seeds = Array.from(selectedSeeds).map((i) => rows[i]?.params).filter(Boolean) as Record<string, unknown>[];
    await runRefine(seeds, `refine of ${seeds.length} selected`, `${seeds.length} selected row${seeds.length === 1 ? "" : "s"}`);
  }

  async function refineTopFromRun(runId: number) {
    setError(null);
    let seeds: Record<string, unknown>[] = [];
    try {
      const detail = await api.fuzzRunDetail(runId, 5);
      seeds = detail.results.map((r) => r.params);
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.status}: ${e.message}` : String(e);
      setError(msg);
      logEvent({ level: "error", message: `Couldn't load run #${runId} top 5: ${msg}` });
      return;
    }
    if (seeds.length === 0) {
      logEvent({ level: "warn", message: `Run #${runId} has no results to refine.` });
      return;
    }
    await runRefine(
      seeds,
      `refine top ${seeds.length} of run #${runId}`,
      `top ${seeds.length} of run #${runId}`,
    );
  }

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-5">
      <header>
        <h1 className="text-2xl font-semibold">Find</h1>
        <p className="text-sm text-(--color-text-dim) max-w-2xl mt-1">
          Tell the fuzzer what kind of strategy you're looking for, then run a search.
          Each row in the leaderboard is one configuration scored on train and forward
          windows. Click any row to load it into Analyze.
        </p>
      </header>

      <GoalPanel
        selectedId={goalId}
        onSelect={(id) => {
          setGoalId(id);
          setFiltersTouched(false);
        }}
        filters={filters}
        onFilterChange={(k, v) => {
          setFilters((f) => ({ ...f, [k]: v }));
          setFiltersTouched(true);
        }}
        excludeCount={goal.exclude.length}
      />

      <CoveragePanel
        inv={inv}
        selected={selectedSymbols}
        windowSuggestion={winSugg}
        onToggle={(sym) => {
          setSelectedSymbols((prev) => {
            const next = new Set(prev);
            if (next.has(sym)) next.delete(sym);
            else next.add(sym);
            return next;
          });
        }}
        onResetToTemplate={() => {
          if (template) setSelectedSymbols(new Set(templateSymbols(template)));
        }}
        onDownloadMore={async (sym, start) => {
          logEvent({ level: "info", message: `Downloading more ${sym} history from ${start}…` });
          try {
            await api.download({ symbols: [sym], start, overwrite: true });
            await refreshInventory();
            logEvent({ level: "info", message: `Downloaded ${sym} from ${start}.` });
          } catch (e) {
            const msg = e instanceof ApiError ? `${e.status}: ${e.message}` : String(e);
            logEvent({ level: "error", message: `Download failed for ${sym}: ${msg}` });
          }
        }}
        onAddSymbol={async (sym, start) => {
          logEvent({ level: "info", message: `Adding ${sym} from ${start}…` });
          try {
            const r = await api.download({ symbols: [sym], start, overwrite: false });
            await refreshInventory();
            // Auto-select the new symbol so it joins the window calc
            setSelectedSymbols((prev) => new Set(prev).add(sym.toUpperCase()));
            const failed = r.failed.find(([s]) => s.toUpperCase() === sym.toUpperCase());
            if (failed) {
              logEvent({ level: "warn", message: `${sym}: ${failed[1]}` });
            } else {
              logEvent({ level: "info", message: `Added ${sym} (source: ${r.source_used[sym.toUpperCase()] ?? "?"}).` });
            }
          } catch (e) {
            const msg = e instanceof ApiError ? `${e.status}: ${e.message}` : String(e);
            logEvent({ level: "error", message: `Couldn't add ${sym}: ${msg}` });
          }
        }}
      />

      <WindowControls
        suggestion={winSugg}
        trainStart={trainStart}
        trainEnd={trainEnd}
        fwdStart={fwdStart}
        fwdEnd={fwdEnd}
        onChange={(k, v) => {
          if (k === "trainStart") setTrainStart(v);
          if (k === "trainEnd") setTrainEnd(v);
          if (k === "fwdStart") setFwdStart(v);
          if (k === "fwdEnd") setFwdEnd(v);
        }}
        onApplySuggestion={() => {
          if (winSugg?.available) {
            setTrainStart(winSugg.train_start!);
            setTrainEnd(winSugg.train_end!);
            setFwdStart(winSugg.fwd_start!);
            setFwdEnd(winSugg.fwd_end!);
          }
        }}
      />

      <FuzzControls
        nRuns={nRuns} setNRuns={setNRuns}
        workers={workers} setWorkers={setWorkers}
        explore={explore} setExplore={setExplore}
        onRun={runFuzz}
        running={running}
      />

      <RunHistoryPanel
        runs={runs}
        activeRunId={activeRunId}
        showAllTime={showAllTime}
        loading={loadingHistory}
        onLoadRun={loadHistoricalRun}
        onDeleteRun={deleteRun}
        onRefineTop={refineTopFromRun}
        refineDisabled={running}
      />

      {error && (
        <Card className="p-4 border-(--color-neg)/50 bg-(--color-neg)/10">
          <span className="text-(--color-neg) text-sm">{error}</span>
        </Card>
      )}

      <Leaderboard
        rows={filteredRows}
        allRows={rows}
        running={running}
        completed={completed}
        nRuns={nRuns}
        onClickRow={loadRowIntoAnalyze}
        filters={filters}
        selectedSeeds={selectedSeeds}
        onToggleSeed={(idx) => {
          setSelectedSeeds((prev) => {
            const next = new Set(prev);
            if (next.has(idx)) next.delete(idx);
            else next.add(idx);
            return next;
          });
        }}
        onClearSeeds={() => setSelectedSeeds(new Set())}
        onRefineSelected={refineSelected}
        showAllTime={showAllTime}
        loadingAllTime={loadingHistory && showAllTime}
        hasRuns={runs.length > 0}
        onLoadAllTime={loadAllTime}
        activeRunId={activeRunId}
      />

      <SessionLog events={events} onClear={() => setEvents([])} />
    </div>
  );
}

// ── Session event log ─────────────────────────────────────────────────────

interface SessionEvent {
  ts: string;          // HH:MM:SS
  level: "info" | "warn" | "error";
  message: string;
}

// ── Persistent run history (saved across browser sessions) ─────────────────

function RunHistoryPanel({
  runs,
  activeRunId,
  showAllTime,
  loading,
  onLoadRun,
  onDeleteRun,
  onRefineTop,
  refineDisabled,
}: {
  runs: FuzzRunMetaRow[];
  activeRunId: number | null;
  showAllTime: boolean;
  loading: boolean;
  onLoadRun: (id: number) => void;
  onDeleteRun: (id: number) => void;
  onRefineTop: (id: number) => void;
  refineDisabled: boolean;
}) {
  const [open, setOpen] = useState(runs.length > 0);
  useEffect(() => {
    // Auto-open the first time we get runs
    if (runs.length > 0) setOpen(true);
  }, [runs.length === 0]);

  return (
    <Card className="overflow-hidden">
      <div
        role="button"
        tabIndex={0}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setOpen((o) => !o); }}
        className="w-full px-4 py-2.5 flex items-center justify-between text-left hover:bg-(--color-surface-2)/40 cursor-pointer select-none"
      >
        <div className="flex items-center gap-3">
          <span className="text-sm font-medium text-(--color-text-dim) uppercase tracking-wider">
            Saved fuzz runs
          </span>
          {runs.length > 0 && (
            <span className="text-xs text-(--color-text-dim) tabular">
              {runs.length} {runs.length === 1 ? "run" : "runs"}
            </span>
          )}
        </div>
        <span className="text-xs text-(--color-text-dim)">
          {open ? "Hide ▴" : "Show ▾"}
        </span>
      </div>
      {open && (
        <div className="border-t border-(--color-border)">
          {loading && (
            <div className="px-4 py-4 text-xs text-(--color-text-dim) flex items-center gap-2">
              <Spinner /> Loading…
            </div>
          )}
          {!loading && runs.length === 0 && (
            <p className="px-4 py-3 text-xs text-(--color-text-dim)">
              Runs are saved automatically. Run a fuzz above to see history accumulate here.
            </p>
          )}
          {!loading && runs.length > 0 && (
            <ul className="divide-y divide-(--color-border)/40 max-h-72 overflow-y-auto">
              {runs.map((r) => {
                const active = r.id === activeRunId && !showAllTime;
                return (
                  <li
                    key={r.id}
                    className={`px-4 py-2 flex items-center justify-between gap-3 ${
                      active ? "bg-(--color-surface-2)/60" : "hover:bg-(--color-surface-2)/30"
                    }`}
                  >
                    <button
                      onClick={() => onLoadRun(r.id)}
                      className="flex-1 text-left flex items-center gap-3"
                    >
                      {active && <span className="text-(--color-accent) text-xs">●</span>}
                      <div className="flex-1">
                        <div className="text-sm font-medium tabular flex items-center gap-2">
                          #{r.id}
                          {r.label && (
                            <span className="text-(--color-text-dim) font-normal">— {r.label}</span>
                          )}
                          {r.goal_id && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-(--color-surface-2) text-(--color-text-dim) capitalize">
                              {r.goal_id}
                            </span>
                          )}
                        </div>
                        <div className="text-xs text-(--color-text-dim) tabular">
                          {new Date(r.ran_at + "Z").toLocaleString()} ·{" "}
                          {r.completed}/{r.n_runs} runs · top score{" "}
                          <span className="text-(--color-text)">
                            {r.top_score?.toFixed(1) ?? "?"}
                          </span>
                          {" · "}
                          {r.train_start.slice(0, 7)}→{r.fwd_end.slice(0, 7)}
                        </div>
                      </div>
                    </button>
                    <button
                      onClick={() => onRefineTop(r.id)}
                      disabled={refineDisabled || r.completed === 0}
                      title={r.completed === 0 ? "Run has no results to refine" : "Refine around the top 5 of this run"}
                      className="text-xs px-2 py-0.5 rounded border border-(--color-border) text-(--color-text-dim) hover:text-(--color-text) hover:border-(--color-text-dim) disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      Refine top 5
                    </button>
                    <button
                      onClick={() => onDeleteRun(r.id)}
                      title="Delete this run"
                      className="text-xs text-(--color-text-dim) hover:text-(--color-neg) px-1"
                    >
                      ✕
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </Card>
  );
}

function SessionLog({
  events,
  onClear,
}: {
  events: SessionEvent[];
  onClear: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Card className="overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full px-4 py-2 flex items-center justify-between text-left hover:bg-(--color-surface-2)/40"
      >
        <span className="text-sm font-medium text-(--color-text-dim) uppercase tracking-wider">
          Session log{events.length > 0 ? ` — ${events.length}` : ""}
        </span>
        <span className="text-xs text-(--color-text-dim)">{open ? "Hide ▴" : "Show ▾"}</span>
      </button>
      {open && (
        <div className="border-t border-(--color-border)">
          {events.length === 0 ? (
            <p className="px-4 py-3 text-xs text-(--color-text-dim)">
              Events appear here as you run fuzzes. Useful for understanding what the
              fuzzer did and why filters may have hidden everything.
            </p>
          ) : (
            <>
              <ul className="font-mono text-xs max-h-64 overflow-y-auto divide-y divide-(--color-border)/40">
                {events.map((ev, i) => (
                  <li key={i} className="px-4 py-1.5 flex gap-3">
                    <span className="text-(--color-text-dim) tabular shrink-0">{ev.ts}</span>
                    <span
                      className={
                        ev.level === "error"
                          ? "text-(--color-neg)"
                          : ev.level === "warn"
                            ? "text-(--color-warn)"
                            : "text-(--color-text)"
                      }
                    >
                      {ev.message}
                    </span>
                  </li>
                ))}
              </ul>
              <div className="px-4 py-2 border-t border-(--color-border) flex justify-end">
                <button
                  onClick={onClear}
                  className="text-xs text-(--color-text-dim) hover:text-(--color-text)"
                >
                  Clear
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </Card>
  );
}

// ── Stats helpers ──────────────────────────────────────────────────────────

function median(arr: number[]): number {
  if (arr.length === 0) return 0;
  const s = [...arr].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

function summarize(rows: FuzzResultRow[]) {
  if (rows.length === 0) return null;
  const trainCagr = rows.map((r) => r.train_cagr_pct);
  const fwdCagr = rows.map((r) => r.fwd_cagr_pct);
  const worst = rows.map((r) => Math.min(r.train_dd_pct, r.fwd_dd_pct));
  const trades = rows.map((r) => r.n_trades);
  return {
    n: rows.length,
    trainCagr: {
      min: Math.min(...trainCagr),
      median: median(trainCagr),
      max: Math.max(...trainCagr),
    },
    fwdCagr: {
      min: Math.min(...fwdCagr),
      median: median(fwdCagr),
      max: Math.max(...fwdCagr),
    },
    worstDd: {
      min: Math.min(...worst),
      median: median(worst),
      max: Math.max(...worst),
    },
    trades: {
      min: Math.min(...trades),
      median: median(trades),
      max: Math.max(...trades),
    },
  };
}

function diagnoseFilters(
  rows: FuzzResultRow[],
  filters: SearchGoal["filters"],
): string[] {
  if (rows.length === 0) return [];
  const tooLowTrain = rows.filter((r) => r.train_cagr_pct < filters.minTrainCAGR).length;
  const tooLowFwd = rows.filter((r) => r.fwd_cagr_pct < filters.minFwdCAGR).length;
  const tooDeepDd = rows.filter(
    (r) => Math.min(r.train_dd_pct, r.fwd_dd_pct) < filters.maxWorstDD,
  ).length;
  const tooFewTrades = rows.filter((r) => r.n_trades < filters.minTrades).length;
  const issues: string[] = [];
  if (tooLowTrain) issues.push(`${tooLowTrain} hidden by Min train CAGR ≥ ${filters.minTrainCAGR}%`);
  if (tooLowFwd) issues.push(`${tooLowFwd} hidden by Min forward CAGR ≥ ${filters.minFwdCAGR}%`);
  if (tooDeepDd) issues.push(`${tooDeepDd} hidden by Max drawdown −${Math.abs(filters.maxWorstDD)}%`);
  if (tooFewTrades) issues.push(`${tooFewTrades} hidden by Min trades ≥ ${filters.minTrades}`);
  return issues;
}

// ── Goal panel ─────────────────────────────────────────────────────────────

function GoalPanel({
  selectedId,
  onSelect,
  filters,
  onFilterChange,
  excludeCount,
}: {
  selectedId: string;
  onSelect: (id: string) => void;
  filters: SearchGoal["filters"];
  onFilterChange: <K extends keyof SearchGoal["filters"]>(
    k: K,
    v: SearchGoal["filters"][K],
  ) => void;
  excludeCount: number;
}) {
  const selected = findGoal(selectedId);
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-3">
        <div>
          <h2 className="text-sm font-medium text-(--color-text-dim) uppercase tracking-wider">
            What are you looking for?
          </h2>
          <p className="text-xs text-(--color-text-dim) mt-0.5 max-w-2xl">
            {selected.description}
          </p>
        </div>
      </div>

      <div className="flex gap-2 mb-4 flex-wrap">
        {GOALS.map((g) => {
          const active = g.id === selectedId;
          return (
            <button
              key={g.id}
              onClick={() => onSelect(g.id)}
              className={`px-3 py-1.5 text-sm rounded transition ${
                active
                  ? "bg-(--color-accent) text-white"
                  : "bg-(--color-surface-2) text-(--color-text-dim) hover:text-(--color-text) border border-(--color-border)"
              }`}
            >
              {g.name}
            </button>
          );
        })}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <FilterField
          label="Min train CAGR"
          value={filters.minTrainCAGR}
          onChange={(v) => onFilterChange("minTrainCAGR", v)}
          format={(v) => `${v >= 0 ? "≥" : "≤"} ${v}%`}
        />
        <FilterField
          label="Min forward CAGR"
          value={filters.minFwdCAGR}
          onChange={(v) => onFilterChange("minFwdCAGR", v)}
          format={(v) => `≥ ${v}%`}
        />
        <FilterField
          label="Max drawdown"
          // Input shows magnitude (positive), storage stays negative.
          // 40 in the input == "tolerate drawdowns no worse than -40%"
          value={Math.abs(filters.maxWorstDD)}
          onChange={(v) => onFilterChange("maxWorstDD", -Math.abs(v))}
          format={(v) => `down to −${Math.abs(v)}%`}
        />
        <FilterField
          label="Min trades"
          value={filters.minTrades}
          onChange={(v) => onFilterChange("minTrades", v)}
          format={(v) => `≥ ${v}`}
        />
      </div>

      {excludeCount > 0 && (
        <p className="text-xs text-(--color-text-dim) mt-3">
          → Fuzzer will exclude {excludeCount} symbols not allowed by this goal
          {selectedId === "steady" && " (3× ETFs and crypto)"}
          {selectedId === "balanced" && " (crypto only)"}
          .
        </p>
      )}
      <p className="text-xs text-(--color-text-dim) mt-1">
        Filters apply to the leaderboard after the run — adjust them anytime without re-running.
      </p>
    </Card>
  );
}

function FilterField({
  label,
  value,
  onChange,
  format,
}: {
  label: string;
  value: number;
  onChange: (n: number) => void;
  format: (v: number) => string;
}) {
  return (
    <div>
      <label className="block text-xs text-(--color-text-dim) mb-1">
        {label} <span className="text-(--color-text) tabular">{format(value)}</span>
      </label>
      <input
        type="number"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        step={5}
        className="bg-(--color-surface-2) border border-(--color-border) rounded px-3 py-1.5 text-sm tabular w-full"
      />
    </div>
  );
}

// ── Inventory panel ────────────────────────────────────────────────────────

// Data coverage panel — replaces the old summary-only InventoryPanel.
//
// Lists every daily symbol with checkboxes, sorts by has-data and then by
// shortest history (so the user can see right away which symbol is squashing
// their window). The selection set drives the window-suggester intersection
// in the parent. Per-row "Download more" lets users back-fill any short symbol
// without leaving Find, and "Add symbol" downloads a brand-new ticker.
function CoveragePanel({
  inv,
  selected,
  windowSuggestion,
  onToggle,
  onResetToTemplate,
  onDownloadMore,
  onAddSymbol,
}: {
  inv: InventoryResponse | null;
  selected: Set<string>;
  windowSuggestion: SuggestFuzzWindowResponse | null;
  onToggle: (sym: string) => void;
  onResetToTemplate: () => void;
  onDownloadMore: (sym: string, start: string) => Promise<void> | void;
  onAddSymbol: (sym: string, start: string) => Promise<void> | void;
}) {
  const [open, setOpen] = useState(true);
  const [addTicker, setAddTicker] = useState("");
  const [addStart, setAddStart] = useState("2018-01-01");
  const [busy, setBusy] = useState(false);
  // Per-row "Download more" inline date input + busy flag
  const [moreDate, setMoreDate] = useState<Record<string, string>>({});
  const [busyMore, setBusyMore] = useState<Record<string, boolean>>({});

  // Stable sort — must be above early returns (Rules of Hooks).
  // Symbols with data first, then by first_bar descending (shortest history =
  // most likely bottleneck). Does NOT re-sort on selection changes so toggling
  // a checkbox doesn't reset the scroll position.
  const rows = useMemo(
    () =>
      inv
        ? [...inv.symbols].sort((a, b) => {
            if (a.has_data !== b.has_data) return a.has_data ? -1 : 1;
            return (a.first_bar ?? "9999") > (b.first_bar ?? "9999") ? -1 : 1;
          })
        : [],
    [inv],
  );

  const allSelectableSymbols = rows.filter((s) => s.has_data).map((s) => s.symbol);
  const allSelected =
    allSelectableSymbols.length > 0 && allSelectableSymbols.every((s) => selected.has(s));

  function handleToggleAll() {
    if (allSelected) {
      allSelectableSymbols.forEach((s) => { if (selected.has(s)) onToggle(s); });
    } else {
      allSelectableSymbols.forEach((s) => { if (!selected.has(s)) onToggle(s); });
    }
  }

  if (!inv) {
    return (
      <Card className="p-4">
        <Spinner /> <span className="text-sm text-(--color-text-dim) ml-2">Scanning data…</span>
      </Card>
    );
  }
  if (inv.with_data === 0) {
    return <NoDataWarning />;
  }

  // Bottleneck: the selected symbol with the latest first_bar shrinks the window.
  const selectedRows = inv.symbols.filter((s) => s.has_data && selected.has(s.symbol));
  const bottleneck =
    selectedRows.length > 0
      ? selectedRows.reduce((acc, r) =>
          (r.first_bar ?? "") > (acc.first_bar ?? "") ? r : acc,
        selectedRows[0])
      : null;
  const windowIsTight =
    !!windowSuggestion &&
    windowSuggestion.available &&
    windowSuggestion.train_months + windowSuggestion.fwd_months < 36;

  async function handleDownloadMore(sym: string) {
    const start = moreDate[sym] ?? "2015-01-01";
    setBusyMore((m) => ({ ...m, [sym]: true }));
    try {
      await onDownloadMore(sym, start);
    } finally {
      setBusyMore((m) => ({ ...m, [sym]: false }));
    }
  }

  async function handleAddSymbol() {
    const sym = addTicker.trim().toUpperCase();
    if (!sym) return;
    setBusy(true);
    try {
      await onAddSymbol(sym, addStart);
      setAddTicker("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full px-4 py-3 flex items-center justify-between hover:bg-(--color-surface-2)/40"
      >
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-medium text-(--color-text-dim) uppercase tracking-wider">
            Data coverage
          </h2>
          <span className="text-xs text-(--color-text-dim) tabular">
            {selected.size} selected · {inv.with_data} with data · {inv.total} total
          </span>
        </div>
        <span className="text-xs text-(--color-text-dim)">{open ? "Hide ▴" : "Show ▾"}</span>
      </button>

      {open && (
        <div className="border-t border-(--color-border)">
          {bottleneck && bottleneck.first_bar && windowIsTight && (
            <div className="px-4 py-2 text-xs border-b border-(--color-warn)/40 bg-(--color-warn)/10 text-(--color-warn) flex items-center justify-between gap-3 flex-wrap">
              <span>
                Window bounded by <strong>{bottleneck.symbol}</strong> — data
                starts {bottleneck.first_bar}. Refine your selection or
                download earlier history.
              </span>
              <button
                onClick={() => handleDownloadMore(bottleneck.symbol)}
                disabled={busyMore[bottleneck.symbol]}
                className="text-xs underline hover:text-(--color-text)"
              >
                {busyMore[bottleneck.symbol] ? "Downloading…" : `Download more ${bottleneck.symbol}`}
              </button>
            </div>
          )}

          <div className="px-4 py-2 flex items-center justify-between gap-2 border-b border-(--color-border)/40 text-xs text-(--color-text-dim)">
            <span>
              Selection drives the suggested fuzz window (intersection of
              first/last bars across checked symbols).
            </span>
            <button onClick={onResetToTemplate} className="hover:text-(--color-text)">
              Reset to template
            </button>
          </div>

          <div className="max-h-72 overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="text-xs text-(--color-text-dim) uppercase tracking-wider sticky top-0 bg-(--color-surface)">
                <tr className="border-b border-(--color-border)">
                  <th className="w-8 px-2 py-2 text-center">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={handleToggleAll}
                      className="accent-(--color-accent) cursor-pointer"
                      title={allSelected ? "Deselect all" : "Select all"}
                    />
                  </th>
                  <th className="text-left px-3 py-2">Symbol</th>
                  <th className="text-left px-3 py-2">First bar</th>
                  <th className="text-left px-3 py-2">Last bar</th>
                  <th className="text-right px-3 py-2">Bars</th>
                  <th className="text-left px-3 py-2">Role</th>
                  <th className="text-right px-3 py-2">More history</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => {
                  const isBottleneck = bottleneck?.symbol === s.symbol && windowIsTight;
                  return (
                    <tr
                      key={s.symbol}
                      className={`border-b border-(--color-border)/30 ${
                        selected.has(s.symbol) ? "" : "opacity-60"
                      } ${isBottleneck ? "bg-(--color-warn)/5" : ""}`}
                    >
                      <td className="px-2 py-1.5 text-center">
                        <input
                          type="checkbox"
                          checked={selected.has(s.symbol)}
                          onChange={() => onToggle(s.symbol)}
                          disabled={!s.has_data}
                          className="accent-(--color-accent) cursor-pointer disabled:cursor-not-allowed"
                          aria-label={`Toggle ${s.symbol}`}
                        />
                      </td>
                      <td className="px-3 py-1.5 tabular font-medium">{s.symbol}</td>
                      <td className="px-3 py-1.5 tabular text-(--color-text-dim)">
                        {s.first_bar ?? "—"}
                      </td>
                      <td className="px-3 py-1.5 tabular text-(--color-text-dim)">
                        {s.last_bar ?? "—"}
                      </td>
                      <td className="px-3 py-1.5 text-right tabular text-(--color-text-dim)">
                        {s.n_bars > 0 ? s.n_bars : "—"}
                      </td>
                      <td className="px-3 py-1.5 text-xs text-(--color-text-dim)">{s.role || ""}</td>
                      <td className="px-3 py-1.5 text-right">
                        {s.has_data ? (
                          <div className="inline-flex items-center gap-1">
                            <input
                              type="date"
                              value={moreDate[s.symbol] ?? "2015-01-01"}
                              onChange={(e) =>
                                setMoreDate((m) => ({ ...m, [s.symbol]: e.target.value }))
                              }
                              className="bg-(--color-surface-2) border border-(--color-border) rounded px-1.5 py-0.5 text-xs tabular w-32"
                            />
                            <button
                              onClick={() => handleDownloadMore(s.symbol)}
                              disabled={busyMore[s.symbol]}
                              className="text-xs px-2 py-0.5 rounded border border-(--color-border) text-(--color-text-dim) hover:text-(--color-text) hover:border-(--color-text-dim) disabled:opacity-40"
                            >
                              {busyMore[s.symbol] ? "…" : "Download"}
                            </button>
                          </div>
                        ) : (
                          <span className="text-xs text-(--color-text-dim)">no data</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="px-4 py-3 border-t border-(--color-border)/40 flex items-center gap-2 flex-wrap">
            <span className="text-xs text-(--color-text-dim) mr-1">Add symbol:</span>
            <input
              type="text"
              value={addTicker}
              onChange={(e) => setAddTicker(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") void handleAddSymbol(); }}
              placeholder="e.g. NVDA"
              className="bg-(--color-surface-2) border border-(--color-border) rounded px-2 py-1 text-sm tabular w-28"
            />
            <input
              type="date"
              value={addStart}
              onChange={(e) => setAddStart(e.target.value)}
              className="bg-(--color-surface-2) border border-(--color-border) rounded px-2 py-1 text-sm tabular"
            />
            <Button size="sm" variant="ghost" onClick={handleAddSymbol} disabled={busy || !addTicker.trim()}>
              {busy ? <Spinner /> : "Download"}
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}

function NoDataWarning() {
  const setView = useApp((s) => s.setView);
  return (
    <Card className="p-5 border-(--color-warn)/40 bg-(--color-warn)/10">
      <h3 className="font-semibold text-(--color-warn) mb-2">No data yet</h3>
      <p className="text-sm text-(--color-text-dim) mb-3">
        The fuzzer needs daily price bars to backtest against. Head to{" "}
        <strong className="text-(--color-text)">Settings</strong> to download the core ETF
        universe — it takes about a minute with Alpaca paper keys (free) or yfinance.
      </p>
      <Button size="sm" onClick={() => setView("settings")}>
        Open Settings →
      </Button>
    </Card>
  );
}


// ── Window controls ────────────────────────────────────────────────────────

function WindowControls({
  suggestion,
  trainStart, trainEnd, fwdStart, fwdEnd,
  onChange,
  onApplySuggestion,
}: {
  suggestion: SuggestFuzzWindowResponse | null;
  trainStart: string; trainEnd: string; fwdStart: string; fwdEnd: string;
  onChange: (k: "trainStart" | "trainEnd" | "fwdStart" | "fwdEnd", v: string) => void;
  onApplySuggestion: () => void;
}) {
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h2 className="text-sm font-medium text-(--color-text-dim) uppercase tracking-wider">
          Train / forward windows
        </h2>
        {suggestion?.available && (
          <button
            onClick={onApplySuggestion}
            className="text-xs text-(--color-accent) hover:underline"
          >
            Apply smart suggestion
          </button>
        )}
      </div>

      {suggestion?.available && suggestion.notes.length > 0 && (
        <ul className="mb-3 text-xs text-(--color-text-dim) space-y-0.5">
          {suggestion.notes.map((n, i) => (
            <li key={i}>{i === 0 ? "→ " : "  · "}{n}</li>
          ))}
        </ul>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <DateField label="Train start" value={trainStart} onChange={(v) => onChange("trainStart", v)} />
        <DateField label="Train end"   value={trainEnd}   onChange={(v) => onChange("trainEnd", v)} />
        <DateField label="Forward start" value={fwdStart} onChange={(v) => onChange("fwdStart", v)} />
        <DateField label="Forward end"   value={fwdEnd}   onChange={(v) => onChange("fwdEnd", v)} />
      </div>
      <p className="text-xs text-(--color-text-dim) mt-3">
        Training is what the fuzzer optimizes on. The forward window is held back and
        only scored — it's your honest estimate of out-of-sample performance.
      </p>
    </Card>
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

// ── Fuzz controls ──────────────────────────────────────────────────────────

function FuzzControls({
  nRuns, setNRuns,
  workers, setWorkers,
  explore, setExplore,
  onRun,
  running,
}: {
  nRuns: number; setNRuns: (n: number) => void;
  workers: number; setWorkers: (n: number) => void;
  explore: number; setExplore: (n: number) => void;
  onRun: () => void;
  running: boolean;
}) {
  return (
    <Card className="p-4 flex flex-wrap items-end gap-5">
      <NumberField label="Trials" value={nRuns} onChange={setNRuns} min={10} max={2000} step={10} />
      <NumberField label="Workers" value={workers} onChange={setWorkers} min={1} max={12} />
      <SliderField
        label="Explore vs refine"
        value={explore}
        onChange={setExplore}
        min={0.1}
        max={0.95}
        step={0.05}
        format={(v) => `${Math.round(v * 100)}%`}
      />
      <div className="ml-auto">
        <Button onClick={onRun} disabled={running}>
          {running ? (
            <>
              <Spinner /> <span className="ml-2">Running…</span>
            </>
          ) : (
            "Run fuzz"
          )}
        </Button>
      </div>
    </Card>
  );
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
        className="bg-(--color-surface-2) border border-(--color-border) rounded px-3 py-1.5 text-sm tabular w-24"
      />
    </div>
  );
}

function SliderField({
  label, value, onChange, min, max, step, format,
}: {
  label: string; value: number;
  onChange: (n: number) => void;
  min: number; max: number; step: number;
  format: (v: number) => string;
}) {
  return (
    <div>
      <label className="block text-xs text-(--color-text-dim) mb-1">
        {label} <span className="tabular text-(--color-text)">{format(value)}</span>
      </label>
      <input
        type="range"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        min={min} max={max} step={step}
        className="w-40 accent-(--color-accent)"
      />
    </div>
  );
}

// ── Leaderboard ────────────────────────────────────────────────────────────

type SortKey = "score" | "train_cagr_pct" | "train_dd_pct" | "fwd_cagr_pct" | "fwd_dd_pct" | "n_trades";

function Leaderboard({
  rows, allRows, running, completed, nRuns, onClickRow, filters,
  selectedSeeds, onToggleSeed, onClearSeeds, onRefineSelected,
  showAllTime, loadingAllTime, hasRuns, onLoadAllTime, activeRunId,
}: {
  rows: FuzzResultRow[];
  allRows: FuzzResultRow[];
  running: boolean;
  completed: number;
  nRuns: number;
  onClickRow: (r: FuzzResultRow) => void;
  filters: SearchGoal["filters"];
  selectedSeeds: Set<number>;
  onToggleSeed: (idx: number) => void;
  onClearSeeds: () => void;
  onRefineSelected: () => void;
  showAllTime: boolean;
  loadingAllTime: boolean;
  hasRuns: boolean;
  onLoadAllTime: () => void;
  activeRunId: number | null;
}) {
  const [showAll, setShowAll] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("score");
  const [sortAsc, setSortAsc] = useState(false);

  function handleSort(key: SortKey) {
    if (sortKey === key) setSortAsc((a) => !a);
    else { setSortKey(key); setSortAsc(key === "train_dd_pct" || key === "fwd_dd_pct"); }
  }

  if (running) {
    return (
      <Card className="p-8 text-center">
        <Spinner />
        <p className="text-sm text-(--color-text-dim) mt-3">
          Running {nRuns} trials across workers… results will arrive in a batch when complete.
        </p>
      </Card>
    );
  }

  const totalRows = allRows.length;
  if (totalRows === 0) {
    return (
      <Card className="p-8 text-center">
        <p className="text-(--color-text-dim) text-sm">
          Pick a window, hit <strong>Run fuzz</strong>, and the leaderboard lands here.
        </p>
      </Card>
    );
  }

  const filteredOutEverything = rows.length === 0 && totalRows > 0;
  const baseRows = showAll || filteredOutEverything ? allRows : rows;
  const displayRows = [...baseRows].sort((a, b) => {
    const diff = a[sortKey] - b[sortKey];
    return sortAsc ? diff : -diff;
  });

  function SortTh({ col, label, align = "right" }: { col: SortKey; label: string; align?: "left" | "right" }) {
    const active = sortKey === col;
    const arrow = active ? (sortAsc ? " ▴" : " ▾") : "";
    return (
      <th
        className={`text-${align} px-4 py-2 cursor-pointer hover:text-(--color-text) select-none whitespace-nowrap ${active ? "text-(--color-text)" : ""}`}
        onClick={() => handleSort(col)}
        title={`Sort by ${label}`}
      >
        {label}{arrow}
      </th>
    );
  }

  return (
    <Card className="overflow-hidden">
      <div className="px-4 py-3 border-b border-(--color-border) flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-3 flex-wrap">
          <h2 className="text-sm font-medium text-(--color-text-dim) uppercase tracking-wider">
            Leaderboard
          </h2>
          {/* Mode toggle: Current run vs All-time */}
          <div className="flex rounded overflow-hidden border border-(--color-border) text-xs">
            <button
              onClick={() => { /* current run is already shown when !showAllTime */ }}
              disabled={!showAllTime}
              className={`px-3 py-1 transition ${
                !showAllTime
                  ? "bg-(--color-accent) text-white"
                  : "text-(--color-text-dim) hover:text-(--color-text)"
              }`}
            >
              {activeRunId ? `Run #${activeRunId}` : "Current run"}
            </button>
            <button
              onClick={onLoadAllTime}
              disabled={!hasRuns || loadingAllTime}
              title={!hasRuns ? "Run a fuzz first to build history" : "Best results across every saved run"}
              className={`px-3 py-1 transition border-l border-(--color-border) ${
                showAllTime
                  ? "bg-(--color-accent) text-white"
                  : "text-(--color-text-dim) hover:text-(--color-text) disabled:opacity-40 disabled:cursor-not-allowed"
              }`}
            >
              {loadingAllTime ? "Loading…" : "All-time"}
            </button>
          </div>
          <span className="text-xs text-(--color-text-dim) tabular">
            {displayRows.length}{displayRows.length !== totalRows ? ` of ${totalRows}` : ""} results
            {showAll && rows.length !== totalRows && (
              <span className="ml-1 text-(--color-warn)">(filters off)</span>
            )}
          </span>
        </div>
        <div className="flex items-center gap-3">
          {!filteredOutEverything && totalRows > rows.length && (
            <button
              onClick={() => setShowAll((s) => !s)}
              className="text-xs text-(--color-accent) hover:underline"
            >
              {showAll ? "Apply filters" : `Show all ${totalRows}`}
            </button>
          )}
          <button
            onClick={() => exportLeaderboardCsv(displayRows)}
            className="text-xs text-(--color-text-dim) hover:text-(--color-text) underline-offset-2 hover:underline"
            title="Download these rows as CSV"
          >
            Export CSV
          </button>
          <span className="text-xs text-(--color-text-dim) tabular">
            {completed} completed
          </span>
        </div>
      </div>

      {filteredOutEverything && (
        <FilterDiagnostics rows={allRows} filters={filters} />
      )}

      {selectedSeeds.size > 0 && (
        <div className="px-4 py-2 border-b border-(--color-accent)/40 bg-(--color-accent-dim)/30 flex items-center justify-between gap-3 flex-wrap">
          <span className="text-sm">
            <strong className="text-(--color-accent)">{selectedSeeds.size}</strong>{" "}
            row{selectedSeeds.size === 1 ? "" : "s"} selected as refinement seed
            {selectedSeeds.size === 1 ? "" : "s"}.
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={onClearSeeds}
              className="text-xs text-(--color-text-dim) hover:text-(--color-text)"
            >
              Clear
            </button>
            <Button size="sm" onClick={onRefineSelected} disabled={running}>
              {running ? <Spinner /> : `Refine ${selectedSeeds.size} selected`}
            </Button>
          </div>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs text-(--color-text-dim) uppercase tracking-wider">
            <tr className="border-b border-(--color-border)">
              <th className="px-2 py-2 w-8 text-center" title="Select rows as refinement seeds" />
              <th className="text-left px-4 py-2">#</th>
              <SortTh col="score" label="Score" />
              <SortTh col="train_cagr_pct" label="Train CAGR" />
              <SortTh col="train_dd_pct" label="Train DD" />
              <SortTh col="fwd_cagr_pct" label="Fwd CAGR" />
              <SortTh col="fwd_dd_pct" label="Fwd DD" />
              <SortTh col="n_trades" label="Trades" />
              <th className="text-left px-4 py-2">Pool</th>
              <th className="px-2 py-2" />
            </tr>
          </thead>
          <tbody>
            {displayRows.map((r, i) => {
              const pool = (r.params.risk_on_pool as string[] | undefined) ?? [];
              // selectedSeeds tracks indices into the original (pre-sort) baseRows array.
              // Find the original index so seed selection survives re-sorting.
              const origIdx = baseRows.indexOf(r);
              const checked = selectedSeeds.has(origIdx);
              return (
                <tr
                  key={origIdx}
                  className={`border-b border-(--color-border)/40 hover:bg-(--color-surface-2)/60 transition cursor-pointer ${
                    checked ? "bg-(--color-accent-dim)/30" : ""
                  }`}
                  onClick={() => onClickRow(r)}
                >
                  <td
                    className="px-2 py-2 text-center"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => onToggleSeed(origIdx)}
                      className="accent-(--color-accent) cursor-pointer"
                      aria-label="Use as refinement seed"
                    />
                  </td>
                  <td className="px-4 py-2 tabular text-(--color-text-dim)">{i + 1}</td>
                  <td className="px-4 py-2 text-right tabular font-medium">
                    {r.score.toFixed(1)}
                  </td>
                  <td className={`px-4 py-2 text-right tabular ${pctColor(r.train_cagr_pct)}`}>
                    {pct(r.train_cagr_pct)}
                  </td>
                  <td className="px-4 py-2 text-right tabular text-(--color-neg)">
                    {pct(r.train_dd_pct)}
                  </td>
                  <td className={`px-4 py-2 text-right tabular ${pctColor(r.fwd_cagr_pct)}`}>
                    {pct(r.fwd_cagr_pct)}
                  </td>
                  <td className="px-4 py-2 text-right tabular text-(--color-neg)">
                    {pct(r.fwd_dd_pct)}
                  </td>
                  <td className="px-4 py-2 text-right tabular text-(--color-text-dim)">
                    {r.n_trades}
                  </td>
                  <td className="px-4 py-2 text-xs text-(--color-text-dim) max-w-[280px] truncate">
                    {pool.slice(0, 4).join(", ")}
                    {pool.length > 4 ? ` +${pool.length - 4}` : ""}
                  </td>
                  <td className="px-2 py-2 text-(--color-accent) text-xs">→</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-(--color-text-dim) px-4 py-2 border-t border-(--color-border)">
        Click a row to load that strategy into Analyze.
      </p>
    </Card>
  );
}

// ── Filter diagnostics — shown when filters hide everything ────────────────

function FilterDiagnostics({
  rows,
  filters,
}: {
  rows: FuzzResultRow[];
  filters: SearchGoal["filters"];
}) {
  const s = summarize(rows);
  const issues = diagnoseFilters(rows, filters);
  if (!s) return null;

  const Range = ({
    label,
    range,
    fmt,
  }: {
    label: string;
    range: { min: number; median: number; max: number };
    fmt: (n: number) => string;
  }) => (
    <div className="text-xs">
      <div className="text-(--color-text-dim) mb-0.5">{label}</div>
      <div className="tabular">
        <span className={pctColor(range.min)}>{fmt(range.min)}</span>
        <span className="text-(--color-text-dim) mx-1.5">·</span>
        <span className="text-(--color-text)">{fmt(range.median)}</span>
        <span className="text-(--color-text-dim) mx-1.5">·</span>
        <span className={pctColor(range.max)}>{fmt(range.max)}</span>
      </div>
      <div className="text-(--color-text-dim) text-[10px] mt-0.5">min · median · max</div>
    </div>
  );

  return (
    <div className="px-4 py-4 bg-(--color-warn)/5 border-b border-(--color-warn)/30">
      <h3 className="text-sm font-medium text-(--color-warn) mb-2">
        All {rows.length} results were filtered out
      </h3>
      <p className="text-xs text-(--color-text-dim) mb-3">
        Here's what the fuzzer actually returned — adjust the filters above to
        match this range, or loosen the Goal preset.
      </p>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-3">
        <Range label="Train CAGR" range={s.trainCagr} fmt={(n) => pct(n)} />
        <Range label="Forward CAGR" range={s.fwdCagr} fmt={(n) => pct(n)} />
        <Range label="Worst drawdown" range={s.worstDd} fmt={(n) => pct(n)} />
        <Range
          label="Trades"
          range={s.trades}
          fmt={(n) => n.toFixed(0)}
        />
      </div>
      {issues.length > 0 && (
        <ul className="text-xs text-(--color-text-dim) space-y-0.5">
          {issues.map((it, i) => (
            <li key={i}>· {it}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function exportLeaderboardCsv(rows: FuzzResultRow[]): void {
  const headers = [
    "rank", "score",
    "train_cagr_pct", "train_dd_pct",
    "fwd_cagr_pct", "fwd_dd_pct",
    "n_trades", "params",
  ];
  const data = rows.map((r, i) => [
    i + 1, r.score.toFixed(2),
    r.train_cagr_pct.toFixed(2), r.train_dd_pct.toFixed(2),
    r.fwd_cagr_pct.toFixed(2), r.fwd_dd_pct.toFixed(2),
    r.n_trades, r.params,
  ]);
  downloadCsv(`stratscout-leaderboard-${todayStamp()}.csv`, toCsv(headers, data));
}
