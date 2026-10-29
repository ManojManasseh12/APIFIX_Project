import type { Observation } from "../types";

export function ResultPanel({ observation }: { observation: Observation | null }) {
  if (!observation) {
    return (
      <section className="rounded-2xl border border-slate-800 bg-slate-900 p-5 text-sm text-slate-400">
        No test yet.
      </section>
    );
  }

  const status = observation.status ?? observation.error_type ?? "unknown";
  const tone =
    observation.error_type || (observation.status ?? 0) >= 400
      ? "text-rose-400"
      : "text-emerald-400";

  return (
    <section className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
        Observation
      </h2>
      <div className="mt-2 flex items-baseline gap-3">
        <span className={`text-3xl font-bold ${tone}`}>{status}</span>
        <span className="text-sm text-slate-400">{observation.latency_ms} ms</span>
      </div>
      {observation.redirect_location && (
        <p className="mt-2 text-sm text-amber-300">
          Redirect to: {observation.redirect_location}
        </p>
      )}
      {observation.error_message && (
        <p className="mt-2 text-sm text-rose-300">{observation.error_message}</p>
      )}
      <pre className="mt-3 max-h-64 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-300">
        {observation.body_preview || "(empty body)"}
      </pre>
    </section>
  );
}
