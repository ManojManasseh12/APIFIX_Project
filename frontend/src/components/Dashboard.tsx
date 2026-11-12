import { useCallback, useEffect, useState } from "react";
import type { RunSummary } from "../types";

type DashboardStats = {
  total_runs: number;
  failure_count: number;
  daily_runs: { date: string; count: number }[];
  status_counts: { status: string; count: number }[];
  provider_usage: { provider: string; count: number }[];
  endpoints: {
    method: string;
    path: string;
    latest_status: number | null;
    runs: number;
  }[];
};

function methodStyle(method: string) {
  const styles: Record<string, string> = {
    GET: "bg-emerald-100 text-emerald-700",
    POST: "bg-blue-100 text-blue-700",
    PUT: "bg-amber-100 text-amber-700",
    PATCH: "bg-purple-100 text-purple-700",
    DELETE: "bg-red-100 text-red-700",
  };
  return styles[method] ?? "bg-slate-100 text-slate-600";
}

function relativeTime(value: string) {
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return value;
  const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60_000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function statusColor(status: number | null) {
  if (status === null) return "text-slate-400";
  if (status >= 500) return "text-rose-600";
  if (status >= 400) return "text-amber-600";
  return "text-emerald-600";
}

function providerColor(provider: string) {
  const family = provider.split(":", 1)[0].toLowerCase();
  const colors: Record<string, string> = {
    groq: "bg-orange-50 text-orange-700",
    gemini: "bg-indigo-50 text-indigo-700",
    mistral: "bg-sky-50 text-sky-700",
    nvidia: "bg-emerald-50 text-emerald-700",
  };
  return colors[family] ?? "bg-slate-100 text-slate-700";
}

// ── Shimmer skeleton primitives ───────────────────────────────────────────────

function Shimmer({
  className,
  style,
}: {
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <div
      className={`animate-pulse rounded bg-slate-200 ${className ?? ""}`}
      style={{
        backgroundImage:
          "linear-gradient(90deg,#e2e8f0 25%,#f1f5f9 50%,#e2e8f0 75%)",
        backgroundSize: "200% 100%",
        animation: "shimmer 1.4s ease-in-out infinite",
        ...style,
      }}
    />
  );
}

function KpiSkeleton() {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <Shimmer className="mb-3 h-1 w-12" />
      <Shimmer className="mt-2 h-3 w-28" />
      <Shimmer className="mt-2 h-8 w-16" />
      <Shimmer className="mt-2 h-3 w-40" />
    </div>
  );
}

function BarRowSkeleton() {
  return (
    <div className="space-y-3">
      {[70, 50, 35, 20].map((w) => (
        <div key={w}>
          <div className="mb-1.5 flex justify-between">
            <Shimmer className="h-3 w-28" />
            <Shimmer className="h-3 w-10" />
          </div>
          <Shimmer className="h-2 w-full" style={{ width: `${w}%` } as React.CSSProperties} />
        </div>
      ))}
    </div>
  );
}

function ChartSkeleton() {
  return (
    <div className="flex h-48 items-end gap-3">
      {[55, 70, 45, 80, 95, 60, 75].map((h, i) => (
        <div key={i} className="flex h-full flex-1 flex-col items-center justify-end gap-2">
          <Shimmer className="h-3 w-6" />
          <div className="flex h-full w-full items-end">
            <div className="w-full animate-pulse rounded-t-md bg-slate-200" style={{ height: `${h}%` }} />
          </div>
          <Shimmer className="h-2.5 w-6" />
        </div>
      ))}
    </div>
  );
}

function ListRowSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="divide-y divide-slate-100">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 py-3 first:pt-0 last:pb-0">
          <Shimmer className="h-5 w-10 rounded" />
          <Shimmer className="h-3 flex-1" />
          <Shimmer className="h-3 w-8" />
          <Shimmer className="h-4 w-16 rounded-full" />
          <Shimmer className="h-3 w-12" />
        </div>
      ))}
    </div>
  );
}

// ── KPI card ──────────────────────────────────────────────────────────────────

function Kpi({
  label,
  value,
  description,
  accent,
}: {
  label: string;
  value: string;
  description: string;
  accent: string;
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className={`mb-3 h-1 w-12 rounded-full ${accent}`} />
      <p className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
        {label}
      </p>
      <p className="mt-2 text-3xl font-black tracking-tight text-slate-900">
        {value}
      </p>
      <p className="mt-1 text-xs text-slate-500">{description}</p>
    </div>
  );
}

