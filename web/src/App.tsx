import { useEffect, useState } from "react";
import {
  BarChart2,
  Search,
  TrendingUp,
  GitBranch,
  Radio,
  Settings as SettingsIcon,
  HelpCircle,
  ChevronLeft,
  ChevronRight,
  FlaskConical,
} from "lucide-react";
import { api } from "./api";
import { useApp } from "./store";
import { Onboarding } from "./screens/Onboarding";
import { Analyze } from "./screens/Analyze";
import { Find } from "./screens/Find";
import { WalkForward } from "./screens/WalkForward";
import { Live } from "./screens/Live";
import { Settings } from "./screens/Settings";
import { Universe } from "./screens/Universe";
import { FactorLab } from "./screens/FactorLab";
import { HELP_TOPICS } from "./helpContent";

type AppView = "onboarding" | "analyze" | "find" | "walkforward" | "live" | "settings" | "universe" | "factorlab";

const NAV_ITEMS: Array<{
  id: AppView;
  label: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
  requiresTemplate?: boolean;
  etfOnly?: boolean;
}> = [
  { id: "universe", label: "Universe",      icon: Search },
  { id: "analyze",  label: "Analyze",       icon: BarChart2,   requiresTemplate: true },
  { id: "find",     label: "Find",          icon: TrendingUp,  requiresTemplate: true },
  { id: "walkforward", label: "Validate",   icon: GitBranch,   requiresTemplate: true, etfOnly: true },
  { id: "live",     label: "Live",          icon: Radio },
  { id: "factorlab", label: "Factor Lab",   icon: FlaskConical, etfOnly: true },
];

function App() {
  const view = useApp((s) => s.view);
  const template = useApp((s) => s.template);
  const apiHealthy = useApp((s) => s.apiHealthy);
  const setApiHealthy = useApp((s) => s.setApiHealthy);
  const [helpOpen, setHelpOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const check = () =>
      api.health()
        .then(() => !cancelled && setApiHealthy(true))
        .catch(() => !cancelled && setApiHealthy(false));
    check();
    const t = setInterval(check, 30_000);
    return () => { cancelled = true; clearInterval(t); };
  }, [setApiHealthy]);

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: "var(--color-bg)" }}>
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed((v) => !v)}
        healthy={apiHealthy}
        hasTemplate={!!template}
        isSmallcap={template?.kind === "smallcap"}
        onHelp={() => setHelpOpen(true)}
      />
      <main className="flex-1 overflow-y-auto min-w-0">
        {view === "onboarding"   && <Onboarding />}
        {view === "universe"     && <Universe />}
        {view === "analyze"      && <Analyze />}
        {view === "find"         && <Find />}
        {view === "walkforward"  && <WalkForward />}
        {view === "live"         && <Live />}
        {view === "factorlab"    && <FactorLab />}
        {view === "settings"     && <Settings />}
      </main>
      {helpOpen && <HelpDrawer viewKey={view} onClose={() => setHelpOpen(false)} />}
    </div>
  );
}

