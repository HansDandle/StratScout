import { useEffect, useState, useCallback } from "react";
import { RefreshCw, Download, CheckCircle, XCircle, Minus, Trophy } from "lucide-react";
import { api } from "../api";
import type { FactorRow } from "../types";

const FMT = (v: number | null, digits = 4) =>
  v == null || Number.isNaN(v) ? "—" : v.toFixed(digits);

const FMT_SIGNED = (v: number | null, digits = 4) =>
  v == null || Number.isNaN(v)
    ? "—"
    : (v >= 0 ? "+" : "") + v.toFixed(digits);

function SigBadge({ sig }: { sig: boolean | null }) {
  if (sig === null) return <Minus size={14} style={{ color: "var(--color-text-dim)" }} />;
  return sig
    ? <CheckCircle size={14} style={{ color: "var(--color-pos)" }} />
    : <XCircle size={14} style={{ color: "var(--color-text-dim)" }} />;
}

function IcBar({ ic }: { ic: number | null }) {
  if (ic == null || Number.isNaN(ic)) return <span style={{ color: "var(--color-text-dim)" }}>—</span>;
  const pct = Math.min(Math.abs(ic) * 100 / 0.5, 100);
  const color = Math.abs(ic) >= 0.15
    ? (ic > 0 ? "var(--color-pos)" : "var(--color-neg)")
    : "var(--color-text-dim)";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div style={{ width: 60, height: 6, background: "var(--color-border)", borderRadius: 3, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 3 }} />
      </div>
      <span style={{ fontSize: 11, color, fontVariantNumeric: "tabular-nums" }}>
        {FMT_SIGNED(ic, 3)}
      </span>
    </div>
  );
}

interface SurvivorGroup {
  factor_key: string;        // "fear_greed + google_trends_layoffs"
  factor_set: string[];      // ["fear_greed", "google_trends_layoffs"]
  seen_count: number;        // how many distinct recipes used this combination
  best_ic: number;
  best_ic_oos: number | null;
  best_name: string;         // recipe with best IC
  significant_count: number;
  recipe_count: number;
}

