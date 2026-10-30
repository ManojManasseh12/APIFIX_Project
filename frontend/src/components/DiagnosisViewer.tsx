import { useEffect, useState } from "react";
import type { Citation, Patch, RetestResult } from "../types";

const REDACTED = "<REDACTED>";

function containsRedacted(value: unknown): boolean {
  if (typeof value === "string") return value.includes(REDACTED);
  if (Array.isArray(value)) return value.some(containsRedacted);
  if (value && typeof value === "object") {
    return Object.values(value as Record<string, unknown>).some(containsRedacted);
  }
  return false;
}

function normalizePatch(value: unknown): Patch {
  const raw = (value ?? {}) as Partial<Patch>;
  return {
    set_headers: Object.fromEntries(
      Object.entries(raw.set_headers ?? {}).map(([key, item]) => [
        key,
        String(item),
      ])
    ),
    remove_headers: (raw.remove_headers ?? []).map(String),
    set_body: raw.set_body ?? null,
    set_url: raw.set_url ?? null,
  };
}

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
  onApplyFix: (patch: Patch) => void;
  onCopyCurl: () => void;
  retesting: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const [patchDraft, setPatchDraft] = useState("");
  const [editingPatch, setEditingPatch] = useState(false);
  const [secrets, setSecrets] = useState<Record<string, string>>({});

  useEffect(() => {
    setPatchDraft(patch ? JSON.stringify(normalizePatch(patch), null, 2) : "");
    setSecrets({});
    setEditingPatch(false);
  }, [patch]);

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

  let parsedPatch: Patch | null = null;
  let patchError: string | null = null;
  if (patchDraft) {
    try {
      parsedPatch = normalizePatch(JSON.parse(patchDraft));
    } catch (err) {
      patchError = err instanceof Error ? err.message : "Invalid JSON";
    }
  }

  const redactedHeaderKeys = parsedPatch
    ? Object.entries(parsedPatch.set_headers)
        .filter(([, value]) => value.includes(REDACTED))
        .map(([key]) => key)
    : [];
  const hasOtherRedaction = parsedPatch
    ? containsRedacted({
        remove_headers: parsedPatch.remove_headers,
        set_body: parsedPatch.set_body,
        set_url: parsedPatch.set_url,
      })
    : false;

  function resolvedPatch(base: Patch): Patch {
    const headers = Object.fromEntries(
      Object.entries(base.set_headers).map(([key, value]) => [
        key,
        value.includes(REDACTED)
          ? value.replace(REDACTED, secrets[key] ?? REDACTED)
          : value,
      ])
    );
    return { ...base, set_headers: headers };
  }

  const missingSecrets = redactedHeaderKeys.some(
    (key) => !secrets[key]?.trim()
  );
  const unresolvedRedaction = Boolean(
    parsedPatch && containsRedacted(resolvedPatch(parsedPatch))
  );
  const canRetest =
    Boolean(parsedPatch) &&
    !patchError &&
    !missingSecrets &&
    !unresolvedRedaction &&
    !retesting;

  function handleApply() {
    if (!parsedPatch || !canRetest) return;
    onApplyFix(resolvedPatch(parsedPatch));
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
            <div className="flex items-center justify-between">
              <h3 className="text-[11px] font-bold tracking-wider text-slate-400 uppercase">
                Suggested Fix
              </h3>
              {patch && (
                <button
                  type="button"
                  onClick={() => setEditingPatch((value) => !value)}
                  className="rounded-md border border-slate-200 px-2 py-1 text-[10px] font-semibold text-slate-600 hover:bg-slate-50"
                >
                  {editingPatch ? "Preview" : "Edit JSON"}
                </button>
              )}
            </div>
            <div className="mt-1.5 overflow-hidden rounded-xl bg-[#0d1527] p-3.5 shadow-inner">
              {editingPatch ? (
                <textarea
                  value={patchDraft}
                  onChange={(event) => setPatchDraft(event.target.value)}
                  spellCheck={false}
                  rows={10}
                  className="w-full resize-y bg-transparent font-mono text-xs leading-relaxed text-slate-100 outline-none"
                />
              ) : (
                <pre className="max-h-48 overflow-auto font-mono text-xs leading-relaxed text-slate-100">
                  {patchDraft || suggestedFix || JSON.stringify(patch, null, 2)}
                </pre>
              )}
            </div>
            {patchError && (
              <p className="mt-2 rounded-lg bg-rose-50 p-2 text-[11px] text-rose-700">
                Patch JSON is invalid: {patchError}
              </p>
            )}

            {redactedHeaderKeys.length > 0 && (
              <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-2.5">
                <p className="text-[11px] font-semibold text-amber-900">
                  This patch contains redacted secrets
                </p>
                <p className="mt-0.5 text-[11px] text-amber-800">
                  Enter the real value for each header. For example, paste only
                  the token that replaces <code>&lt;REDACTED&gt;</code>.
                </p>
                {redactedHeaderKeys.map((key) => (
                  <label key={key} className="mt-2 block text-[11px] text-amber-900">
                    <span className="font-mono">{key}</span>
                    <input
                      type="text"
                      autoComplete="off"
                      spellCheck={false}
                      value={secrets[key] ?? ""}
                      onChange={(event) =>
                        setSecrets((prev) => ({ ...prev, [key]: event.target.value }))
                      }
                      placeholder="Paste the real token or secret"
                      className="mt-1 w-full rounded-md border border-amber-300 bg-white px-2 py-1.5 font-mono text-xs text-slate-800 outline-none focus:border-amber-500"
                    />
                  </label>
                ))}
              </div>
            )}

            {hasOtherRedaction && (
              <p className="mt-2 rounded-lg bg-amber-50 p-2 text-[11px] text-amber-800">
                The patch also contains <code>&lt;REDACTED&gt;</code> outside
                headers. Use <strong>Edit JSON</strong> to replace it before
                retesting.
              </p>
            )}
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
            onClick={handleApply}
            disabled={!canRetest}
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
          {missingSecrets
            ? "Fill in the redacted header value(s) to enable the retest."
            : unresolvedRedaction
            ? "Replace the remaining <REDACTED> values in Edit JSON to enable the retest."
            : "A fix counts as verified only after the re-run returns success."}
        </p>
      </div>
    </section>
    
  );
}

