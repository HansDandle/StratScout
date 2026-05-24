// Typed HTTP client for the StratScout FastAPI service.
// In dev, Vite proxies /api → http://127.0.0.1:8765.
// In Tauri, the sidecar runs on 127.0.0.1:8765 directly.

import type {
  BacktestRequest,
  BacktestResponse,
  FactorsResponse,
  FactorDownloadRequest,
  FactorDownloadResponse,
  BaselineRequest,
  BaselineResponse,
  CategoriesResponse,
  CreateStrategyRequest,
  DownloadRequest,
  DownloadResponse,
  DryRunResponse,
  RunResponse,
  ScheduleStatus,
  FuzzRunDetailResponse,
  FuzzRunListResponse,
  FuzzRunMetaRow,
  FuzzRunRequest,
  FuzzRunResponse,
  HealthResponse,
  InventoryResponse,
  LeaderboardResponse,
  PreflightResponse,
  ProvidersResponse,
  ProviderStatus,
  PutCredentialRequest,
  StrategyListResponse,
  StrategyRow,
  SuggestFuzzWindowRequest,
  SuggestFuzzWindowResponse,
  TestCredentialResponse,
  TradeOrdersResponse,
  UpdateStrategyRequest,
  WalkForwardResponse,
  WalkForwardRunRequest,
} from "./types";

// In dev, Vite proxies /api → :8765. In production (web app), set VITE_API_URL.
const BASE = (import.meta.env.VITE_API_URL as string | undefined)
  ? `${import.meta.env.VITE_API_URL as string}/api`
  : "/api";

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function call<T>(
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    let detail = "";
    try {
      const j = await r.json();
      detail = j.detail ?? j.message ?? r.statusText;
    } catch {
      detail = r.statusText;
    }
    throw new ApiError(r.status, detail);
  }
  return (await r.json()) as T;
}

