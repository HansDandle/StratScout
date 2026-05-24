// Live tab — saved strategies + trade-mode toggle gated by preflight checks.
//
// Sections:
//   1) "My strategies" list (active saved strategies)
//   2) For the active strategy: preflight checklist + trade-mode toggle
//
// Live activation is intentionally hard:
//   - Walk-forward must have run with active win rate >= 50% over >= 12 months
//   - Worst monthly DD <= configured threshold
//   - Paper trading history exists (informational gate until that ships)
//   - User has explicitly acknowledged risk (writes ACK_RISK to notes)
//
// Paper mode is reachable as long as you have a saved strategy and a connected
// broker (Settings tab). The gate is only on Live.

import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import { useApp } from "../store";
import { Button, Card, MetricTile, Spinner } from "../components/ui";
import { NavChart } from "../components/NavChart";
import { pct, money, pctColor } from "../format";
import { paramLabel, paramHint } from "../paramHints";
import type {
  PreflightCheckOut,
  PreflightResponse,
  ScheduleStatus,
  StrategyRow,
  TradeMode,
  TradeOrderRow,
  WalkForwardResponse,
} from "../types";

export function Live() {
  const setView = useApp((s) => s.setView);
  const activeSaved = useApp((s) => s.activeSavedStrategy);
  const setActiveSaved = useApp((s) => s.setActiveSavedStrategy);

  const [strategies, setStrategies] = useState<StrategyRow[] | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [preflight, setPreflight] = useState<PreflightResponse | null>(null);
  const [loadingPf, setLoadingPf] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refreshStrategies(currentShowArchived: boolean) {
    const r = await api.listStrategies(currentShowArchived);
    setStrategies(r.strategies);
    if (!activeSaved && r.strategies.length > 0) {
      setActiveSaved(r.strategies[0]);
    }
  }

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        if (!cancelled) await refreshStrategies(showArchived);
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e.message : String(e));
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showArchived]);

  async function toggleArchive(s: StrategyRow) {
    try {
      const updated = await api.updateStrategy(s.id, { archived: !s.archived });
      // If we're hiding archived and just archived the active one, clear it.
      if (!showArchived && updated.archived && activeSaved?.id === updated.id) {
        setActiveSaved(null);
      } else if (activeSaved?.id === updated.id) {
        setActiveSaved(updated);
      }
      await refreshStrategies(showArchived);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    }
  }

  // Recompute preflight whenever active strategy changes (or is updated)
  useEffect(() => {
    if (!activeSaved) {
      setPreflight(null);
      return;
    }
    let cancelled = false;
    setLoadingPf(true);
    void (async () => {
      try {
        const r = await api.preflight(activeSaved.id);
        if (!cancelled) setPreflight(r);
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e.message : String(e));
      } finally {
        if (!cancelled) setLoadingPf(false);
      }
    })();
    return () => { cancelled = true; };
  }, [activeSaved]);

  async function switchMode(mode: TradeMode) {
    if (!activeSaved) return;
    try {
      const updated = await api.updateStrategy(activeSaved.id, { trade_mode: mode });
      setActiveSaved(updated);
      setStrategies((prev) =>
        prev ? prev.map((s) => (s.id === updated.id ? updated : s)) : prev,
      );
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    }
  }

  async function acknowledgeRisk() {
    if (!activeSaved) return;
    const notes = (activeSaved.notes || "") + (activeSaved.notes ? " " : "") + "ACK_RISK";
    try {
      const updated = await api.updateStrategy(activeSaved.id, { notes });
      setActiveSaved(updated);
      // refresh preflight
      const r = await api.preflight(updated.id);
      setPreflight(r);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    }
  }

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-5">
      <header>
        <h1 className="text-2xl font-semibold">Live trading</h1>
        <p className="text-sm text-(--color-text-dim) max-w-2xl mt-1">
          Activate a saved strategy in Paper or Live mode. Live is gated by
          a preflight checklist designed to keep you from going live on a
          curve-fit strategy.
        </p>
      </header>

      {error && (
        <Card className="p-4 border-(--color-neg)/50 bg-(--color-neg)/10">
          <span className="text-(--color-neg) text-sm">{error}</span>
        </Card>
      )}

      <StrategiesList
        strategies={strategies}
        activeId={activeSaved?.id ?? null}
        showArchived={showArchived}
        onToggleArchived={() => setShowArchived((v) => !v)}
        onSelect={(s) => setActiveSaved(s)}
        onToggleArchive={toggleArchive}
      />

      <DailySchedulePanel />

      {activeSaved && (
        <>
          <ModeToggle
            mode={activeSaved.trade_mode}
            preflight={preflight}
            loadingPreflight={loadingPf}
            onModeChange={switchMode}
          />
          <StrategyDetailPanel strategy={activeSaved} />
          <TradeActivityPanel strategy={activeSaved} />
          <PreflightPanel
            preflight={preflight}
            loading={loadingPf}
            onAcknowledgeRisk={acknowledgeRisk}
            onRunWalkForward={() => setView("walkforward")}
          />
        </>
      )}

      {strategies !== null && strategies.length === 0 && (
        <Card className="p-8 text-center">
          <p className="text-(--color-text-dim) text-sm">
            No saved strategies yet. Head to <strong>Analyze</strong>, run a
            backtest, then click <strong>Save strategy</strong>.
          </p>
          <Button size="sm" onClick={() => setView("analyze")} className="mt-3">
            Open Analyze
          </Button>
        </Card>
      )}
    </div>
  );
}

