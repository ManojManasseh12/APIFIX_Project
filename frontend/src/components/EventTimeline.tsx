export type TimelineEntry = {
  id: number;
  label: string;
  state: "running" | "done" | "warn" | "error";
  detail?: string;
};

const ICONS: Record<TimelineEntry["state"], JSX.Element> = {
  running: (
    <svg className="h-3.5 w-3.5 animate-spin text-teal-600" viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-90" fill="currentColor" d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4z" />
    </svg>
  ),
  done: (
    <svg className="h-3.5 w-3.5 text-emerald-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 6 9 17l-5-5" />
    </svg>
  ),
  warn: (
    <svg className="h-3.5 w-3.5 text-amber-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
    </svg>
  ),
  error: (
    <svg className="h-3.5 w-3.5 text-rose-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <path d="m15 9-6 6m0-6 6 6" />
    </svg>
  ),
};

/**
 * Live pipeline feed built from the backend's server-sent events:
 * request executed → docs retrieved → provider → generating → done.
 * Only real events appear here; each entry is one SSE frame.
 */
export function EventTimeline({ entries }: { entries: TimelineEntry[] }) {
  if (entries.length === 0) return null;

  return (
    <div
      role="log"
      aria-live="polite"
      aria-label="Pipeline events"
      className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"
    >
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-xs font-bold uppercase tracking-wide text-slate-400">
          Pipeline events
        </h3>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-500">
          live · server-sent events
        </span>
      </div>
      <ol className="space-y-2.5">
        {entries.map((entry) => (
          <li key={entry.id} className="flex items-start gap-2.5 text-xs">
            <span className="mt-0.5 shrink-0">{ICONS[entry.state]}</span>
            <span className="min-w-0">
              <span
                className={
                  "font-semibold " +
                  (entry.state === "error"
                    ? "text-rose-700"
                    : entry.state === "warn"
                      ? "text-amber-700"
                      : entry.state === "running"
                        ? "text-slate-900"
                        : "text-slate-600")
                }
              >
                {entry.label}
              </span>
              {entry.detail && (
                <span className="ml-1.5 break-words text-slate-400">{entry.detail}</span>
              )}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