export function Dashboard({
  onNavigateToDiagnose,
  recentRuns = [],
  bundledChunks = null,
}: {
  onNavigateToDiagnose: () => void;
  recentRuns?: RunSummary[];
  bundledChunks?: number | null;
}) {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refreshStats = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch("/api/runs/analytics");
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(data?.detail ?? "Could not load dashboard analytics");
      }
      setStats(data);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshStats();
  }, [refreshStats]);

  const maxDailyRuns = Math.max(1, ...(stats?.daily_runs.map((day) => day.count) ?? []));
  const maxProviderRuns = Math.max(1, ...(stats?.provider_usage.map((item) => item.count) ?? []));
  const runs = recentRuns.slice(0, 5);

  return (
    <div className="mx-auto max-w-[1550px] space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-black tracking-tight text-slate-900">
            Overview
          </h1>
          <p className="mt-0.5 text-sm text-slate-500">
            Live analytics from persisted diagnoses and the knowledge base.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => void refreshStats()}
            disabled={loading}
            className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            {loading ? "Refreshing…" : "Refresh"}
          </button>
          <button
            onClick={onNavigateToDiagnose}
            className="rounded-xl bg-teal-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-teal-700"
          >
            Diagnose a request
          </button>
        </div>
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800"
        >
          Dashboard data could not be loaded: {error}
        </div>
      )}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {loading && !stats ? (
          <>
            <KpiSkeleton />
            <KpiSkeleton />
            <KpiSkeleton />
            <KpiSkeleton />
          </>
        ) : (
          <>
            <Kpi
              label="Completed diagnoses"
              value={stats ? String(stats.total_runs) : "—"}
              description="Saved in run history"
              accent="bg-teal-500"
            />
            <Kpi
              label="HTTP failures observed"
              value={stats ? String(stats.failure_count) : "—"}
              description="Saved observations with status 400+"
              accent="bg-amber-500"
            />
            <Kpi
              label="Knowledge chunks"
              value={bundledChunks === null ? "—" : String(bundledChunks)}
              description="Currently indexed in the knowledge base"
              accent="bg-violet-500"
            />
            <Kpi
              label="Models used"
              value={stats ? String(stats.provider_usage.length) : "—"}
              description="Models with saved diagnoses"
              accent="bg-sky-500"
            />
          </>
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm lg:col-span-3">
          <h2 className="mb-5 text-sm font-bold text-slate-800">
            Diagnoses per day
          </h2>
          {stats?.daily_runs.length ? (
            <div className="flex h-48 items-end gap-3">
              {stats.daily_runs.map((day) => {
                const label = new Date(`${day.date}T00:00:00`).toLocaleDateString(
                  undefined,
                  { weekday: "short" },
                );
                const height = `${Math.max(4, (day.count / maxDailyRuns) * 100)}%`;
                return (
                  <div
                    key={day.date}
                    className="flex h-full flex-1 flex-col items-center justify-end gap-2"
                    title={`${day.date}: ${day.count} diagnoses`}
                  >
                    <span className="text-xs font-semibold text-slate-600">
                      {day.count}
                    </span>
                    <div className="flex h-full w-full items-end">
                      <div
                        className="w-full rounded-t-md bg-teal-400 transition-all"
                        style={{ height }}
                      />
                    </div>
                    <span className="text-[11px] text-slate-400">{label}</span>
                  </div>
                );
              })}
            </div>
          ) : loading && !stats ? (
            <ChartSkeleton />
          ) : (
            <p className="py-12 text-center text-sm text-slate-500">
              No diagnosis runs recorded yet.
            </p>
          )}
        </section>

        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm lg:col-span-2">
          <h2 className="mb-4 text-sm font-bold text-slate-800">
            Saved response statuses
          </h2>
          {stats?.status_counts.length ? (
            <div className="space-y-3">
              {stats.status_counts.map(({ status, count }) => {
                const percent = stats.total_runs
                  ? (count / stats.total_runs) * 100
                  : 0;
                const color =
                  status === "2xx"
                    ? "bg-emerald-400"
                    : status === "4xx"
                      ? "bg-amber-400"
                      : status === "5xx"
                        ? "bg-rose-400"
                        : "bg-slate-300";
                return (
                  <div key={status}>
                    <div className="mb-1 flex justify-between text-xs">
                      <span className="font-medium text-slate-700">{status}</span>
                      <span className="font-semibold text-slate-600">
                        {count} ({percent.toFixed(0)}%)
                      </span>
                    </div>
                    <div className="h-2 overflow-hidden rounded-full bg-slate-100">
                      <div
                        className={`h-full rounded-full ${color}`}
                        style={{ width: `${percent}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          ) : loading && !stats ? (
            <BarRowSkeleton />
          ) : (
            <p className="py-8 text-center text-sm text-slate-500">
              No saved response statuses yet.
            </p>
          )}
        </section>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-4 text-sm font-bold text-slate-800">
            Model usage in saved diagnoses
          </h2>
          {stats?.provider_usage.length ? (
            <div className="space-y-3">
              {stats.provider_usage.map(({ provider, count }) => (
                <div key={provider}>
                  <div className="mb-1 flex items-center justify-between gap-2">
                    <span
                      className={`truncate rounded-full px-2 py-0.5 text-[11px] font-semibold ${providerColor(provider)}`}
                    >
                      {provider}
                    </span>
                    <span className="text-xs font-semibold text-slate-700">
                      {count}
                    </span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
                    <div
                      className="h-full rounded-full bg-indigo-400"
                      style={{
                        width: `${(count / maxProviderRuns) * 100}%`,
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          ) : loading && !stats ? (
            <BarRowSkeleton />
          ) : (
            <p className="py-8 text-center text-sm text-slate-500">
              No model usage recorded yet.
            </p>
          )}
        </section>

        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-4 text-sm font-bold text-slate-800">
            Most-tested endpoints
          </h2>
          {stats?.endpoints.length ? (
            <div className="space-y-3">
              {stats.endpoints.map((endpoint) => (
                <div
                  key={`${endpoint.method}:${endpoint.path}`}
                  className="flex items-center gap-2"
                >
                  <span
                    className={`rounded px-1.5 py-0.5 font-mono text-[10px] font-bold ${methodStyle(endpoint.method)}`}
                  >
                    {endpoint.method}
                  </span>
                  <span className="min-w-0 flex-1 truncate font-mono text-xs text-slate-600">
                    {endpoint.path}
                  </span>
                  <span
                    className={`font-mono text-xs font-bold ${statusColor(endpoint.latest_status)}`}
                  >
                    {endpoint.latest_status ?? "—"}
                  </span>
                  <span className="text-[11px] text-slate-400">
                    {endpoint.runs} runs
                  </span>
                </div>
              ))}
            </div>
          ) : loading && !stats ? (
            <ListRowSkeleton rows={4} />
          ) : (
            <p className="py-8 text-center text-sm text-slate-500">
              No endpoints in saved diagnosis history yet.
            </p>
          )}
        </section>
      </div>

      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="mb-4 text-sm font-bold text-slate-800">
          Recent diagnoses
        </h2>
        {runs.length ? (
          <div className="divide-y divide-slate-100">
            {runs.map((run) => {
              let path = run.url;
              try {
                path = new URL(run.url).pathname;
              } catch {
                // Keep the stored URL visible if it is not an absolute URL.
              }
              return (
                <div
                  key={run.run_id}
                  className="flex items-center gap-3 py-3 first:pt-0 last:pb-0"
                >
                  <span
                    className={`rounded px-1.5 py-0.5 font-mono text-[10px] font-bold ${methodStyle(run.method)}`}
                  >
                    {run.method}
                  </span>
                  <span className="min-w-0 flex-1 truncate font-mono text-xs text-slate-600">
                    {path}
                  </span>
                  <span
                    className={`font-mono text-xs font-bold ${statusColor(run.status)}`}
                  >
                    {run.status ?? "—"}
                  </span>
                  {run.provider && (
                    <span
                      className={`hidden rounded-full px-2 py-0.5 text-[10px] font-semibold sm:inline ${providerColor(run.provider)}`}
                    >
                      {run.provider}
                    </span>
                  )}
                  <span className="shrink-0 text-[11px] text-slate-400">
                    {relativeTime(run.created_at)}
                  </span>
                </div>
              );
            })}
          </div>
        ) : loading && recentRuns.length === 0 ? (
          <ListRowSkeleton rows={3} />
        ) : (
          <p className="py-8 text-center text-sm text-slate-500">
            Run a diagnosis to see real history here.
          </p>
        )}
      </section>
    </div>
  );
}