export const api = {
  health: () => call<HealthResponse>("GET", "/health"),
  backtest: (req: BacktestRequest) => call<BacktestResponse>("POST", "/backtest", req),
  baselines: (req: BaselineRequest) => call<BaselineResponse>("POST", "/baselines", req),
  inventory: () => call<InventoryResponse>("GET", "/data/inventory"),
  categories: () => call<CategoriesResponse>("GET", "/data/categories"),
  suggestFuzzWindow: (req: SuggestFuzzWindowRequest) =>
    call<SuggestFuzzWindowResponse>("POST", "/data/suggest-fuzz-window", req),
  fuzz: (req: FuzzRunRequest) => call<FuzzRunResponse>("POST", "/fuzz", req),
  download: (req: DownloadRequest) => call<DownloadResponse>("POST", "/data/download", req),
  credentials: (test = false) =>
    call<ProvidersResponse>("GET", test ? "/settings/credentials?test=true" : "/settings/credentials"),
  putCredential: (req: PutCredentialRequest) =>
    call<ProviderStatus>("PUT", "/settings/credentials", req),
  deleteCredential: (provider_id: string, field_name: string) =>
    call<{ ok: boolean }>("DELETE", `/settings/credentials/${provider_id}/${field_name}`),
  testCredential: (provider_id: string) =>
    call<TestCredentialResponse>("POST", `/settings/credentials/${provider_id}/test`),

  // Strategies
  listStrategies: (includeArchived = false) =>
    call<StrategyListResponse>(
      "GET",
      includeArchived ? "/strategies?include_archived=true" : "/strategies",
    ),
  createStrategy: (req: CreateStrategyRequest) =>
    call<StrategyRow>("POST", "/strategies", req),
  getStrategy: (id: number) => call<StrategyRow>("GET", `/strategies/${id}`),
  updateStrategy: (id: number, req: UpdateStrategyRequest) =>
    call<StrategyRow>("PATCH", `/strategies/${id}`, req),
  deleteStrategy: (id: number) =>
    call<{ ok: boolean }>("DELETE", `/strategies/${id}`),

  // Walk-forward
  walkForward: (req: WalkForwardRunRequest) =>
    call<WalkForwardResponse>("POST", "/walk-forward", req),
  latestWalkForward: (id: number) =>
    call<WalkForwardResponse>("GET", `/strategies/${id}/walk-forward/latest`),
  /** Streaming walk-forward. Calls onProgress for each completed month,
   *  then resolves with the full WalkForwardResponse when done. */
  walkForwardStream: (
    req: WalkForwardRunRequest,
    onProgress: (completed: number, total: number, month: string, elapsedSec: number, remainingSec: number) => void,
    onSteering?: (excludeAdded: string[], reason: string) => void,
  ): Promise<WalkForwardResponse> => {
    return new Promise(async (resolve, reject) => {
      try {
        const r = await fetch(`${BASE}/walk-forward/stream`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(req),
        });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          reject(new ApiError(r.status, j.detail ?? r.statusText));
          return;
        }
        const reader = r.body!.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const parts = buf.split("\n\n");
          buf = parts.pop()!;
          for (const part of parts) {
            const line = part.trim();
            if (!line.startsWith("data: ")) continue;
            const ev = JSON.parse(line.slice(6)) as Record<string, unknown>;
            if (ev.type === "progress") {
              onProgress(
                ev.completed as number, ev.total as number,
                ev.month as string, ev.elapsed_sec as number, ev.remaining_sec as number,
              );
            } else if (ev.type === "steering") {
              onSteering?.(ev.exclude_added as string[], ev.reason as string);
            } else if (ev.type === "done") {
              resolve(ev.result as WalkForwardResponse);
            } else if (ev.type === "error") {
              reject(new ApiError(500, ev.message as string));
            }
          }
        }
      } catch (e) {
        reject(e);
      }
    });
  },

  // Preflight
  preflight: (id: number) =>
    call<PreflightResponse>("GET", `/strategies/${id}/preflight`),

  // Trade orders + execution
  listOrders: (id: number, limit = 100) =>
    call<TradeOrdersResponse>("GET", `/strategies/${id}/orders?limit=${limit}`),
  /** Execute the strategy. mode: 'dry' | 'paper' | 'live'. */
  runStrategy: (id: number, mode: "dry" | "paper" | "live" = "dry", note = "") =>
    call<RunResponse>(
      "POST",
      `/strategies/${id}/run-now?mode=${mode}&note=${encodeURIComponent(note)}`,
    ),
  /** @deprecated kept for backward compat — use runStrategy() */
  runStrategyNow: (id: number, note = "") =>
    call<DryRunResponse>("POST", `/strategies/${id}/run-now?mode=dry&note=${encodeURIComponent(note)}`),

  // Daily schedule (Windows Task Scheduler)
  scheduleStatus: () => call<ScheduleStatus>("GET", "/schedule"),
  scheduleInstall: (run_time = "09:35") =>
    call<ScheduleStatus>("POST", "/schedule", { run_time }),
  scheduleRemove: () => call<ScheduleStatus>("DELETE", "/schedule"),

  // Fuzz history
  fuzzRuns: (limit = 30) =>
    call<FuzzRunListResponse>("GET", `/fuzz/runs?limit=${limit}`),
  fuzzRunDetail: (id: number, limit = 100) =>
    call<FuzzRunDetailResponse>("GET", `/fuzz/runs/${id}?limit=${limit}`),
  deleteFuzzRun: (id: number) =>
    call<{ ok: boolean }>("DELETE", `/fuzz/runs/${id}`),
  relabelFuzzRun: (id: number, label: string) =>
    call<FuzzRunMetaRow>("PATCH", `/fuzz/runs/${id}`, { label }),
  fuzzLeaderboard: (limit = 50, kind = "etf") =>
    call<LeaderboardResponse>(
      "GET",
      `/fuzz/leaderboard?limit=${limit}&strategy_kind=${kind}`,
    ),

  // Factor Lab
  factorsList: () => call<FactorsResponse>("GET", "/factors"),
  factorsDownload: (req: FactorDownloadRequest) =>
    call<FactorDownloadResponse>("POST", "/factors/download", req),
};

export { ApiError };
