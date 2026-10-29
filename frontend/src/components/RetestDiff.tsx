import type { RetestResult } from "../types";

export function RetestDiff({ result }: { result: RetestResult | null }) {
  if (!result) return null;

  const { diff } = result;
  const tone = diff.verdict === "pass" ? "text-emerald-400" : "text-amber-400";

  return (
    <section className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
        Retest
      </h2>
      <p className="mt-2 text-3xl font-bold">
        {diff.status_before} → {diff.status_after}
      </p>
      <p className={`text-sm ${tone}`}>Verdict: {diff.verdict}</p>
      <p className="mt-1 text-xs text-slate-400">
        {diff.latency_before_ms} ms → {diff.latency_after_ms} ms
        {diff.body_changed ? " · body changed" : " · body unchanged"}
      </p>
    </section>
  );
}
