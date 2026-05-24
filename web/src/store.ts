// Zustand store for app-wide state.
// Kept tiny — server state stays in React state hooks at the screen level.

import { create } from "zustand";
import type { BacktestResponse, StrategyRow as SavedStrategy, StrategyTemplate } from "./types";

interface AppState {
  // Active screen
  view: "onboarding" | "analyze" | "find" | "live" | "settings" | "walkforward" | "universe" | "factorlab";
  setView: (v: AppState["view"]) => void;

  // Selected template / strategy
  template: StrategyTemplate | null;
  setTemplate: (t: StrategyTemplate | null) => void;

  // Most recent backtest result (for re-rendering charts without re-fetching)
  lastResult: BacktestResponse | null;
  lastWindow: { start: string; end: string } | null;
  setLastResult: (r: BacktestResponse, w: { start: string; end: string }) => void;

  // A custom param set loaded from a fuzz leaderboard row (overrides template defaults)
  customParams: Record<string, unknown> | null;
  customParamsLabel: string | null;
  setCustomParams: (p: Record<string, unknown> | null, label?: string | null) => void;

  // Saved strategy currently bound to the Analyze view (for persistence)
  activeSavedStrategy: SavedStrategy | null;
  setActiveSavedStrategy: (s: SavedStrategy | null) => void;

  // Seed params queued for Find's refine flow (set by "Refine this" in Analyze)
  refineSeedParams: Record<string, unknown>[] | null;
  refineSeedLabel: string | null;
  setRefineSeedParams: (p: Record<string, unknown>[] | null, label?: string | null) => void;

  // Health check status (set on first connect)
  apiHealthy: boolean | null;
  setApiHealthy: (h: boolean) => void;
}

export const useApp = create<AppState>((set) => ({
  view: "onboarding",
  setView: (view) => set({ view }),

  template: null,
  setTemplate: (template) =>
    set({
      template,
      view: template ? "analyze" : "onboarding",
      // Switching template clears any leaderboard-loaded params
      customParams: null,
      customParamsLabel: null,
      lastResult: null,
    }),

  lastResult: null,
  lastWindow: null,
  setLastResult: (r, w) => set({ lastResult: r, lastWindow: w }),

  customParams: null,
  customParamsLabel: null,
  setCustomParams: (p, label = null) =>
    set({ customParams: p, customParamsLabel: label, lastResult: null }),

  activeSavedStrategy: null,
  setActiveSavedStrategy: (s) => set({ activeSavedStrategy: s }),

  refineSeedParams: null,
  refineSeedLabel: null,
  setRefineSeedParams: (p, label = null) => set({ refineSeedParams: p, refineSeedLabel: label }),

  apiHealthy: null,
  setApiHealthy: (h) => set({ apiHealthy: h }),
}));
