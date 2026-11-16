import { useEffect, useRef, useState } from "react";
import type { RunSummary } from "./types";
import { Header } from "./components/Header";
import type { WorkspaceTab } from "./components/Header";
import { Sidebar } from "./components/Sidebar";
import { RequestBuilder } from "./components/RequestBuilder";
import { ResponseViewer } from "./components/ResponseViewer";
import { DiagnosisViewer } from "./components/DiagnosisViewer";
import { AskPanel } from "./components/AskPanel";
import { AuthPanel } from "./components/AuthPanel";
import { Dashboard } from "./components/Dashboard";
import { EventTimeline } from "./components/EventTimeline";
import type { TimelineEntry } from "./components/EventTimeline";
import { PRESETS } from "./data/presets";
import { authedFetch, apiUrl } from "./lib/api";
import { authConfigured, supabase, signOut } from "./lib/supabase";
import { streamDiagnosis } from "./lib/sse";
import type { Session } from "@supabase/supabase-js";
import type { Citation, Observation, Patch, RequestSpec, RetestResult, ScenarioPreset } from "./types";

export default function App() {
  const [activeTab, setActiveTab] = useState<WorkspaceTab>("dashboard");

  // Supabase Auth session (null until loaded; always null when auth is off).
  const [session, setSession] = useState<Session | null>(null);
  const [authReady, setAuthReady] = useState<boolean>(!authConfigured);
  useEffect(() => {
    if (!supabase) return;
    void supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setAuthReady(true);
    });
    const { data: subscription } = supabase.auth.onAuthStateChange(
      (_event, nextSession) => {
        setSession(nextSession);
        setAuthReady(true);
      }
    );
    return () => subscription.subscription.unsubscribe();
  }, []);

  async function handleSignOut() {
    await signOut();
    setSession(null);
  }
  const [presets, setPresets] = useState<ScenarioPreset[]>(PRESETS);
  const [selectedPresetId, setSelectedPresetId] = useState<string>(PRESETS[0].id);
  const [activeCollection, setActiveCollection] = useState<string>("github");

  // Active request and observation state
  const [spec, setSpec] = useState<RequestSpec>(PRESETS[0].spec);
  const [observation, setObservation] = useState<Observation | null>(
    PRESETS[0].sampleObservation ?? null
  );

  // Diagnosis state
  const [whatWentWrong, setWhatWentWrong] = useState<string>(
    PRESETS[0].sampleDiagnosis?.whatWentWrong ?? ""
  );
  const [suggestedFix, setSuggestedFix] = useState<string>(
    PRESETS[0].sampleDiagnosis?.suggestedFix ?? ""
  );
  const [patch, setPatch] = useState<Patch | null>(
    PRESETS[0].sampleDiagnosis?.patch ?? null
  );
  const [sources, setSources] = useState<{ id: string; label: string; url?: string }[]>(
    PRESETS[0].sampleDiagnosis?.sources ?? []
  );
  const [citations, setCitations] = useState<Citation[]>([]);

  // Execution status
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("done");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [retestResult, setRetestResult] = useState<RetestResult | null>(null);
  const [retesting, setRetesting] = useState<boolean>(false);
  // Which provider actually served the streamed diagnosis (label from the
  // stream) and any failovers recorded on the way.
  const [providerLabel, setProviderLabel] = useState<string | null>(null);
  const [fallbacks, setFallbacks] = useState<string[]>([]);
  const [isMockData, setIsMockData] = useState<boolean>(true);

  // Live pipeline timeline, one entry per server-sent event received
  // during a diagnosis run.
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const timelineIdRef = useRef(1);

  function pushTimelineEntry(label: string, state: TimelineEntry["state"], detail?: string) {
    setTimeline((prev) => [
      ...prev,
      { id: timelineIdRef.current++, label, state, detail },
    ]);
  }

  /** Complete the currently running entry (used when the next step starts). */
  function finishRunningTimeline(detail?: string, state: TimelineEntry["state"] = "done") {
    setTimeline((prev) =>
      prev.map((entry) =>
        entry.state === "running"
          ? { ...entry, state, detail: detail ?? entry.detail }
          : entry
      )
    );
  }

  // Recent run history from the backend (Supabase / SQLite)
  const [recentRuns, setRecentRuns] = useState<RunSummary[]>([]);

  // Cancel in-flight SSE streams when the workspace unmounts.
  const streamAbortRef = useRef<AbortController | null>(null);
  useEffect(() => () => streamAbortRef.current?.abort(), []);

  // Load real run history for the sidebar; refresh after each diagnosis.
  const refreshRuns = async () => {
    try {
      const response = await authedFetch("/api/runs");
      if (response.ok) {
        const data = await response.json();
        setRecentRuns(data.runs ?? []);
      }
    } catch {
      // Sidebar stays on preset data when the backend is unreachable
    }
  };
  useEffect(() => {
    void refreshRuns();
  }, []);

  // Preset Selection
  function handleSelectPreset(preset: ScenarioPreset) {
    setSelectedPresetId(preset.id);
    setActiveCollection(preset.collection);
    setSpec(preset.spec);
    setObservation(preset.sampleObservation ?? null);
    setWhatWentWrong(preset.sampleDiagnosis?.whatWentWrong ?? "");
    setSuggestedFix(preset.sampleDiagnosis?.suggestedFix ?? "");
    setPatch(preset.sampleDiagnosis?.patch ?? null);
    setSources(preset.sampleDiagnosis?.sources ?? []);
    setCitations([]);
    setRetestResult(null);
    setError(null);
    setStatus("done");
    setIsMockData(true);
  }

  // Live Test Execution
  async function handleRunTest() {
    streamAbortRef.current?.abort();
    const controller = new AbortController();
    streamAbortRef.current = controller;

    setStatus("running");
    setError(null);
    setIsMockData(false);
    setProviderLabel(null);
    setFallbacks([]);
    setRetestResult(null);
    setWhatWentWrong("");
    setSuggestedFix("");
    setPatch(null);
    setCitations([]);
    setSources([]);
    setTimeline([]);
    pushTimelineEntry("Executing HTTP request", "running");

    try {
      await streamDiagnosis(
        spec,
        (event, data) => {
          if (event === "observation") {
            setObservation(data);
            finishRunningTimeline(
              `HTTP ${data.status}${data.latency_ms != null ? ` · ${Math.round(data.latency_ms)} ms` : ""}`
            );
            pushTimelineEntry("Retrieving documentation", "running");
          }
          if (event === "token") {
            setWhatWentWrong((prev) => prev + data.text);
          }
          if (event === "retrieval") {
            setCitations(data.chunks ?? []);
            finishRunningTimeline(`${(data.chunks ?? []).length} chunks retrieved`);
            pushTimelineEntry("Generating diagnosis", "running");
          }
          if (event === "provider") {
            setProviderLabel(data.name ?? null);
            setTimeline((prev) =>
              prev.map((entry) =>
                entry.state === "running"
                  ? { ...entry, detail: `${data.name} · streaming…` }
                  : entry
              )
            );
          }
          if (event === "provider_fallback") {
            setFallbacks((prev) => [
              ...prev,
              `${data.from}: ${data.reason}`,
            ]);
            pushTimelineEntry(`${data.from} unavailable`, "warn", data.reason);
          }
          if (event === "done") {
            if (data.diagnosis) {
              setWhatWentWrong(data.diagnosis);
            }
            if (data.patch) {
              setPatch(data.patch);
              setSuggestedFix(JSON.stringify(data.patch, null, 2));
            }
            finishRunningTimeline("Diagnosis complete");
            setStatus("done");
            void refreshRuns();
          }
          if (event === "error") {
            finishRunningTimeline(data.message ?? data.code, "error");
            if (data.code === "no_providers_configured") {
              setError(
                "No AI provider keys configured in .env. Test completed; add GEMINI_API_KEY for live LLM streaming."
              );
            } else {
              setError(data.message ?? data.code);
            }
            setStatus("error");
          }
        },
        controller.signal
      );
    } catch (err) {
      if ((err as Error)?.name === "AbortError") return; // cancelled by a newer run/unmount
      setError(String(err));
      setStatus("error");
    } finally {
      if (streamAbortRef.current === controller) {
        streamAbortRef.current = null;
      }
    }
  }

  // Apply fix and re-run
  async function handleApplyFix(patchOverride?: Patch) {
    const activePatch = patchOverride ?? patch;
    if (!activePatch) return;
    setRetesting(true);
    setError(null);

    try {
      const response = await authedFetch("/api/retest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ spec, patch: activePatch, before: observation }),
      });

      if (!response.ok) {
        throw new Error(`Retest request failed: HTTP ${response.status}`);
      }

      const data: RetestResult = await response.json();
      setRetestResult(data);
      setPatch(activePatch);
      setObservation(data.observation);

      // Update current spec with applied patch
      const patchedSpec: RequestSpec = {
        ...spec,
        headers: { ...spec.headers, ...activePatch.set_headers },
        body: activePatch.set_body ?? spec.body,
        approved: true,
      };
      setSpec(patchedSpec);
    } catch (err) {
      // Never fabricate a passing retest. Surface the failure so the fix
      // stays explicitly unverified until the server confirms it.
      setRetestResult(null);
      setError(`Retest failed: ${String(err)}`);
    } finally {
      setRetesting(false);
    }
  }

  // Copy as cURL command. Resolves to false when the clipboard is unavailable
  // (e.g. non-secure origins), so the UI never shows a false "Copied!".
  async function handleCopyCurl(): Promise<boolean> {
    let curl = `curl -X ${spec.method} "${spec.url}"`;
    for (const [key, val] of Object.entries(spec.headers)) {
      curl += ` \\\n  -H "${key}: ${val}"`;
    }
    if (spec.body) {
      const bodyStr = typeof spec.body === "string" ? spec.body : JSON.stringify(spec.body);
      curl += ` \\\n  -d '${bodyStr}'`;
    }
    try {
      await navigator.clipboard.writeText(curl);
      return true;
    } catch {
      setError("Clipboard copy failed — your browser blocked clipboard access.");
      return false;
    }
  }

  // Import OpenAPI file (JSON only; YAML is rejected with a clear message)
  // Uploaded OpenAPI files ("My own API" collection) and their KB ingestion
  const [uploadedFiles, setUploadedFiles] = useState<
    { name: string; size: number }[]
  >([]);
  const [importing, setImporting] = useState(false);
  const [bundledChunks, setBundledChunks] = useState<number | null>(null);

  const refreshKbStats = async () => {
    try {
      const response = await authedFetch("/api/kb/stats");
      if (response.ok) {
        setBundledChunks((await response.json()).chunk_count ?? null);
      }
    } catch {
      // keep the previous value when the backend is unreachable
    }
  };
  useEffect(() => {
    void refreshKbStats();
  }, []);

  const refreshFiles = async () => {
    try {
      const response = await authedFetch("/api/files");
      if (response.ok) {
        setUploadedFiles((await response.json()).files ?? []);
      }
    } catch {
      // file list stays empty when the backend is unreachable
    }
  };
  useEffect(() => {
    void refreshFiles();
  }, []);

  // Full import flow: upload the spec, ingest it into the knowledge base,
  // then create presets for its endpoints so they can be tested directly.
  async function handleImportOpenAPI(file: File) {
    setError(null);
    setNotice(null);
    setImporting(true);
    try {
      // 1. Upload (backend validates extension/size, sanitizes the name)
      const form = new FormData();
      form.append("files", file);
      const uploadRes = await authedFetch("/api/upload", {
        method: "POST",
        body: form,
      });
      const uploadData = await uploadRes.json().catch(() => null);
      if (!uploadRes.ok) {
        throw new Error(uploadData?.detail ?? "Upload failed");
      }
      const savedName: string = uploadData.files[0].name;

      // 2. Ingest into the knowledge base (chunks + embeddings)
      const ingestRes = await authedFetch("/api/ingest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ files: [savedName] }),
      });
      const ingestData = await ingestRes.json().catch(() => null);
      if (!ingestRes.ok) {
        throw new Error(ingestData?.detail ?? "Knowledge base ingest failed");
      }
      const chunksAdded: number =
        ingestData?.chunks_added ??
        (ingestData?.files ?? []).reduce(
          (sum: number, entry: { chunks_added?: number }) =>
            sum + (entry.chunks_added ?? 0),
          0
        );

      // 3. Build presets from the spec (JSON only; YAML still indexes fine)
      const newPresets: ScenarioPreset[] = [];
      if (/\.json$/i.test(file.name)) {
        try {
          const json = JSON.parse(await file.text());
          const base: string = (json.servers?.[0]?.url ?? "").replace(/\/$/, "");
          const entries: { method: string; path: string }[] = [];
          for (const [path, methods] of Object.entries(json.paths ?? {})) {
            for (const method of Object.keys(methods as object)) {
              if (["get", "post", "put", "patch", "delete"].includes(method)) {
                entries.push({ method: method.toUpperCase(), path });
              }
            }
          }
          for (const entry of entries.slice(0, 8)) {
            newPresets.push({
              id: `custom-${Date.now()}-${newPresets.length}`,
              collection: "custom",
              name: `${entry.method} ${entry.path}`,
              statusBadge: 200,
              method: entry.method,
              path: entry.path,
              spec: {
                method: entry.method,
                url: `${base}${entry.path}`.replace(/\{[^}]+\}/g, "1"),
                headers: { Accept: "application/json" },
                body: null,
                timeout_s: 10,
                expected_status: null,
                question: `Analyze this ${entry.method} request against ${json.info?.title ?? file.name}`,
                approved: false,
              },
            });
          }
        } catch {
          // Spec structure unexpected — upload+ingest already succeeded
        }
      }
      if (newPresets.length === 0) {
        newPresets.push({
          id: `custom-${Date.now()}`,
          collection: "custom",
          name: file.name,
          statusBadge: 200,
          method: "GET",
          path: `/${file.name}`,
          spec: {
            method: "GET",
            url: "http://localhost:8001/v1/profile",
            headers: { Accept: "application/json" },
            body: null,
            timeout_s: 5,
            expected_status: 200,
            question: `Ask about the imported API ${file.name}`,
            approved: false,
          },
        });
      }

      setPresets((prev) => [...newPresets, ...prev]);
      setActiveCollection("custom");
      handleSelectPreset(newPresets[0]);
      void refreshFiles();
      void refreshKbStats();
      setNotice(
        `Imported "${file.name}" — ${chunksAdded} chunk${chunksAdded === 1 ? "" : "s"} indexed into the knowledge base.`
      );
    } catch (err) {
      setError(`OpenAPI import failed: ${(err as Error).message}`);
    } finally {
      setImporting(false);
    }
  }

  // Auth gate: with Supabase Auth configured, nothing renders until a
  // session exists (UI only — the backend independently rejects tokenless
  // requests). With auth off, authReady is true from the start.
  if (!authReady) {
    return (
      <div className="app-shell flex min-h-screen flex-col text-slate-800 antialiased">
        <Header activeTab={activeTab} onTabChange={setActiveTab} />
        <main className="flex-1 p-5" />
      </div>
    );
  }

  if (authConfigured && !session) {
    return (
      <div className="app-shell flex min-h-screen flex-col text-slate-800 antialiased">
        <AuthPanel
          onAuthed={() => {
            // onAuthStateChange usually delivers the session first; re-read
            // it so a signUp that signs in straight away also flips the gate.
            void supabase?.auth.getSession().then(({ data }) => setSession(data.session));
          }}
        />
      </div>
    );
  }

  return (
    <div className="app-shell flex min-h-screen flex-col text-slate-800 antialiased">
      {/* Top Header */}
      <Header
        activeTab={activeTab}
        onTabChange={setActiveTab}
        userEmail={session?.user?.email ?? null}
        onSignOut={handleSignOut}
      />

      <main className="flex-1 p-5">
        {notice && (
          <div className="mx-auto mb-4 max-w-[1550px] rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-xs font-medium text-emerald-800">
            {notice}
          </div>
        )}
        {activeTab === "dashboard" ? (
          <Dashboard
            onNavigateToDiagnose={() => setActiveTab("diagnose")}
            recentRuns={recentRuns}
            bundledChunks={bundledChunks}
          />
        ) : activeTab === "diagnose" ? (
          <div className="mx-auto grid max-w-[1550px] grid-cols-1 items-start gap-5 lg:grid-cols-12">
            {/* Left Column: Collections & Recent Tests (approx 3 cols) */}
            <div className="lg:col-span-3">
              <Sidebar
                presets={presets}
                runs={recentRuns}
                files={uploadedFiles}
                importing={importing}
                bundledChunks={bundledChunks}
                selectedPresetId={selectedPresetId}
                onSelectPreset={handleSelectPreset}
                activeCollection={activeCollection}
                onSelectCollection={setActiveCollection}
                onImportOpenAPI={handleImportOpenAPI}
              />
            </div>

            {/* Middle Column: Request & Response (approx 5 cols) */}
            <div className="space-y-5 lg:col-span-5">
              <RequestBuilder
                spec={spec}
                onChangeSpec={setSpec}
                onRunTest={handleRunTest}
                observationStatus={observation?.status ?? null}
                busy={status === "running"}
              />
              <EventTimeline entries={timeline} />
              <ResponseViewer observation={observation} isMockData={isMockData} />
            </div>

            {/* Right Column: AI Diagnosis (approx 4 cols) */}
            <div className="lg:col-span-4">
              <DiagnosisViewer
                whatWentWrong={whatWentWrong}
                suggestedFix={suggestedFix}
                sources={sources}
                citations={citations}
                patch={patch}
                status={status}
                error={error}
                retestResult={retestResult}
                onApplyFix={handleApplyFix}
                onCopyCurl={handleCopyCurl}
                retesting={retesting}
                observationStatus={observation?.status}
                providerLabel={providerLabel}
                fallbacks={fallbacks}
              />
            </div>
          </div>
        ) : (
          <AskPanel />
        )}
      </main>
    </div>
  );
}
