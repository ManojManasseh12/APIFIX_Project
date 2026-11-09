import type { Observation } from "../types";

export function ResponseViewer({
  observation,
  isMockData,
}: {
  observation: Observation | null;
  isMockData?: boolean;
}) {
  if (!observation) {
    return (
      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
        <h2 className="text-sm font-bold text-slate-900">Response</h2>
        <div className="mt-4 flex h-36 items-center justify-center rounded-xl border border-dashed border-slate-200 bg-slate-50 text-xs text-slate-500">
          Click "Run test" to execute the request.
        </div>
      </section>
    );
  }

  const status = observation.status;
  const statusText =
    status === 200
      ? "200 OK"
      : status === 201
      ? "201 Created"
      : status === 401
      ? "401 Unauthorized"
      : status === 403
      ? "403 Forbidden"
      : status === 404
      ? "404 Not Found"
      : status === 422
      ? "422 Unprocessable Entity"
      : status === 429
      ? "429 Too Many Requests"
      : status === 500
      ? "500 Internal Server Error"
      : observation.error_type || `${status ?? "Unknown"}`;

  const badgeStyle =
    status && status < 300
      ? "bg-emerald-100 text-emerald-800"
      : status === 422
      ? "bg-[#fef3c7] text-[#92400e]"
      : "bg-rose-100 text-rose-800";

  const sizeKb = observation.body_preview
    ? `${(observation.body_preview.length / 1024).toFixed(1)} KB`
    : null;

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs">
      {/* Header */}
      <div className="flex items-center gap-2.5">
        <h2 className="text-sm font-bold text-slate-900">Response</h2>
        <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${badgeStyle}`}>
          {statusText}
        </span>
      </div>

      {/* Sub-meta */}
      <div className="mt-1 text-xs text-slate-500">
        {observation.latency_ms} ms
        {sizeKb ? ` · ${sizeKb}${observation.truncated ? " (truncated)" : ""}` : ""}
        {observation.error_message ? ` · ${observation.error_message}` : ""}
      </div>

      {/* Response Body Box */}
      <div className="mt-3 overflow-hidden rounded-xl border border-slate-200 bg-[#f8fafc] p-3.5 shadow-2xs">
        <pre className="max-h-56 overflow-auto font-mono text-xs leading-relaxed text-slate-800">
          {observation.body_preview || "(empty response body)"}
        </pre>
      </div>

      {/* Footer text */}
      <div className="mt-2 text-[11px] text-slate-500">
        {isMockData ? "Sample data for the mockup." : "Live execution observation."}
      </div>
    </section>
  );
}

