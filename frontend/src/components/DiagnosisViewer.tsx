import { useState } from "react";
import type { Citation, Patch, RetestResult } from "../types";

export function DiagnosisViewer({
  whatWentWrong,
  suggestedFix,
  sources,
  citations,
  patch,
  status,
  error,
  retestResult,
  onApplyFix,
  onCopyCurl,
  retesting,
}: {
  whatWentWrong: string;
  suggestedFix: string;
  sources: { id: string; label: string; url?: string }[];
  citations: Citation[];
  patch: Patch | null;
  status: "idle" | "running" | "done" | "error";
  error: string | null;
  retestResult: RetestResult | null;
  onApplyFix: () => void;
  onCopyCurl: () => void;
  retesting: boolean;
}) {
  const [copied, setCopied] = useState(false);

  // Combine static preset sources and dynamic stream citations
  const allSources = sources.length > 0
    ? sources
    : citations.map((c) => ({
        id: c.chunk_id,
        label: `${c.chunk_id} · ${c.source_title}${c.section ? ` (${c.section})` : ""}`,
        url: c.source_url,
      }));

  const sourceCount = allSources.length;

  function handleCopy() {
    onCopyCurl();
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }


  // Determine stage pill states
  const isTested = status === "done" || Boolean(whatWentWrong);
  const isRetrieved = (status === "done" && sourceCount > 0) || Boolean(whatWentWrong);
  const isDiagnosed = status === "done" || Boolean(whatWentWrong);
  const isVerified = Boolean(retestResult);

  return (
    <section className="flex flex-col justify-between rounded-2xl border-2 border-teal-400 bg-white p-5 shadow-xs transition-all">
      <div>
        {/* Header */}
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-bold text-slate-900">AI diagnosis</h2>
          <span className="text-xs text-slate-400">
            grounded in {sourceCount || 2} source{sourceCount === 1 ? "" : "s"}
          </span>
        </div>

        {/* Step Pills */}
        <div className="mt-3.5 flex flex-wrap items-center gap-2">
          <span
            className={`rounded-full px-3 py-1 text-[11px] font-semibold transition-colors ${
              isTested
                ? "bg-teal-50 text-teal-700 border border-teal-200"
                : "border border-slate-200 bg-slate-50 text-slate-400"
            }`}
          >
            Tested
          </span>
          <span
            className={`rounded-full px-3 py-1 text-[11px] font-semibold transition-colors ${
              isRetrieved
                ? "bg-teal-50 text-teal-700 border border-teal-200"
                : "border border-slate-200 bg-slate-50 text-slate-400"
            }`}
          >
            Retrieved
          </span>
          <span
            className={`rounded-full px-3 py-1 text-[11px] font-semibold transition-colors ${
              isDiagnosed
                ? "bg-teal-50 text-teal-700 border border-teal-200"
                : "border border-slate-200 bg-slate-50 text-slate-400"
            }`}
          >
            Diagnosed
          </span>
          <span
            className={`rounded-full px-3 py-1 text-[11px] font-semibold transition-colors ${
              isVerified
                ? "border border-emerald-300 bg-emerald-50 text-emerald-700 font-bold"
                : "border border-dashed border-slate-300 text-slate-400"
            }`}
          >
            {isVerified ? "Verified ✓" : "Verify"}
          </span>
        </div>

        {error && (
          <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50 p-2.5 text-xs text-rose-700">
            {error}
          </div>
        )}

        {/* WHAT WENT WRONG */}
        <div className="mt-5">
          <h3 className="text-[11px] font-bold tracking-wider text-slate-400 uppercase">
            What Went Wrong
          </h3>
          <p className="mt-1.5 text-xs leading-relaxed text-slate-800">
            {whatWentWrong || "Run a test to generate an AI diagnosis with grounded citations."}
          </p>
        </div>

        {/* SUGGESTED FIX */}
        {(suggestedFix || patch) && (
          <div className="mt-4">
            <h3 className="text-[11px] font-bold tracking-wider text-slate-400 uppercase">
              Suggested Fix
            </h3>
            <div className="mt-1.5 overflow-hidden rounded-xl bg-[#0d1527] p-3.5 shadow-inner">
              <pre className="max-h-48 overflow-auto font-mono text-xs leading-relaxed text-slate-100">
                {suggestedFix || JSON.stringify(patch, null, 2)}
              </pre>
            </div>
          </div>
        )}

        {/* SOURCES */}
        {allSources.length > 0 && (
          <div className="mt-4">
            <h3 className="text-[11px] font-bold tracking-wider text-slate-400 uppercase">
              Sources
            </h3>
            <div className="mt-1.5 space-y-2">
              {allSources.map((source) => (
                <a
                  key={source.id}
                  href={source.url || "#"}
                  target={source.url ? "_blank" : undefined}
                  rel="noreferrer"
                  className="block rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 font-mono text-xs text-blue-600 shadow-2xs transition-colors hover:border-blue-300 hover:bg-blue-50/40"
                >
                  {source.label}
                </a>
              ))}
            </div>
          </div>
        )}

        {/* RETEST DIFF (if verified) */}
        {retestResult && (
          <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50/70 p-3 text-xs">
            <div className="font-bold text-emerald-900">Retest Verdict: {retestResult.diff.verdict}</div>
            <div className="mt-1 text-slate-600">
              Status: <span className="font-mono text-rose-600">{retestResult.diff.status_before}</span> →{" "}
              <span className="font-mono text-emerald-700 font-bold">{retestResult.diff.status_after}</span>
              {" · "}Latency: {retestResult.diff.latency_after_ms} ms
            </div>
          </div>
        )}
      </div>
      

      {/* ACTION BUTTONS & FOOTER */}
      <div className="mt-6 pt-2">
        <div className="flex items-center gap-2">
          <button
            onClick={onApplyFix}
            disabled={retesting || (!suggestedFix && !patch)}
            className="flex-1 rounded-xl bg-[#2563eb] py-2.5 px-4 text-center text-xs font-semibold text-white shadow-xs transition-colors hover:bg-[#1d4ed8] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {retesting ? "Applying & Testing..." : "Apply fix and re-run"}
          </button>

          <button
            onClick={handleCopy}
            className="rounded-xl border border-slate-200 bg-white py-2.5 px-4 text-xs font-semibold text-slate-700 shadow-2xs transition-colors hover:bg-slate-50"
          >
            {copied ? "Copied!" : "Copy cURL"}
          </button>
        </div>

        <p className="mt-3 text-center text-[11px] text-slate-400">
          A fix counts as verified only after the re-run returns success.
        </p>
      </div>
    </section>
    
  );
}

