// Settings screen — broker / data-provider API keys + data overview.
//
// Two sections:
//   1. Data inventory by category (daily / intraday / smallcap / options)
//      with a "Download missing core ETFs" CTA
//   2. Connections — one card per provider (Alpaca, Schwab, Polygon, ThetaData)
//      showing key fields + test/save/clear buttons
//
// Secrets are never returned by the server — fields show "set" or "missing"
// only. Users type a new value to overwrite.

import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { Button, Card, Spinner } from "../components/ui";
import type { CategoryRow, DownloadResponse, ProviderStatus } from "../types";

const CORE_ETFS = [
  "SPY", "QQQ", "TQQQ", "UPRO", "SOXL", "TECL",
  "AGG", "BIL", "TLT", "GLD", "SLV", "XLP",
];

export function Settings() {
  const [providers, setProviders] = useState<ProviderStatus[] | null>(null);
  const [categories, setCategories] = useState<CategoryRow[] | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [creds, cats] = await Promise.all([api.credentials(), api.categories()]);
        if (!cancelled) {
          setProviders(creds.providers);
          setCategories(cats.categories);
        }
      } catch (e) {
        console.error(e);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  const refresh = () => setRefreshKey((k) => k + 1);

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Settings</h1>
        <p className="text-sm text-(--color-text-dim) max-w-2xl mt-1">
          API keys are stored in your operating-system keychain (Windows Credential Locker
          on this machine). Existing keys from <code className="text-(--color-text)">.env</code>{" "}
          are auto-imported.
        </p>
      </header>

      <DataPanel categories={categories} onRefresh={refresh} />
      <ConnectionsPanel providers={providers} onUpdate={refresh} />
    </div>
  );
}

// ── Data panel ─────────────────────────────────────────────────────────────

function DataPanel({
  categories,
  onRefresh,
}: {
  categories: CategoryRow[] | null;
  onRefresh: () => void;
}) {
  const [downloading, setDownloading] = useState(false);
  const [download, setDownload] = useState<DownloadResponse | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  async function downloadCoreETFs() {
    setDownloading(true);
    setDownload(null);
    setDownloadError(null);
    try {
      const r = await api.download({ symbols: CORE_ETFS, start: "2018-01-01" });
      setDownload(r);
      onRefresh();
    } catch (e) {
      setDownloadError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setDownloading(false);
    }
  }

  return (
    <Card className="p-5">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
        <div>
          <h2 className="text-lg font-semibold">Your data</h2>
          <p className="text-xs text-(--color-text-dim) mt-0.5">
            Broken out by what kind of data it is and which strategies use it.
          </p>
        </div>
        <Button onClick={downloadCoreETFs} disabled={downloading} size="sm">
          {downloading ? (
            <>
              <Spinner /> <span className="ml-2">Downloading…</span>
            </>
          ) : (
            "Download core ETFs"
          )}
        </Button>
      </div>

      {!categories ? (
        <div className="text-(--color-text-dim) text-sm flex items-center gap-2">
          <Spinner /> Scanning…
        </div>
      ) : (
        <div className="space-y-2">
          {categories.map((cat) => (
            <CategoryRowView key={cat.kind} cat={cat} />
          ))}
        </div>
      )}

      {downloadError && (
        <p className="text-(--color-neg) text-sm mt-4">Error: {downloadError}</p>
      )}

      {download && (
        <DownloadReport report={download} />
      )}
    </Card>
  );
}

function CategoryRowView({ cat }: { cat: CategoryRow }) {
  const has = cat.n_files > 0;
  return (
    <div
      className={`px-4 py-3 rounded border ${
        has ? "border-(--color-border) bg-(--color-surface-2)/40" : "border-(--color-border)/40"
      }`}
    >
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3">
          <div
            className={`w-2 h-2 rounded-full ${
              has ? "bg-(--color-pos)" : "bg-(--color-text-dim)"
            }`}
          />
          <span className="font-medium">{cat.label}</span>
        </div>
        <div className="flex items-center gap-4 text-xs text-(--color-text-dim) tabular">
          <span>{cat.n_files} files</span>
          <span>{cat.total_size_mb.toFixed(1)} MB</span>
          {cat.earliest && cat.latest && (
            <span>
              {cat.earliest} → {cat.latest}
            </span>
          )}
        </div>
      </div>
      <p className="text-xs text-(--color-text-dim) mt-2">{cat.note}</p>
    </div>
  );
}