function StrategiesList({
  strategies,
  activeId,
  showArchived,
  onSelect,
  onToggleArchive,
  onToggleArchived,
}: {
  strategies: StrategyRow[] | null;
  activeId: number | null;
  showArchived: boolean;
  onSelect: (s: StrategyRow) => void;
  onToggleArchive: (s: StrategyRow) => void;
  onToggleArchived: () => void;
}) {
  if (strategies === null) {
    return (
      <Card className="p-4">
        <Spinner /> <span className="text-sm text-(--color-text-dim) ml-2">Loading saved strategies…</span>
      </Card>
    );
  }
  if (strategies.length === 0 && !showArchived) return null;

  return (
    <Card className="overflow-hidden">
      <div className="px-4 py-3 border-b border-(--color-border) flex items-center justify-between gap-2">
        <h2 className="text-sm font-medium text-(--color-text-dim) uppercase tracking-wider">
          My strategies — {strategies.length}
        </h2>
        <button
          onClick={onToggleArchived}
          className="text-xs text-(--color-text-dim) hover:text-(--color-text) underline-offset-2 hover:underline"
        >
          {showArchived ? "Hide archived" : "Show archived"}
        </button>
      </div>
      {strategies.length === 0 ? (
        <div className="p-6 text-center text-sm text-(--color-text-dim)">
          No archived strategies.
        </div>
      ) : (
        <ul className="divide-y divide-(--color-border)/40">
          {strategies.map((s) => {
            const active = s.id === activeId;
            return (
              <li key={s.id}>
                <div
                  className={`px-4 py-3 flex items-center justify-between gap-3 hover:bg-(--color-surface-2)/40 ${
                    active ? "bg-(--color-surface-2)/60" : ""
                  } ${s.archived ? "opacity-60" : ""}`}
                >
                  <button
                    onClick={() => onSelect(s)}
                    className="flex items-center gap-3 flex-1 text-left"
                  >
                    {active && <span className="text-(--color-accent) text-xs">●</span>}
                    <div>
                      <div className="font-medium flex items-center gap-2">
                        {s.name}
                        {s.archived && (
                          <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded border border-(--color-border) text-(--color-text-dim)">
                            archived
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-(--color-text-dim)">
                        {s.kind} · updated {new Date(s.updated_at + "Z").toLocaleDateString()}
                      </div>
                    </div>
                  </button>
                  <div className="flex items-center gap-3">
                    <ModeBadge mode={s.trade_mode} />
                    <button
                      onClick={(e) => { e.stopPropagation(); onToggleArchive(s); }}
                      className="text-xs text-(--color-text-dim) hover:text-(--color-text) underline-offset-2 hover:underline"
                      title={s.archived ? "Restore from archive" : "Archive this strategy"}
                    >
                      {s.archived ? "Restore" : "Archive"}
                    </button>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function ModeToggle({
  mode,
  preflight,
  loadingPreflight,
  onModeChange,
}: {
  mode: TradeMode;
  preflight: PreflightResponse | null;
  loadingPreflight: boolean;
  onModeChange: (m: TradeMode) => void;
}) {
  const liveBlocked = !preflight || !preflight.passed || loadingPreflight;
  const tabs: { id: TradeMode; label: string; disabled?: boolean; hint?: string }[] = [
    { id: "off", label: "Off" },
    { id: "paper", label: "Paper trading" },
    {
      id: "live",
      label: "Live",
      disabled: liveBlocked,
      hint: liveBlocked ? "Pre-flight checks not passed" : undefined,
    },
  ];

  return (
    <Card className="p-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h2 className="text-sm font-medium text-(--color-text-dim) uppercase tracking-wider">
            Trade mode
          </h2>
          <p className="text-xs text-(--color-text-dim) mt-1">
            Off = no trades. Paper = simulated against your broker's paper account.
            Live = real money. Switching is instantaneous.
          </p>
        </div>
        <div className="inline-flex rounded-md border border-(--color-border) bg-(--color-surface) p-0.5">
          {tabs.map((t) => {
            const active = mode === t.id;
            return (
              <button
                key={t.id}
                disabled={t.disabled}
                onClick={() => onModeChange(t.id)}
                title={t.hint}
                className={`px-3 py-1.5 text-sm font-medium rounded transition ${
                  active
                    ? t.id === "live"
                      ? "bg-(--color-neg) text-white"
                      : t.id === "paper"
                        ? "bg-(--color-accent) text-white"
                        : "bg-(--color-surface-2) text-(--color-text)"
                    : "text-(--color-text-dim) hover:text-(--color-text) disabled:hover:text-(--color-text-dim) disabled:cursor-not-allowed disabled:opacity-40"
                }`}
              >
                {t.label}
              </button>
            );
          })}
        </div>
      </div>
    </Card>
  );
}

function ModeBadge({ mode }: { mode: TradeMode }) {
  const style = {
    off: "bg-(--color-text-dim)/15 text-(--color-text-dim) border-(--color-text-dim)/40",
    paper: "bg-(--color-accent-dim) text-(--color-accent) border-(--color-accent)/40",
    live: "bg-(--color-neg)/15 text-(--color-neg) border-(--color-neg)/40",
  }[mode];
  const label = { off: "Off", paper: "Paper", live: "Live" }[mode];
  return (
    <span className={`inline-flex items-center px-2 py-0.5 text-xs rounded-full border ${style}`}>
      {label}
    </span>
  );
}

function PreflightPanel({
  preflight,
  loading,
  onAcknowledgeRisk,
  onRunWalkForward,
}: {
  preflight: PreflightResponse | null;
  loading: boolean;
  onAcknowledgeRisk: () => void;
  onRunWalkForward: () => void;
}) {
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
        <h2 className="text-sm font-medium text-(--color-text-dim) uppercase tracking-wider">
          Pre-flight checks
        </h2>
        {preflight && (
          <StatusPill passed={preflight.passed} />
        )}
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-(--color-text-dim) text-sm">
          <Spinner /> Evaluating…
        </div>
      )}

      {!loading && preflight && (
        <ul className="space-y-2">
          {preflight.checks.map((c) => (
            <CheckRow
              key={c.id}
              check={c}
              onAcknowledgeRisk={onAcknowledgeRisk}
              onRunWalkForward={onRunWalkForward}
            />
          ))}
        </ul>
      )}
    </Card>
  );
}

function CheckRow({
  check,
  onAcknowledgeRisk,
  onRunWalkForward,
}: {
  check: PreflightCheckOut;
  onAcknowledgeRisk: () => void;
  onRunWalkForward: () => void;
}) {
  let action: (() => void) | null = null;
  if (!check.passed) {
    if (check.id === "risk_acknowledged") action = onAcknowledgeRisk;
    if (check.id === "walk_forward_present" || check.id === "active_win_rate" || check.id === "max_drawdown") {
      action = onRunWalkForward;
    }
  }

  return (
    <li className="flex items-start gap-3 px-3 py-2 rounded border border-(--color-border) bg-(--color-surface-2)/30">
      <span
        className={`inline-flex items-center justify-center w-5 h-5 rounded-full text-xs flex-shrink-0 mt-0.5 ${
          check.passed
            ? "bg-(--color-pos)/15 text-(--color-pos) border border-(--color-pos)/40"
            : "bg-(--color-text-dim)/15 text-(--color-text-dim) border border-(--color-text-dim)/40"
        }`}
      >
        {check.passed ? "✓" : "·"}
      </span>
      <div className="flex-1">
        <div className="font-medium text-sm">{check.label}</div>
        <div className="text-xs text-(--color-text-dim) mt-0.5">{check.hint}</div>
      </div>
      {action && (
        <Button size="sm" variant="ghost" onClick={action}>
          {check.fix_action}
        </Button>
      )}
    </li>
  );
}

function StatusPill({ passed }: { passed: boolean }) {
  if (passed) {
    return (
      <span className="inline-flex items-center px-2 py-0.5 text-xs rounded-full border bg-(--color-pos)/15 text-(--color-pos) border-(--color-pos)/40">
        All checks pass
      </span>
    );
  }
  return (
    <span className="inline-flex items-center px-2 py-0.5 text-xs rounded-full border bg-(--color-warn)/15 text-(--color-warn) border-(--color-warn)/40">
      Live blocked
    </span>
  );
}

// Strategy detail — the "click a strategy to see what it actually is" panel.
// Shows three things stacked: params (collapsible), latest walk-forward summary
// + equity chart, and an editable notes field for ad-hoc context.
function StrategyDetailPanel({ strategy }: { strategy: StrategyRow }) {
  const [wf, setWf] = useState<WalkForwardResponse | null>(null);
  const [loadingWf, setLoadingWf] = useState(false);
  const [noteDraft, setNoteDraft] = useState(strategy.notes);
  const [savingNotes, setSavingNotes] = useState(false);
  const [noteErr, setNoteErr] = useState<string | null>(null);

  useEffect(() => {
    setNoteDraft(strategy.notes);
    setNoteErr(null);
  }, [strategy.id, strategy.notes]);

  useEffect(() => {
    let cancelled = false;
    setWf(null);
    setLoadingWf(true);
    void (async () => {
      try {
        const r = await api.latestWalkForward(strategy.id);
        if (!cancelled) setWf(r);
      } catch (e) {
        // 404 = no WF yet for this strategy — not an error condition.
        if (!cancelled && e instanceof ApiError && e.status !== 404) {
          // eslint-disable-next-line no-console
          console.warn("latestWalkForward failed", e);
        }
      } finally {
        if (!cancelled) setLoadingWf(false);
      }
    })();
    return () => { cancelled = true; };
  }, [strategy.id]);

  async function saveNotes() {
    if (noteDraft === strategy.notes) return;
    setSavingNotes(true);
    setNoteErr(null);
    try {
      await api.updateStrategy(strategy.id, { notes: noteDraft });
    } catch (e) {
      setNoteErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSavingNotes(false);
    }
  }

  return (
    <Card className="p-4 space-y-4">
      <div>
        <h2 className="text-sm font-medium text-(--color-text-dim) uppercase tracking-wider">
          Strategy detail
        </h2>
        <div className="mt-1 flex items-baseline gap-3 flex-wrap">
          <h3 className="text-lg font-semibold">{strategy.name}</h3>
          <span className="text-xs text-(--color-text-dim)">
            {strategy.kind} · created {new Date(strategy.created_at + "Z").toLocaleDateString()}
          </span>
        </div>
      </div>

      <DetailParams params={strategy.params} />

      <div>
        <h4 className="text-xs uppercase tracking-wider text-(--color-text-dim) mb-2">
          Most recent walk-forward
        </h4>
        {loadingWf && (
          <div className="flex items-center gap-2 text-(--color-text-dim) text-sm">
            <Spinner /> Loading…
          </div>
        )}
        {!loadingWf && !wf && (
          <p className="text-sm text-(--color-text-dim)">
            No walk-forward saved yet for this strategy. Run one to populate the
            preflight checklist below.
          </p>
        )}
        {!loadingWf && wf && <WalkForwardSummary wf={wf} />}
      </div>

      <div>
        <h4 className="text-xs uppercase tracking-wider text-(--color-text-dim) mb-2">
          Notes
        </h4>
        <textarea
          value={noteDraft}
          onChange={(e) => setNoteDraft(e.target.value)}
          onBlur={saveNotes}
          placeholder="Anything you want to remember about this strategy…"
          rows={3}
          className="w-full bg-(--color-surface-2) border border-(--color-border) rounded p-2 text-sm font-mono"
        />
        <div className="flex items-center justify-between mt-1 text-xs text-(--color-text-dim)">
          <span>
            {noteDraft === strategy.notes
              ? "Saved"
              : savingNotes
                ? "Saving…"
                : "Unsaved · blur to save"}
          </span>
          {noteErr && <span className="text-(--color-neg)">{noteErr}</span>}
        </div>
      </div>
    </Card>
  );
}

function DetailParams({ params }: { params: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  const keys = Object.keys(params);
  const pools = keys.filter((k) => Array.isArray(params[k]));
  const scalars = keys.filter((k) => !Array.isArray(params[k]));
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="text-xs uppercase tracking-wider text-(--color-text-dim) hover:text-(--color-text)"
      >
        Parameters — {keys.length} {open ? "▴" : "▾"}
      </button>
      {open && (
        <div className="mt-2 space-y-3">
          {scalars.length > 0 && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-1.5">
              {scalars.map((k) => (
                <div key={k} className="flex items-baseline justify-between gap-2 text-sm">
                  <span className="text-(--color-text-dim) cursor-help" title={paramHint(k) || k}>
                    {paramLabel(k)}
                  </span>
                  <span className="tabular font-medium">
                    {typeof params[k] === "boolean"
                      ? params[k]
                        ? "yes"
                        : "no"
                      : String(params[k])}
                  </span>
                </div>
              ))}
            </div>
          )}
          {pools.map((k) => {
            const symbols = (params[k] as unknown[]).map(String);
            return (
              <div key={k}>
                <div
                  className="text-xs uppercase tracking-wider text-(--color-text-dim) mb-1 cursor-help"
                  title={paramHint(k) || k}
                >
                  {paramLabel(k)} — {symbols.length}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {symbols.map((s) => (
                    <span
                      key={s}
                      className="inline-flex items-center px-2 py-0.5 text-xs rounded border border-(--color-border) bg-(--color-surface-2)/40 tabular"
                    >
                      {s}
                    </span>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// Compact walk-forward summary inside the detail panel — tiles + equity curve.
// Mirrors the WalkForward screen's chart but slimmer.
function WalkForwardSummary({ wf }: { wf: WalkForwardResponse }) {
  const { stratIndex, stratValues, spyIndex, spyValues } = useMemo(() => {
    const stratIndex: string[] = [];
    const stratValues: number[] = [];
    const spyIndex: string[] = [];
    const spyValues: number[] = [];
    let strat = wf.starting_cash;
    let spy = wf.starting_cash;
    if (wf.rows.length > 0) {
      const first = wf.rows[0].month;
      stratIndex.push(first); stratValues.push(strat);
      spyIndex.push(first); spyValues.push(spy);
    }
    for (const r of wf.rows) {
      if (r.val_trades > 0) strat *= 1 + r.val_return_pct / 100;
      spy *= 1 + r.spy_return_pct / 100;
      const d = new Date(r.month);
      d.setMonth(d.getMonth() + 1);
      const stamp = d.toISOString().slice(0, 10);
      stratIndex.push(stamp); stratValues.push(strat);
      spyIndex.push(stamp); spyValues.push(spy);
    }
    return { stratIndex, stratValues, spyIndex, spyValues };
  }, [wf]);

  const stratPct = ((wf.final_equity / wf.starting_cash) - 1) * 100;
  const spyPct = ((wf.spy_equity / wf.starting_cash) - 1) * 100;
  const tone: "pos" | "neg" | "warn" =
    wf.active_win_rate >= 60 ? "pos"
    : wf.active_win_rate >= 40 ? "warn"
    : "neg";

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <MetricTile
          label="Win rate"
          value={`${wf.active_win_rate.toFixed(0)}%`}
          tone={tone}
          hint={`${wf.hits}/${wf.hits + wf.losses} traded`}
        />
        <MetricTile
          label="Strategy"
          value={money(wf.final_equity)}
          tone={stratPct >= 0 ? "pos" : "neg"}
          hint={pct(stratPct)}
        />
        <MetricTile
          label="SPY"
          value={money(wf.spy_equity)}
          hint={pct(spyPct)}
        />
        <MetricTile
          label="Months"
          value={String(wf.n_months)}
          hint={wf.ran_at ? `ran ${new Date(wf.ran_at + "Z").toLocaleDateString()}` : undefined}
        />
      </div>
      {stratValues.length > 1 && (
        <NavChart
          height={220}
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
        />
      )}
      {/* tiny dimmed line to make pctColor still useful (avoids unused-import warning) */}
      {wf.rows.length === 0 && (
        <span className={`text-xs ${pctColor(0)}`}>—</span>
      )}
    </div>
  );
}

// Trade activity — recent orders + dry/paper/live run actions.
// Dry-run records target intent without hitting the broker; paper / live
// diff against the broker's current positions and place market orders.
function TradeActivityPanel({ strategy }: { strategy: StrategyRow }) {
  const [orders, setOrders] = useState<TradeOrderRow[] | null>(null);
  const [running, setRunning] = useState<"dry" | "paper" | "live" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastNote, setLastNote] = useState<string | null>(null);

  async function refresh() {
    setError(null);
    try {
      const r = await api.listOrders(strategy.id, 50);
      setOrders(r.orders);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  useEffect(() => {
    setOrders(null);
    setLastNote(null);
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategy.id]);

  async function runIn(mode: "dry" | "paper" | "live") {
    setRunning(mode);
    setError(null);
    try {
      const r = await api.runStrategy(strategy.id, mode, "manual run");
      const summary = mode === "dry"
        ? `Recorded ${r.targets.length} target(s) — regime ${r.regime}, as of ${r.as_of}`
        : r.fell_back_to_dry
          ? `${mode}: broker not connected — targets recorded as rejected so you can see what would have been traded.`
          : `${mode}: placed ${r.placed}, failed ${r.failed}, regime ${r.regime}, as of ${r.as_of}`;
      setLastNote(summary);
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    } finally {
      setRunning(null);
    }
  }

  // Live is only meaningful when the strategy's trade_mode is 'live'. Paper is
  // always available given a connected broker — we don't gate it on trade_mode
  // because a user might want to test execution before flipping the toggle.
  const liveAllowed = strategy.trade_mode === "live";

  return (
    <Card className="overflow-hidden">
      <div className="px-4 py-3 border-b border-(--color-border) flex items-center justify-between flex-wrap gap-2">
        <div>
          <h2 className="text-sm font-medium text-(--color-text-dim) uppercase tracking-wider">
            Trade activity
          </h2>
          <p className="text-xs text-(--color-text-dim) mt-0.5">
            Dry-run records what the strategy would hold. Paper / live diff the
            target against broker positions and place market orders. Scheduling
            on a daily cron is the next step.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="ghost" onClick={() => runIn("dry")} disabled={running !== null}>
            {running === "dry" ? <Spinner /> : "Dry-run"}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => runIn("paper")} disabled={running !== null}>
            {running === "paper" ? <Spinner /> : "Run paper"}
          </Button>
          <Button
            size="sm"
            variant="danger"
            onClick={() => runIn("live")}
            disabled={running !== null || !liveAllowed}
            title={!liveAllowed ? "Switch the mode toggle to Live first" : "Place real-money orders now"}
          >
            {running === "live" ? <Spinner /> : "Run live"}
          </Button>
        </div>
      </div>
      {error && (
        <div className="px-4 py-2 text-sm text-(--color-neg) border-b border-(--color-border)">
          {error}
        </div>
      )}
      {lastNote && (
        <div className="px-4 py-2 text-xs text-(--color-text-dim) border-b border-(--color-border)">
          {lastNote}
        </div>
      )}
      {orders === null && (
        <div className="p-4 flex items-center gap-2 text-(--color-text-dim) text-sm">
          <Spinner /> Loading activity…
        </div>
      )}
      {orders !== null && orders.length === 0 && (
        <div className="p-6 text-center text-sm text-(--color-text-dim)">
          No activity yet. Click <strong>Dry-run</strong> to record the current targets, or <strong>Run paper</strong> to place simulated orders.
        </div>
      )}
      {orders !== null && orders.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs text-(--color-text-dim) uppercase tracking-wider">
              <tr className="border-b border-(--color-border)">
                <th className="text-left px-4 py-2">When</th>
                <th className="text-left px-4 py-2">Mode</th>
                <th className="text-left px-4 py-2">Action</th>
                <th className="text-left px-4 py-2">Symbol</th>
                <th className="text-right px-4 py-2">Qty</th>
                <th className="text-left px-4 py-2">Status</th>
                <th className="text-left px-4 py-2">Note</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.id} className="border-b border-(--color-border)/40">
                  <td className="px-4 py-2 tabular text-xs text-(--color-text-dim)">
                    {new Date(o.ran_at + "Z").toLocaleString()}
                  </td>
                  <td className="px-4 py-2 text-xs">{o.mode}</td>
                  <td className="px-4 py-2 text-xs">
                    <ActionBadge action={o.action} />
                  </td>
                  <td className="px-4 py-2 tabular font-medium">{o.symbol}</td>
                  <td className="px-4 py-2 text-right tabular text-(--color-text-dim)">
                    {o.qty ?? "—"}
                  </td>
                  <td className="px-4 py-2 text-xs text-(--color-text-dim)">{o.status}</td>
                  <td className="px-4 py-2 text-xs text-(--color-text-dim) max-w-[280px] truncate">
                    {o.message}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function ActionBadge({ action }: { action: string }) {
  const style: Record<string, string> = {
    BUY: "bg-(--color-pos)/15 text-(--color-pos) border-(--color-pos)/40",
    SELL: "bg-(--color-neg)/15 text-(--color-neg) border-(--color-neg)/40",
    TARGET: "bg-(--color-accent-dim) text-(--color-accent) border-(--color-accent)/40",
    HOLD: "bg-(--color-text-dim)/15 text-(--color-text-dim) border-(--color-text-dim)/40",
  };
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 text-xs rounded-full border ${style[action] ?? style.HOLD}`}
    >
      {action}
    </span>
  );
}

// Daily schedule panel — wraps the Windows Task Scheduler "StratScout Daily
// Run" task. When installed, every weekday at `run_time` Windows fires
// `python -m stratscout.engine.scheduled_run` which walks every strategy with
// trade_mode != off and executes it in its persisted mode.
function DailySchedulePanel() {
  const [status, setStatus] = useState<ScheduleStatus | null>(null);
  const [runTime, setRunTime] = useState("09:35");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      const s = await api.scheduleStatus();
      setStatus(s);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function install() {
    setBusy(true);
    setError(null);
    try {
      const s = await api.scheduleInstall(runTime);
      setStatus(s);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      const s = await api.scheduleRemove();
      setStatus(s);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!status) {
    return (
      <Card className="p-4 flex items-center gap-2 text-(--color-text-dim) text-sm">
        <Spinner /> Checking schedule…
      </Card>
    );
  }

  if (!status.supported) {
    return (
      <Card className="p-4 text-sm text-(--color-text-dim)">
        <h2 className="text-sm font-medium text-(--color-text-dim) uppercase tracking-wider mb-1">
          Daily schedule
        </h2>
        <p>
          Automated daily execution is wired through Windows Task Scheduler today.
          macOS (launchd) and Linux (cron / systemd-timer) support is coming —
          for now you can trigger runs manually from the Trade activity panel
          below.
        </p>
      </Card>
    );
  }

  return (
    <Card className="p-4 space-y-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-sm font-medium text-(--color-text-dim) uppercase tracking-wider">
            Daily schedule
          </h2>
          <p className="text-xs text-(--color-text-dim) mt-1 max-w-2xl">
            Runs <strong>every weekday</strong> at the chosen local time. Each
            fire walks every strategy with trade-mode set to Paper or Live and
            executes it. Set strategies you don't want traded to Off.
          </p>
        </div>
        <span
          className={`inline-flex items-center px-2 py-0.5 text-xs rounded-full border ${
            status.installed
              ? "bg-(--color-pos)/15 text-(--color-pos) border-(--color-pos)/40"
              : "bg-(--color-text-dim)/15 text-(--color-text-dim) border-(--color-text-dim)/40"
          }`}
        >
          {status.installed ? "Active" : "Not installed"}
        </span>
      </div>

      {status.installed && (
        <div className="text-xs text-(--color-text-dim) tabular grid grid-cols-1 md:grid-cols-3 gap-2">
          {status.next_run && <span>Next: {status.next_run}</span>}
          {status.run_time && <span>Run time: {status.run_time}</span>}
          {status.last_result && <span>Last result: {status.last_result}</span>}
        </div>
      )}

      <div className="flex items-end gap-2 flex-wrap">
        <div>
          <label className="block text-xs text-(--color-text-dim) mb-1">Run time (24-hour, local)</label>
          <input
            type="time"
            value={runTime}
            onChange={(e) => setRunTime(e.target.value)}
            className="bg-(--color-surface-2) border border-(--color-border) rounded px-3 py-1.5 text-sm tabular"
          />
        </div>
        <Button size="sm" onClick={install} disabled={busy}>
          {busy ? <Spinner /> : status.installed ? "Update schedule" : "Install schedule"}
        </Button>
        {status.installed && (
          <Button size="sm" variant="ghost" onClick={remove} disabled={busy}>
            Remove
          </Button>
        )}
      </div>

      {error && (
        <p className="text-sm text-(--color-neg)">{error}</p>
      )}
    </Card>
  );
}
