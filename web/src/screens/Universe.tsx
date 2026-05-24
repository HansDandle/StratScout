import { useEffect, useState, useMemo } from "react";
import { RefreshCw, Download, CheckCircle, AlertTriangle, XCircle, Search } from "lucide-react";
import { api } from "../api";
import type { InventoryResponse, SymbolCoverageRow } from "../types";
import { Spinner } from "../components/ui";

// Pool membership — mirrors stratscout/engine/data/universes.py
const ANCHORS = new Set(["AGG", "BIL", "TLT"]);
const RISK_ON = new Set([
  "SOXL", "TQQQ", "UPRO", "TECL", "SPXL", "FAS", "CURE", "LABU",
  "ERX", "DRN", "FNGU", "UTSL", "MIDU", "TNA", "URTY",
  "MSTR", "GBTC", "BITX", "CONL", "IBIT",
  "JNUG", "GDXU", "SILJ", "SIL",
]);
const RISK_OFF_RISING = new Set(["QID", "TBF", "SQQQ", "TBT", "PSQ"]);
const RISK_OFF_FALLING = new Set(["UGL", "TMF", "BTAL", "XLP", "NUGT", "UUP", "GLD", "SLV"]);

type PoolFilter = "all" | "risk-on" | "risk-off-rising" | "risk-off-falling" | "anchor";
type CoverageFilter = "all" | "fresh" | "stale" | "missing";

function poolOf(sym: string): { label: string; color: string } {
  if (ANCHORS.has(sym))          return { label: "Anchor",         color: "var(--color-text-dim)" };
  if (RISK_ON.has(sym))          return { label: "Risk-On",        color: "var(--color-pos)" };
  if (RISK_OFF_RISING.has(sym))  return { label: "Risk-Off ↑",    color: "var(--color-warn)" };
  if (RISK_OFF_FALLING.has(sym)) return { label: "Risk-Off ↓",    color: "#60a5fa" };
  return                                  { label: "External",      color: "var(--color-text-dim)" };
}

function statusOf(row: SymbolCoverageRow): { icon: React.ReactNode; label: string; color: string } {
  if (!row.has_data)  return { icon: <XCircle size={13} />,        label: "No data",  color: "var(--color-neg)" };
  if (row.stale)      return { icon: <AlertTriangle size={13} />,  label: "Stale",    color: "var(--color-warn)" };
  return                      { icon: <CheckCircle size={13} />,   label: "Fresh",    color: "var(--color-pos)" };
}