function Sidebar({
  collapsed,
  onToggle,
  healthy,
  hasTemplate,
  isSmallcap,
  onHelp,
}: {
  collapsed: boolean;
  onToggle: () => void;
  healthy: boolean | null;
  hasTemplate: boolean;
  isSmallcap: boolean;
  onHelp: () => void;
}) {
  const view = useApp((s) => s.view);
  const setView = useApp((s) => s.setView);
  const setTemplate = useApp((s) => s.setTemplate);

  const dotColor =
    healthy === true ? "var(--color-pos)"
    : healthy === false ? "var(--color-neg)"
    : "var(--color-text-dim)";

  const statusLabel =
    healthy === true ? "Connected" : healthy === false ? "API offline" : "Connecting…";

  return (
    <aside
      className="flex flex-col shrink-0 border-r transition-all duration-200"
      style={{
        width: collapsed ? 56 : 200,
        background: "var(--color-surface)",
        borderColor: "var(--color-border)",
      }}
    >
      {/* Logo row */}
      <div
        className="flex items-center justify-between px-3 py-3 border-b"
        style={{ borderColor: "var(--color-border)", minHeight: 52 }}
      >
        {!collapsed && (
          <button
            onClick={() => setTemplate(null)}
            className="flex items-center gap-2 group min-w-0"
          >
            <div
              className="w-2 h-2 rounded-full shrink-0"
              style={{ background: "var(--color-accent)" }}
            />
            <span
              className="font-semibold tracking-wide text-sm truncate group-hover:opacity-80 transition-opacity"
            >
              StratScout
            </span>
          </button>
        )}
        <button
          onClick={onToggle}
          className="p-1 rounded transition-colors hover:bg-white/5 text-[var(--color-text-dim)] ml-auto"
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
        </button>
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-2 space-y-0.5 px-1.5">
        {NAV_ITEMS.map((item) => {
          const disabled =
            (item.requiresTemplate && !hasTemplate) ||
            (item.etfOnly && isSmallcap);
          const active = view === item.id;
          const Icon = item.icon;

          return (
            <button
              key={item.id}
              onClick={() => !disabled && setView(item.id)}
              disabled={disabled}
              title={
                collapsed
                  ? item.label
                  : item.etfOnly && isSmallcap
                  ? "Walk-forward is ETF-only"
                  : undefined
              }
              className={`
                w-full flex items-center gap-3 px-2.5 py-2 rounded-md text-sm transition-colors
                ${active
                  ? "text-[var(--color-text)] bg-[var(--color-surface-2)]"
                  : disabled
                  ? "text-[var(--color-text-dim)] opacity-40 cursor-not-allowed"
                  : "text-[var(--color-text-dim)] hover:text-[var(--color-text)] hover:bg-white/5"
                }
                ${collapsed ? "justify-center" : ""}
              `}
            >
              <Icon size={16} className="shrink-0" />
              {!collapsed && <span className="truncate">{item.label}</span>}
            </button>
          );
        })}
      </nav>

      {/* Bottom: help + settings + status */}
      <div className="py-2 px-1.5 border-t space-y-0.5" style={{ borderColor: "var(--color-border)" }}>
        <button
          onClick={onHelp}
          title={collapsed ? "Help" : undefined}
          className={`
            w-full flex items-center gap-3 px-2.5 py-2 rounded-md text-sm transition-colors
            text-[var(--color-text-dim)] hover:text-[var(--color-text)] hover:bg-white/5
            ${collapsed ? "justify-center" : ""}
          `}
        >
          <HelpCircle size={16} className="shrink-0" />
          {!collapsed && <span>Help</span>}
        </button>
        <button
          onClick={() => setView("settings")}
          title={collapsed ? "Settings" : undefined}
          className={`
            w-full flex items-center gap-3 px-2.5 py-2 rounded-md text-sm transition-colors
            ${view === "settings"
              ? "text-[var(--color-text)] bg-[var(--color-surface-2)]"
              : "text-[var(--color-text-dim)] hover:text-[var(--color-text)] hover:bg-white/5"
            }
            ${collapsed ? "justify-center" : ""}
          `}
        >
          <SettingsIcon size={16} className="shrink-0" />
          {!collapsed && <span>Settings</span>}
        </button>

        {/* API status */}
        <div
          className={`flex items-center gap-2 px-2.5 py-2 ${collapsed ? "justify-center" : ""}`}
          title={statusLabel}
        >
          <div
            className="w-1.5 h-1.5 rounded-full shrink-0"
            style={{ background: dotColor }}
          />
          {!collapsed && (
            <span className="text-xs truncate" style={{ color: "var(--color-text-dim)" }}>
              {statusLabel}
            </span>
          )}
        </div>
      </div>
    </aside>
  );
}

function HelpDrawer({ viewKey, onClose }: { viewKey: AppView; onClose: () => void }) {
  const topic = HELP_TOPICS[viewKey];
  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-black/40" />
      <aside
        className="relative w-full max-w-md p-6 overflow-y-auto border-l"
        style={{
          background: "var(--color-surface)",
          borderColor: "var(--color-border)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">{topic?.title ?? "Help"}</h2>
          <button
            onClick={onClose}
            className="text-xl leading-none hover:opacity-70 transition-opacity"
            style={{ color: "var(--color-text-dim)" }}
            aria-label="Close help"
          >
            ×
          </button>
        </header>
        {topic ? (
          <>
            <p className="text-sm leading-relaxed mb-5" style={{ color: "var(--color-text-dim)" }}>
              {topic.intro}
            </p>
            <div className="space-y-4">
              {topic.sections.map((s) => (
                <section key={s.heading}>
                  <h3
                    className="text-xs uppercase tracking-wider mb-1"
                    style={{ color: "var(--color-text-dim)" }}
                  >
                    {s.heading}
                  </h3>
                  <p className="text-sm leading-relaxed">{s.body}</p>
                </section>
              ))}
            </div>
          </>
        ) : (
          <p className="text-sm" style={{ color: "var(--color-text-dim)" }}>
            No help registered for this view yet.
          </p>
        )}
      </aside>
    </div>
  );
}

export default App;
