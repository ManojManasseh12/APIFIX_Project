import { useCallback, useEffect, useState } from "react";
import { authedFetch } from "../lib/api";

type PromptSlot = {
  purpose: string;
  text: string;
  is_custom: boolean;
  updated_at: string | null;
};

const META: Record<string, { title: string; description: string }> = {
  diagnosis: {
    title: "Diagnosis prompt",
    description:
      "The system prompt for every Diagnose run: facts vs. hypotheses, citations, the patch JSON block.",
  },
  ask: {
    title: "Ask prompt",
    description:
      "The system prompt for the Ask API tab: documentation-grounded answers with chunk citations.",
  },
  tool: {
    title: "Tool result prompt",
    description:
      "The prompt that presents live tool results (weather, currency, DNS…) to the user.",
  },
};

/**
 * Prompt templates manager: view, edit, and reset the three system
 * prompts. Overrides are stored in the backend's prompt_templates table
 * and apply from the next request — nothing is cached in the UI.
 */
export function PromptTemplates() {
  const [slots, setSlots] = useState<PromptSlot[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const response = await authedFetch("/api/prompts");
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const list: PromptSlot[] = data.prompts ?? [];
      setSlots(list);
      setDrafts(Object.fromEntries(list.map((slot) => [slot.purpose, slot.text])));
    } catch (err) {
      setError(`Could not load prompts: ${(err as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function save(purpose: string) {
    setBusy(`save-${purpose}`);
    setNotice(null);
    setError(null);
    try {
      const response = await authedFetch(`/api/prompts/${purpose}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: drafts[purpose] }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(data?.detail ?? `HTTP ${response.status}`);
      }
      const saved: PromptSlot = data;
      setSlots((prev) => prev.map((slot) => (slot.purpose === purpose ? saved : slot)));
      setNotice(`Saved — the ${purpose} prompt applies from the next request.`);
    } catch (err) {
      setError(`Save failed: ${(err as Error).message}`);
    } finally {
      setBusy(null);
    }
  }

  async function reset(purpose: string) {
    setBusy(`reset-${purpose}`);
    setNotice(null);
    setError(null);
    try {
      const response = await authedFetch(`/api/prompts/${purpose}`, { method: "DELETE" });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(data?.detail ?? `HTTP ${response.status}`);
      }
      const restored: PromptSlot = data;
      setSlots((prev) => prev.map((slot) => (slot.purpose === purpose ? restored : slot)));
      setDrafts((prev) => ({ ...prev, [purpose]: restored.text }));
      setNotice(`Reset — the built-in ${purpose} prompt is active again.`);
    } catch (err) {
      setError(`Reset failed: ${(err as Error).message}`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto max-w-[1100px] space-y-4">
      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-lg font-bold text-slate-900">Prompt templates</h2>
        <p className="mt-1 text-xs text-slate-500">
          The system prompts sent to the LLM on every request. Custom versions are stored in
          the backend's <code className="rounded bg-slate-100 px-1 py-0.5 text-[11px]">prompt_templates</code> table
          and take effect from the next request — no restart needed. Built-in defaults are
          always available through Reset.
        </p>
      </div>

      {notice && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-xs font-medium text-emerald-800">
          {notice}
        </div>
      )}
      {error && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-2.5 text-xs font-medium text-rose-800">
          {error}
        </div>
      )}

      {loading ? (
        <div className="rounded-2xl border border-slate-200 bg-white p-8 text-center text-xs text-slate-400 shadow-sm">
          Loading prompts…
        </div>
      ) : (
        slots.map((slot) => {
          const meta = META[slot.purpose] ?? { title: slot.purpose, description: "" };
          const dirty = drafts[slot.purpose] !== slot.text;
          return (
            <div key={slot.purpose} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-bold text-slate-900">{meta.title}</h3>
                    {slot.is_custom ? (
                      <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-700">
                        Custom
                      </span>
                    ) : (
                      <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-slate-500">
                        Built-in default
                      </span>
                    )}
                  </div>
                  <p className="mt-0.5 text-[11px] text-slate-500">{meta.description}</p>
                </div>
                <div className="flex items-center gap-2">
                  {dirty && (
                    <button
                      type="button"
                      onClick={() => setDrafts((prev) => ({ ...prev, [slot.purpose]: slot.text }))}
                      className="rounded-lg px-3 py-1.5 text-xs font-semibold text-slate-500 transition hover:bg-slate-100"
                    >
                      Discard edits
                    </button>
                  )}
                  {slot.is_custom && (
                    <button
                      type="button"
                      disabled={busy !== null}
                      onClick={() => reset(slot.purpose)}
                      className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 transition hover:bg-slate-50 disabled:opacity-50"
                    >
                      {busy === `reset-${slot.purpose}` ? "Resetting…" : "Reset to default"}
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={busy !== null || !dirty}
                    onClick={() => save(slot.purpose)}
                    className="rounded-lg bg-slate-950 px-4 py-1.5 text-xs font-semibold text-white transition hover:bg-slate-800 disabled:opacity-40"
                  >
                    {busy === `save-${slot.purpose}` ? "Saving…" : "Save"}
                  </button>
                </div>
              </div>

              <textarea
                value={drafts[slot.purpose] ?? ""}
                onChange={(event) =>
                  setDrafts((prev) => ({ ...prev, [slot.purpose]: event.target.value }))
                }
                spellCheck={false}
                className="mt-3 h-64 w-full resize-y rounded-xl border border-slate-200 bg-slate-50 p-3 font-mono text-[11px] leading-relaxed text-slate-800 focus:border-teal-400 focus:bg-white focus:outline-none"
              />
              <p className="mt-1.5 text-[11px] text-slate-400">
                {slot.text.length} characters ·{" "}
                {dirty ? "unsaved changes" : slot.is_custom ? "custom version active" : "default active"}
              </p>
            </div>
          );
        })
      )}
    </div>
  );
}