function DownloadReport({ report }: { report: DownloadResponse }) {
  const sources = Object.values(report.source_used).reduce<Record<string, number>>(
    (acc, s) => {
      acc[s] = (acc[s] ?? 0) + 1;
      return acc;
    },
    {},
  );
  return (
    <div className="mt-4 p-3 rounded border border-(--color-border) bg-(--color-surface-2)/40 text-sm">
      <div className="font-medium">
        Downloaded {report.done} / {report.total}{" "}
        {report.failed.length > 0 && (
          <span className="text-(--color-warn)">({report.failed.length} failed)</span>
        )}
      </div>
      <div className="text-xs text-(--color-text-dim) mt-1">
        Sources used:{" "}
        {Object.entries(sources)
          .map(([s, n]) => `${s} × ${n}`)
          .join(", ") || "none"}
      </div>
      {report.failed.length > 0 && (
        <ul className="mt-2 text-xs text-(--color-warn) space-y-0.5">
          {report.failed.slice(0, 8).map(([sym, reason]) => (
            <li key={sym}>
              {sym}: {reason}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ── Connections / providers ───────────────────────────────────────────────

function ConnectionsPanel({
  providers,
  onUpdate,
}: {
  providers: ProviderStatus[] | null;
  onUpdate: () => void;
}) {
  return (
    <Card className="p-5">
      <h2 className="text-lg font-semibold mb-1">Connections</h2>
      <p className="text-xs text-(--color-text-dim) mb-4">
        Brokers and data providers. We never display secret values — only whether
        they're set. Type a new value to replace.
      </p>

      {!providers ? (
        <div className="text-(--color-text-dim) text-sm flex items-center gap-2">
          <Spinner /> Loading…
        </div>
      ) : (
        <div className="space-y-3">
          {providers.map((p) => (
            <ProviderCard key={p.id} provider={p} onUpdate={onUpdate} />
          ))}
        </div>
      )}
    </Card>
  );
}

function ProviderCard({
  provider,
  onUpdate,
}: {
  provider: ProviderStatus;
  onUpdate: () => void;
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);

  async function saveAll() {
    setSaving(true);
    setTestResult(null);
    try {
      for (const [field_name, value] of Object.entries(drafts)) {
        if (value.trim()) {
          await api.putCredential({
            provider_id: provider.id,
            field_name,
            value: value.trim(),
          });
        }
      }
      setDrafts({});
      onUpdate();
    } catch (e) {
      setTestResult({
        ok: false,
        message: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e),
      });
    } finally {
      setSaving(false);
    }
  }

  async function testConnection() {
    setTesting(true);
    try {
      const r = await api.testCredential(provider.id);
      setTestResult({ ok: r.ok, message: r.message });
    } catch (e) {
      setTestResult({
        ok: false,
        message: e instanceof ApiError ? `${e.status}: ${e.message}` : String(e),
      });
    } finally {
      setTesting(false);
    }
  }

  async function clearField(field_name: string) {
    await api.deleteCredential(provider.id, field_name);
    onUpdate();
  }

  return (
    <div className="rounded border border-(--color-border) p-4 bg-(--color-surface-2)/30">
      <div className="flex items-start justify-between gap-3 mb-2 flex-wrap">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="font-semibold">{provider.name}</h3>
            <StatusBadge ok={provider.all_present} />
          </div>
          <p className="text-xs text-(--color-text-dim) mt-1 max-w-xl">
            {provider.description}
          </p>
          {provider.signup_url && (
            <a
              href={provider.signup_url}
              target="_blank"
              rel="noreferrer noopener"
              className="text-xs text-(--color-accent) hover:underline"
            >
              Get keys →
            </a>
          )}
        </div>
      </div>

      {provider.required_keys.length === 0 && (
        <p className="text-xs text-(--color-text-dim) mb-3 italic">
          No keys required (uses a local process).
        </p>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
        {provider.required_keys.map((f) => {
          const present = provider.keys_present[f];
          return (
            <div key={f}>
              <label className="block text-xs text-(--color-text-dim) mb-1 capitalize">
                {f.replace(/_/g, " ")}
                {present && (
                  <span className="text-(--color-pos) ml-2 normal-case">• set</span>
                )}
              </label>
              <div className="flex items-center gap-2">
                <input
                  type={f.includes("secret") || f.includes("token") || f.includes("key") ? "password" : "text"}
                  placeholder={present ? "•••••••• (replace)" : "Enter value"}
                  value={drafts[f] ?? ""}
                  onChange={(e) =>
                    setDrafts((d) => ({ ...d, [f]: e.target.value }))
                  }
                  className="flex-1 bg-(--color-surface) border border-(--color-border) rounded px-3 py-1.5 text-sm tabular"
                />
                {present && (
                  <button
                    onClick={() => clearField(f)}
                    className="text-xs text-(--color-text-dim) hover:text-(--color-neg) px-2"
                    title="Clear this field"
                  >
                    Clear
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <div className="flex items-center gap-2 mt-4 flex-wrap">
        <Button
          size="sm"
          onClick={saveAll}
          disabled={saving || Object.values(drafts).every((v) => !v.trim())}
        >
          {saving ? <Spinner /> : "Save"}
        </Button>
        {provider.required_keys.length > 0 && (
          <Button size="sm" variant="ghost" onClick={testConnection} disabled={testing || !provider.all_present}>
            {testing ? <Spinner /> : "Test connection"}
          </Button>
        )}
        {testResult && (
          <span
            className={`text-xs ml-2 ${
              testResult.ok ? "text-(--color-pos)" : "text-(--color-neg)"
            }`}
          >
            {testResult.ok ? "✓ " : "✗ "}
            {testResult.message}
          </span>
        )}
      </div>
    </div>
  );
}

function StatusBadge({ ok }: { ok: boolean }) {
  if (ok) {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-(--color-pos)/15 text-(--color-pos) border border-(--color-pos)/40">
        Connected
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-(--color-text-dim)/20 text-(--color-text-dim) border border-(--color-text-dim)/40">
      Missing keys
    </span>
  );
}
