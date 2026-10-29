import type { Citation, Patch } from "../types";

export function DiagnosisPanel(props: {
  text: string;
  citations: Citation[];
  patch: Patch | null;
  invalid: string[];
  status: string;
  error: string | null;
  onRetest: (patch: Patch) => void;
}) {
  const { text, citations, patch, invalid, status, error, onRetest } = props;

  return (
    <section className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
        Diagnosis
      </h2>

      {error && <p className="mt-2 text-sm text-rose-400">{error}</p>}
      {status === "running" && !text && (
        <p className="mt-2 text-sm text-slate-400">Retrieving evidence...</p>
      )}

      <div className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-200">
        {text}
      </div>

      {invalid.length > 0 && (
        <p className="mt-3 rounded-lg bg-amber-950 p-2 text-xs text-amber-300">
          Removed invalid citations: {invalid.join(", ")}
        </p>
      )}

      {citations.length > 0 && (
        <div className="mt-4">
          <h3 className="text-xs uppercase tracking-wide text-slate-400">Evidence</h3>
          <ul className="mt-2 space-y-2">
            {citations.map((citation) => (
              <li key={citation.chunk_id} className="rounded-lg bg-slate-800 p-2 text-xs">
                <a
                  className="font-mono text-emerald-300 underline"
                  href={citation.source_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {citation.chunk_id}
                </a>
                <span className="ml-2 text-slate-300">
                  {citation.source_title}
                  {citation.section ? ` — ${citation.section}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {patch && (
        <div className="mt-4">
          <h3 className="text-xs uppercase tracking-wide text-slate-400">
            Proposed patch
          </h3>
          <pre className="mt-2 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-emerald-200">
            {JSON.stringify(patch, null, 2)}
          </pre>
          <button
            className="mt-3 rounded-lg bg-emerald-500 px-4 py-2 text-sm font-medium text-slate-950"
            onClick={() => onRetest(patch)}
          >
            Approve and retest
          </button>
        </div>
      )}
    </section>
  );
}