export function FactorLab() {
  const [tab, setTab] = useState<"factors" | "survivors">("factors");
  const [factors, setFactors] = useState<FactorRow[]>([]);
  const [survivors, setSurvivors] = useState<SurvivorGroup[]>([]);
  const [survivorMeta, setSurvivorMeta] = useState<{ total_unique: number; total_rows: number } | null>(null);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [msg, setMsg] = useState("");
  const [sortBy, setSortBy] = useState<"abs_ic" | "name" | "tier">("abs_ic");
  const [nDerived, setNDerived] = useState(200);
  const [showDerived, setShowDerived] = useState(false);

  const loadSurvivors = useCallback(() => {
    fetch("/api/factors/survivors?top_n=100")
      .then((r) => r.json())
      .then((d) => {
        setSurvivors(d.groups ?? []);
        setSurvivorMeta({ total_unique: d.total_unique, total_rows: d.total_rows });
      })
      .catch(() => {});
  }, []);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      api.factorsList().then((r) => setFactors(r.factors)),
      fetch("/api/factors/survivors?top_n=100").then((r) => r.json()).then((d) => {
        setSurvivors(d.groups ?? []);
        setSurvivorMeta({ total_unique: d.total_unique, total_rows: d.total_rows });
      }),
    ])
      .catch(() => setMsg("Failed to load — is the API running?"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const downloadTier = async (tier: number | null, opts?: { derive?: boolean; clearDerived?: boolean }) => {
    setDownloading(true);
    setMsg("");
    try {
      const r = await api.factorsDownload({
        tier: tier ?? undefined,
        derive: opts?.derive,
        n_derived: opts?.derive ? nDerived : undefined,
        clear_derived: opts?.clearDerived,
      });
      setMsg(r.message + (r.failed.length ? ` — failed: ${r.failed.join(", ")}` : ""));
      load();
    } catch {
      setMsg("Download failed.");
    } finally {
      setDownloading(false);
    }
  };

  const displayed = showDerived ? factors : factors.filter((f) => !f.name.startsWith("d__"));
  const sorted = [...displayed].sort((a, b) => {
    if (sortBy === "abs_ic") return (b.abs_ic ?? -1) - (a.abs_ic ?? -1);
    if (sortBy === "tier") return a.tier - b.tier || a.name.localeCompare(b.name);
    return a.name.localeCompare(b.name);
  });

  const bonferroniCount = factors.filter((f) => f.has_data).length || factors.length;
  const bonferroniLine = 1.96 / Math.sqrt(bonferroniCount > 0 ? bonferroniCount : 1); // rough: IC threshold for significance

  return (
    <div style={{ padding: "24px 32px", maxWidth: 1100 }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
        <h1 style={{ fontSize: 20, fontWeight: 600, margin: 0 }}>Factor Lab</h1>
        {/* Tabs */}
        <div style={{ display: "flex", gap: 4, marginLeft: 16 }}>
          {([["factors", "All Factors"], ["survivors", "Survivors"]] as const).map(([id, label]) => (
            <button key={id} onClick={() => setTab(id)} style={{
              padding: "4px 14px", borderRadius: 6, fontSize: 13, cursor: "pointer",
              fontWeight: tab === id ? 600 : 400,
              background: tab === id ? "var(--color-accent)" : "var(--color-surface)",
              color: tab === id ? "#fff" : "var(--color-text)",
              border: "1px solid var(--color-border)",
            }}>
              {id === "survivors" && <Trophy size={11} style={{ marginRight: 4, verticalAlign: "middle" }} />}
              {label}
              {id === "survivors" && survivorMeta && survivorMeta.total_unique > 0 && (
                <span style={{ marginLeft: 6, fontSize: 11, opacity: 0.8 }}>({survivorMeta.total_unique})</span>
              )}
            </button>
          ))}
        </div>
        <button
          onClick={load}
          disabled={loading}
          style={{
            display: "flex", alignItems: "center", gap: 4, padding: "4px 10px",
            background: "var(--color-surface)", border: "1px solid var(--color-border)",
            borderRadius: 6, cursor: "pointer", fontSize: 12, color: "var(--color-text)",
          }}
        >
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
        <button
          onClick={() => downloadTier(1)}
          disabled={downloading}
          style={{
            display: "flex", alignItems: "center", gap: 4, padding: "4px 10px",
            background: "var(--color-accent)", border: "none",
            borderRadius: 6, cursor: "pointer", fontSize: 12, color: "#fff",
          }}
        >
          <Download size={12} />
          Generate Tier 1
        </button>
        <button
          onClick={() => downloadTier(2)}
          disabled={downloading}
          style={{
            display: "flex", alignItems: "center", gap: 4, padding: "4px 10px",
            background: "var(--color-surface)", border: "1px solid var(--color-border)",
            borderRadius: 6, cursor: "pointer", fontSize: 12, color: "var(--color-text)",
          }}
        >
          <Download size={12} />
          Download Tier 2
        </button>

        <div style={{ display: "flex", alignItems: "center", gap: 6, marginLeft: 8, borderLeft: "1px solid var(--color-border)", paddingLeft: 12 }}>
          <input
            type="number"
            min={10} max={2000} step={50}
            value={nDerived}
            onChange={(e) => setNDerived(Number(e.target.value))}
            style={{
              width: 60, padding: "3px 6px", borderRadius: 4, fontSize: 12,
              background: "var(--color-surface)", border: "1px solid var(--color-border)",
              color: "var(--color-text)",
            }}
          />
          <button
            onClick={() => downloadTier(null, { derive: true })}
            disabled={downloading}
            style={{
              display: "flex", alignItems: "center", gap: 4, padding: "4px 10px",
              background: "var(--color-surface)", border: "1px solid var(--color-border)",
              borderRadius: 6, cursor: "pointer", fontSize: 12, color: "var(--color-text)",
            }}
          >
            <Download size={12} />
            Derive combos
          </button>
          <button
            onClick={() => downloadTier(null, { derive: true, clearDerived: true })}
            disabled={downloading}
            style={{
              display: "flex", alignItems: "center", gap: 4, padding: "4px 10px",
              background: "var(--color-surface)", border: "1px solid var(--color-border)",
              borderRadius: 6, cursor: "pointer", fontSize: 12, color: "var(--color-text)",
            }}
          >
            Re-roll
          </button>
        </div>
      </div>

      {tab === "factors" && <p style={{ fontSize: 13, color: "var(--color-text-dim)", marginBottom: 4 }}>
        IC (Information Coefficient) = Spearman correlation of factor vs SPY next-month return.
        Bonferroni-corrected for {bonferroniCount} factors. Most will die here — that's the point.
      </p>}

      {msg && (
        <div style={{
          padding: "8px 12px", marginBottom: 12, borderRadius: 6,
          background: "var(--color-surface)", border: "1px solid var(--color-border)",
          fontSize: 13, color: "var(--color-text)",
        }}>{msg}</div>
      )}

      {tab === "factors" && <>
        <IcChart factors={sorted} bonferroniLine={bonferroniLine} />

        {/* Sort + filter controls */}
        <div style={{ display: "flex", gap: 8, marginBottom: 8, marginTop: 20, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ fontSize: 12, color: "var(--color-text-dim)" }}>Sort:</span>
          {(["abs_ic", "tier", "name"] as const).map((k) => (
            <button key={k} onClick={() => setSortBy(k)} style={{
              padding: "2px 8px", borderRadius: 4, fontSize: 12, cursor: "pointer",
              background: sortBy === k ? "var(--color-accent)" : "var(--color-surface)",
              color: sortBy === k ? "#fff" : "var(--color-text)",
              border: "1px solid var(--color-border)",
            }}>
              {k === "abs_ic" ? "|IC|" : k === "tier" ? "Tier" : "Name"}
            </button>
          ))}
          <div style={{ marginLeft: 12, borderLeft: "1px solid var(--color-border)", paddingLeft: 12 }}>
            <label style={{ fontSize: 12, color: "var(--color-text-dim)", display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
              <input type="checkbox" checked={showDerived} onChange={(e) => setShowDerived(e.target.checked)} />
              Show derived combos ({factors.filter((f) => f.name.startsWith("d__")).length})
            </label>
          </div>
        </div>

        {/* Table */}
        <div style={{ border: "1px solid var(--color-border)", borderRadius: 8, overflow: "hidden", fontSize: 12 }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: "var(--color-surface)", color: "var(--color-text-dim)" }}>
                <Th>Factor</Th><Th>Tier</Th><Th>Data</Th><Th>Current</Th>
                <Th>IC (full)</Th><Th>IC OOS</Th><Th>IC Bull</Th><Th>IC Bear</Th><Th>p-adj</Th><Th>Sig</Th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((f, i) => (
                <tr key={f.name} style={{ background: i % 2 === 0 ? "transparent" : "var(--color-surface)", borderTop: "1px solid var(--color-border)" }}>
                  <td style={{ padding: "7px 10px" }}>
                    <div style={{ fontWeight: 500 }}>{f.name}</div>
                    <div style={{ fontSize: 11, color: "var(--color-text-dim)", marginTop: 2 }}>{f.hypothesis}</div>
                  </td>
                  <Td><span style={{ padding: "1px 6px", borderRadius: 4, fontSize: 11, background: f.tier === 1 ? "var(--color-surface)" : "var(--color-accent)20", color: f.tier === 1 ? "var(--color-text-dim)" : "var(--color-accent)", border: "1px solid var(--color-border)" }}>T{f.tier}</span></Td>
                  <Td>{f.has_data ? <span style={{ color: "var(--color-pos)" }}>{f.last_date ?? "yes"}</span> : <span style={{ color: "var(--color-neg)" }}>missing</span>}</Td>
                  <Td>{f.current_value != null ? f.current_value.toFixed(2) : "—"}</Td>
                  <td style={{ padding: "7px 10px" }}><IcBar ic={f.ic} /></td>
                  <Td style={{ color: getIcColor(f.ic_oos) }}>{FMT_SIGNED(f.ic_oos, 3)}</Td>
                  <Td style={{ color: getIcColor(f.ic_bull) }}>{FMT_SIGNED(f.ic_bull, 3)}</Td>
                  <Td style={{ color: getIcColor(f.ic_bear) }}>{FMT_SIGNED(f.ic_bear, 3)}</Td>
                  <Td>{FMT(f.p_bonferroni, 3)}</Td>
                  <Td><SigBadge sig={f.significant ?? null} /></Td>
                </tr>
              ))}
            </tbody>
          </table>
          {factors.length === 0 && !loading && (
            <div style={{ padding: 32, textAlign: "center", color: "var(--color-text-dim)" }}>
              No factors loaded. Click "Generate Tier 1" to compute calculable factors.
            </div>
          )}
        </div>
        <p style={{ marginTop: 12, fontSize: 11, color: "var(--color-text-dim)" }}>
          Tier 1: computed from date math, no internet required.{" "}
          Tier 2: downloaded from free APIs (Fear &amp; Greed, sunspot counts, Google Trends). Pytrends required for Google Trends.
        </p>
      </>}

      {tab === "survivors" && (
        <SurvivorsPanel survivors={survivors} meta={survivorMeta} onRefresh={loadSurvivors} />
      )}
    </div>
  );
}

function getIcColor(ic: number | null): string {
  if (ic == null || Number.isNaN(ic)) return "var(--color-text-dim)";
  if (Math.abs(ic) < 0.1) return "var(--color-text-dim)";
  return ic > 0 ? "var(--color-pos)" : "var(--color-neg)";
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th style={{ padding: "8px 10px", textAlign: "left", fontWeight: 500, whiteSpace: "nowrap" }}>
      {children}
    </th>
  );
}

function Td({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <td style={{ padding: "7px 10px", whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums", ...style }}>
      {children}
    </td>
  );
}

function IcChart({ factors, bonferroniLine }: { factors: FactorRow[]; bonferroniLine: number }) {
  const withIc = factors.filter((f) => f.abs_ic != null && !Number.isNaN(f.abs_ic));
  if (withIc.length === 0) return null;

  const maxIc = Math.max(...withIc.map((f) => f.abs_ic ?? 0), 0.05);
  const chartH = 120;
  const barW = Math.max(18, Math.min(40, Math.floor(520 / withIc.length)));
  const gap = 3;
  const totalW = withIc.length * (barW + gap);
  const bonferroniPx = (bonferroniLine / maxIc) * chartH;

  return (
    <div style={{
      padding: 16, background: "var(--color-surface)", borderRadius: 8,
      border: "1px solid var(--color-border)", marginBottom: 4, overflowX: "auto",
    }}>
      <div style={{ fontSize: 12, color: "var(--color-text-dim)", marginBottom: 8 }}>
        |IC| by factor — dashed line = Bonferroni significance threshold
      </div>
      <svg width={totalW} height={chartH + 32} style={{ display: "block" }}>
        {/* Bonferroni line */}
        <line
          x1={0} x2={totalW}
          y1={chartH - bonferroniPx} y2={chartH - bonferroniPx}
          stroke="var(--color-accent)" strokeWidth={1} strokeDasharray="4 3" opacity={0.7}
        />
        {withIc.map((f, i) => {
          const h = Math.max(2, ((f.abs_ic ?? 0) / maxIc) * chartH);
          const x = i * (barW + gap);
          const y = chartH - h;
          const aboveThreshold = (f.abs_ic ?? 0) > bonferroniLine;
          const color = aboveThreshold
            ? "var(--color-pos)"
            : f.significant
              ? "var(--color-accent)"
              : "var(--color-border)";
          return (
            <g key={f.name}>
              <rect x={x} y={y} width={barW} height={h} fill={color} rx={2} />
              <text
                x={x + barW / 2} y={chartH + 14}
                textAnchor="middle" fontSize={9}
                fill="var(--color-text-dim)"
                transform={`rotate(-45, ${x + barW / 2}, ${chartH + 14})`}
              >
                {f.name.replace(/_/g, " ").slice(0, 18)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

type SurvivorSort = "seen" | "abs_ic" | "ic_oos" | "p_adj";

function SurvivorsPanel({
  survivors,
  meta,
  onRefresh,
}: {
  survivors: SurvivorGroup[];
  meta: { total_unique: number; total_rows: number } | null;
  onRefresh: () => void;
}) {
  const [sortBy, setSortBy] = useState<SurvivorSort>("seen");

  const sorted = [...survivors].sort((a, b) => {
    if (sortBy === "seen")   return b.seen_count - a.seen_count;
    if (sortBy === "abs_ic") return Math.abs(b.best_ic) - Math.abs(a.best_ic);
    if (sortBy === "ic_oos") return Math.abs(b.best_ic_oos ?? 0) - Math.abs(a.best_ic_oos ?? 0);
    if (sortBy === "p_adj")  return b.significant_count - a.significant_count;
    return 0;
  });

  if (!meta) {
    return (
      <div style={{ padding: 40, textAlign: "center", color: "var(--color-text-dim)", fontSize: 13 }}>
        No survivors log yet.<br /><br />
        Run the loop from the terminal:<br />
        <code style={{ background: "var(--color-surface)", padding: "4px 8px", borderRadius: 4, fontSize: 12 }}>
          python -m stratscout.engine.data.factor_backtest loop 200
        </code>
        <br /><br />
        It re-rolls random combinations each round and saves anything worth keeping automatically.
      </div>
    );
  }

  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
        <span style={{ fontSize: 13, color: "var(--color-text-dim)" }}>
          {survivors.length} factor combinations from {meta.total_unique} unique recipes · {meta.total_rows} total tests
        </span>
        <button onClick={onRefresh} style={{
          display: "flex", alignItems: "center", gap: 4, padding: "3px 8px",
          background: "var(--color-surface)", border: "1px solid var(--color-border)",
          borderRadius: 5, cursor: "pointer", fontSize: 12, color: "var(--color-text)",
        }}>
          <RefreshCw size={11} /> Refresh
        </button>
        <span style={{ fontSize: 11, color: "var(--color-text-dim)" }}>
          <code>python -m stratscout.engine.data.factor_backtest loop 200</code>
        </span>
      </div>

      {/* Sort controls */}
      <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 8 }}>
        <span style={{ fontSize: 11, color: "var(--color-text-dim)" }}>Sort:</span>
        {(["seen", "abs_ic", "ic_oos", "p_adj"] as const).map((k) => (
          <button key={k} onClick={() => setSortBy(k)} style={{
            padding: "1px 7px", borderRadius: 4, fontSize: 11, cursor: "pointer",
            background: sortBy === k ? "var(--color-accent)" : "var(--color-surface)",
            color: sortBy === k ? "#fff" : "var(--color-text-dim)",
            border: "1px solid var(--color-border)",
          }}>
            {k === "abs_ic" ? "Best |IC|" : k === "ic_oos" ? "IC OOS" : k === "p_adj" ? "# Sig" : "Seen"}
          </button>
        ))}
      </div>

      {survivors.length === 0 ? (
        <div style={{ padding: 32, textAlign: "center", color: "var(--color-text-dim)", fontSize: 13 }}>
          Log exists but no survivors yet. Keep the loop running.
        </div>
      ) : (
        <div style={{ border: "1px solid var(--color-border)", borderRadius: 8, overflow: "hidden", fontSize: 12 }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: "var(--color-surface)", color: "var(--color-text-dim)" }}>
                <Th>Factor combination</Th>
                <Th>Best IC</Th>
                <Th>IC OOS</Th>
                <Th>Recipes</Th>
                <Th>Seen</Th>
                <Th>Sig hits</Th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((g, i) => (
                <tr key={g.factor_key} style={{
                  background: i % 2 === 0 ? "transparent" : "var(--color-surface)",
                  borderTop: "1px solid var(--color-border)",
                  outline: g.significant_count > 0 ? "1px solid var(--color-pos)" : undefined,
                }}>
                  <td style={{ padding: "7px 10px", maxWidth: 340 }}>
                    <div style={{ fontWeight: 600, fontSize: 12 }}>
                      {g.factor_set.map((f, fi) => (
                        <span key={f}>
                          {fi > 0 && <span style={{ color: "var(--color-text-dim)", margin: "0 4px" }}>+</span>}
                          <span style={{ color: f === "fear_greed" || f.startsWith("google") ? "var(--color-accent)" : "var(--color-text)" }}>{f}</span>
                        </span>
                      ))}
                    </div>
                    <div style={{ fontSize: 10, color: "var(--color-text-dim)", marginTop: 2, fontFamily: "monospace" }}>
                      best: {g.best_name.slice(3, 60)}{g.best_name.length > 63 ? "…" : ""}
                    </div>
                  </td>
                  <td style={{ padding: "7px 10px" }}><IcBar ic={g.best_ic} /></td>
                  <Td style={{ color: getIcColor(g.best_ic_oos) }}>{FMT_SIGNED(g.best_ic_oos, 3)}</Td>
                  <Td style={{ color: "var(--color-text-dim)" }}>{g.recipe_count}</Td>
                  <Td style={{ color: g.seen_count >= 5 ? "var(--color-pos)" : g.seen_count >= 2 ? "var(--color-text)" : "var(--color-text-dim)", fontWeight: g.seen_count >= 5 ? 600 : 400 }}>
                    {g.seen_count}×
                  </Td>
                  <Td style={{ color: g.significant_count > 0 ? "var(--color-pos)" : "var(--color-text-dim)" }}>
                    {g.significant_count > 0 ? `${g.significant_count} *` : "—"}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p style={{ marginTop: 10, fontSize: 11, color: "var(--color-text-dim)" }}>
        Grouped by which base factors appear together — different operators/transforms count as the same combination.
        "Seen" = distinct recipes using this pair that survived. "Sig hits" = how many were Bonferroni significant.
        Highlighted in blue = Fear &amp; Greed or Google Trends factors.
      </p>
    </div>
  );
}