export function Universe() {
  const [inv, setInv] = useState<InventoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [poolFilter, setPoolFilter] = useState<PoolFilter>("all");
  const [covFilter, setCovFilter] = useState<CoverageFilter>("all");
  const [enriching, setEnriching] = useState<Record<string, boolean>>({});
  const [enrichDone, setEnrichDone] = useState<Record<string, boolean>>({});
  const [enrichError, setEnrichError] = useState<Record<string, string>>({});
  const [refetchingAll, setRefetchingAll] = useState(false);

  async function load() {
    setLoading(true);
    try { setInv(await api.inventory()); }
    finally { setLoading(false); }
  }

  useEffect(() => { load(); }, []);

  // Build a coverage map: all known pool symbols + anything the API already has data for
  const rows: SymbolCoverageRow[] = useMemo(() => {
    const ALL_KNOWN = [
      ...Array.from(ANCHORS),
      ...Array.from(RISK_ON),
      ...Array.from(RISK_OFF_RISING),
      ...Array.from(RISK_OFF_FALLING),
    ];
    const knownSet = new Set(ALL_KNOWN);
    const bySymbol = new Map<string, SymbolCoverageRow>(
      (inv?.symbols ?? []).map((r) => [r.symbol, r])
    );
    // Start with the pool symbols in order
    const result = ALL_KNOWN.map(
      (sym) =>
        bySymbol.get(sym) ?? {
          symbol: sym, has_data: false, first_bar: null, last_bar: null,
          n_bars: 0, stale: false, sufficient_for_backtest: false,
          sufficient_for_walk_forward: false, role: "",
        }
    );
    // Append any extra symbols the API returned that aren't in known pools
    for (const row of inv?.symbols ?? []) {
      if (!knownSet.has(row.symbol)) result.push(row);
    }
    return result;
  }, [inv]);

  // Ticker typed in the search box that doesn't match any row yet
  const searchUpper = search.trim().toUpperCase();
  const unknownSearch = searchUpper.length >= 1 && !rows.some((r) => r.symbol === searchUpper)
    ? searchUpper : null;

  const filtered = useMemo(() => {
    return rows.filter((r) => {
      if (search && !r.symbol.toLowerCase().includes(search.toLowerCase())) return false;
      const pool = poolOf(r.symbol).label.toLowerCase();
      if (poolFilter === "risk-on"          && pool !== "risk-on")         return false;
      if (poolFilter === "risk-off-rising"  && pool !== "risk-off ↑")     return false;
      if (poolFilter === "risk-off-falling" && pool !== "risk-off ↓")     return false;
      if (poolFilter === "anchor"           && pool !== "anchor")          return false;
      if (covFilter === "fresh"   && (r.stale || !r.has_data))  return false;
      if (covFilter === "stale"   && !r.stale)                  return false;
      if (covFilter === "missing" && r.has_data)                return false;
      return true;
    });
  }, [rows, search, poolFilter, covFilter]);

  async function enrich(sym: string, overwrite = false) {
    setEnriching((p) => ({ ...p, [sym]: true }));
    setEnrichError((p) => { const n = { ...p }; delete n[sym]; return n; });
    try {
      await api.download({ symbols: [sym], start: "2018-01-01", overwrite });
      setEnrichDone((p) => ({ ...p, [sym]: true }));
      await load();
    } catch (e) {
      setEnrichError((p) => ({ ...p, [sym]: String(e) }));
    } finally {
      setEnriching((p) => ({ ...p, [sym]: false }));
    }
  }

  async function refetchAll() {
    const allSyms = rows.map((r) => r.symbol);
    setRefetchingAll(true);
    try {
      await api.download({ symbols: allSyms, start: "2018-01-01", overwrite: true });
      await load();
    } catch {
      // best-effort
    } finally {
      setRefetchingAll(false);
    }
  }

  const withData   = rows.filter((r) => r.has_data && !r.stale).length;
  const staleCount = rows.filter((r) => r.stale).length;
  const missing    = rows.filter((r) => !r.has_data).length;

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold">Universe</h1>
          <p className="text-sm mt-0.5" style={{ color: "var(--color-text-dim)" }}>
            {rows.length} symbols &nbsp;·&nbsp;
            <span style={{ color: "var(--color-pos)" }}>{withData} fresh</span>
            {staleCount > 0 && (
              <> &nbsp;·&nbsp; <span style={{ color: "var(--color-warn)" }}>{staleCount} stale</span></>
            )}
            {missing > 0 && (
              <> &nbsp;·&nbsp; <span style={{ color: "var(--color-neg)" }}>{missing} missing</span></>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={refetchAll}
            disabled={refetchingAll || loading}
            className="flex items-center gap-2 px-3 py-1.5 text-sm rounded-md border transition-colors hover:bg-white/5 disabled:opacity-50"
            style={{ borderColor: "var(--color-border)", color: "var(--color-text-dim)" }}
            title="Re-download all symbols from 2018-01-01, overwriting existing data"
          >
            {refetchingAll ? <Spinner /> : <Download size={13} />}
            Re-fetch all
          </button>
          <button
            onClick={load}
            disabled={loading}
            className="flex items-center gap-2 px-3 py-1.5 text-sm rounded-md border transition-colors hover:bg-white/5 disabled:opacity-50"
            style={{ borderColor: "var(--color-border)", color: "var(--color-text-dim)" }}
          >
            {loading ? <Spinner /> : <RefreshCw size={13} />}
            Refresh
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-4">
        {/* Search */}
        <div className="relative">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: "var(--color-text-dim)" }} />
          <input
            type="text"
            placeholder="Search symbol…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8 pr-3 py-1.5 text-sm rounded-md border outline-none bg-transparent focus:border-[var(--color-accent)] transition-colors"
            style={{ borderColor: "var(--color-border)", color: "var(--color-text)", width: 160 }}
          />
        </div>

        {/* Pool filter */}
        <FilterChips<PoolFilter>
          value={poolFilter}
          onChange={setPoolFilter}
          options={[
            { value: "all",              label: "All pools" },
            { value: "risk-on",          label: "Risk-On" },
            { value: "risk-off-rising",  label: "Risk-Off ↑" },
            { value: "risk-off-falling", label: "Risk-Off ↓" },
            { value: "anchor",           label: "Anchor" },
          ]}
        />

        {/* Coverage filter */}
        <FilterChips<CoverageFilter>
          value={covFilter}
          onChange={setCovFilter}
          options={[
            { value: "all",     label: "Any status" },
            { value: "fresh",   label: "Fresh" },
            { value: "stale",   label: "Stale" },
            { value: "missing", label: "Missing" },
          ]}
        />
      </div>

      {/* Table */}
      <div
        className="rounded-lg border overflow-hidden"
        style={{ borderColor: "var(--color-border)", background: "var(--color-surface)" }}
      >
        <table className="w-full text-sm">
          <thead>
            <tr
              className="text-xs uppercase tracking-wider border-b"
              style={{ borderColor: "var(--color-border)", color: "var(--color-text-dim)" }}
            >
              <th className="text-left px-4 py-2.5">Symbol</th>
              <th className="text-left px-4 py-2.5">Pool</th>
              <th className="text-left px-4 py-2.5">First bar</th>
              <th className="text-left px-4 py-2.5">Last bar</th>
              <th className="text-right px-4 py-2.5">Bars</th>
              <th className="text-left px-4 py-2.5">Status</th>
              <th className="text-left px-4 py-2.5"></th>
            </tr>
          </thead>
          <tbody>
            {loading && !inv && (
              <tr>
                <td colSpan={7} className="text-center py-10">
                  <Spinner />
                </td>
              </tr>
            )}
            {/* Unknown symbol typed in search — show an "Add" row */}
            {unknownSearch && (
              <tr className="border-b" style={{ borderColor: "var(--color-border)" }}>
                <td className="px-4 py-2.5 font-mono font-semibold">{unknownSearch}</td>
                <td className="px-4 py-2.5">
                  <span className="text-xs font-medium" style={{ color: "var(--color-text-dim)" }}>External</span>
                </td>
                <td className="px-4 py-2.5 tabular" style={{ color: "var(--color-text-dim)" }}>—</td>
                <td className="px-4 py-2.5 tabular" style={{ color: "var(--color-text-dim)" }}>—</td>
                <td className="px-4 py-2.5 text-right tabular" style={{ color: "var(--color-text-dim)" }}>—</td>
                <td className="px-4 py-2.5">
                  <span className="flex items-center gap-1.5 text-xs" style={{ color: "var(--color-text-dim)" }}>
                    <XCircle size={13} /> Not downloaded
                  </span>
                </td>
                <td className="px-4 py-2.5">
                  {enrichError[unknownSearch] ? (
                    <span className="text-xs" style={{ color: "var(--color-neg)" }} title={enrichError[unknownSearch]}>
                      Failed
                    </span>
                  ) : enrichDone[unknownSearch] ? (
                    <span className="text-xs" style={{ color: "var(--color-pos)" }}>Done</span>
                  ) : (
                    <button
                      onClick={() => enrich(unknownSearch, false)}
                      disabled={enriching[unknownSearch]}
                      className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded border transition-colors hover:bg-white/5 disabled:opacity-50"
                      style={{ borderColor: "var(--color-accent)", color: "var(--color-accent)" }}
                    >
                      {enriching[unknownSearch] ? <Spinner /> : <Download size={11} />}
                      Add {unknownSearch}
                    </button>
                  )}
                </td>
              </tr>
            )}
            {!loading && filtered.length === 0 && !unknownSearch && (
              <tr>
                <td colSpan={7} className="text-center py-10" style={{ color: "var(--color-text-dim)" }}>
                  No symbols match the current filters.
                </td>
              </tr>
            )}
            {filtered.map((row, i) => {
              const pool   = poolOf(row.symbol);
              const status = statusOf(row);
              const isBusy = enriching[row.symbol];
              const isDone = enrichDone[row.symbol];
              const err    = enrichError[row.symbol];

              return (
                <tr
                  key={row.symbol}
                  className="border-b transition-colors hover:bg-white/[0.02]"
                  style={{
                    borderColor: i === filtered.length - 1 ? "transparent" : "var(--color-border)",
                  }}
                >
                  <td className="px-4 py-2.5 font-mono font-semibold">{row.symbol}</td>
                  <td className="px-4 py-2.5">
                    <span className="text-xs font-medium" style={{ color: pool.color }}>
                      {pool.label}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 tabular" style={{ color: "var(--color-text-dim)" }}>
                    {row.first_bar ?? "—"}
                  </td>
                  <td className="px-4 py-2.5 tabular" style={{ color: "var(--color-text-dim)" }}>
                    {row.last_bar ?? "—"}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular" style={{ color: "var(--color-text-dim)" }}>
                    {row.n_bars > 0 ? row.n_bars.toLocaleString() : "—"}
                  </td>
                  <td className="px-4 py-2.5">
                    <span className="flex items-center gap-1.5 text-xs" style={{ color: status.color }}>
                      {status.icon}
                      {status.label}
                    </span>
                  </td>
                  <td className="px-4 py-2.5">
                    {err ? (
                      <span className="text-xs" style={{ color: "var(--color-neg)" }} title={err}>
                        Failed
                      </span>
                    ) : isDone ? (
                      <span className="text-xs" style={{ color: "var(--color-pos)" }}>Done</span>
                    ) : (
                      <button
                        onClick={() => enrich(row.symbol, row.has_data)}
                        disabled={isBusy}
                        className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded border transition-colors hover:bg-white/5 disabled:opacity-50"
                        style={{ borderColor: "var(--color-border)", color: "var(--color-text-dim)" }}
                      >
                        {isBusy ? <Spinner /> : <Download size={11} />}
                        {row.has_data ? "Re-fetch" : "Enrich"}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function FilterChips<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
}) {
  return (
    <div
      className="flex rounded-md border overflow-hidden"
      style={{ borderColor: "var(--color-border)" }}
    >
      {options.map((opt) => (
        <button
          key={opt.value}
          onClick={() => onChange(opt.value)}
          className="px-3 py-1.5 text-xs transition-colors border-r last:border-r-0"
          style={{
            borderColor: "var(--color-border)",
            background: value === opt.value ? "var(--color-surface-2)" : "transparent",
            color: value === opt.value ? "var(--color-text)" : "var(--color-text-dim)",
          }}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
