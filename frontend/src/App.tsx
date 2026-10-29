import { useState } from "react";
import { DiagnosisPanel } from "./components/DiagnosisPanel";
import { RequestForm } from "./components/RequestForm";
import { ResultPanel } from "./components/ResultPanel";
import { RetestDiff } from "./components/RetestDiff";
import { streamDiagnosis } from "./lib/sse";
import type { Citation, Observation, Patch, RequestSpec, RetestResult } from "./types";

export default function App() {
  const [observation, setObservation] = useState<Observation | null>(null);
  const [diagnosis, setDiagnosis] = useState("");
  const [citations, setCitations] = useState<Citation[]>([]);
  const [patch, setPatch] = useState<Patch | null>(null);
  const [invalid, setInvalid] = useState<string[]>([]);
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const [lastSpec, setLastSpec] = useState<RequestSpec | null>(null);
  const [retest, setRetest] = useState<RetestResult | null>(null);

  async function run(spec: RequestSpec) {
    setLastSpec(spec);
    setStatus("running");
    setDiagnosis("");
    setCitations([]);
    setPatch(null);
    setInvalid([]);
    setError(null);
    setRetest(null);

    try {
      await streamDiagnosis(spec, (event, data) => {
        if (event === "observation") setObservation(data);
        if (event === "token") setDiagnosis((prev) => prev + data.text);
        if (event === "retrieval") setCitations(data.chunks ?? []);
        if (event === "done") {
          setPatch(data.patch ?? null);
          setInvalid(data.invalid_citations ?? []);
          setStatus("done");
        }
        if (event === "error") {
          setError(data.message ?? data.code);
          setStatus("error");
        }
      });
    } catch (err) {
      setError(String(err));
      setStatus("error");
    }
  }

  async function retestNow(patchToApply: Patch) {
    if (!lastSpec) return;
    const response = await fetch("/api/retest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spec: lastSpec, patch: patchToApply, before: observation }),
    });
    if (!response.ok) {
      setError(`Retest failed: ${response.status}`);
      return;
    }
    setRetest(await response.json());
  }

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <div className="mx-auto grid max-w-7xl gap-6 p-6 lg:grid-cols-2">
        <RequestForm onSubmit={run} busy={status === "running"} />
        <div className="space-y-6">
          <ResultPanel observation={observation} />
          <DiagnosisPanel
            text={diagnosis}
            citations={citations}
            patch={patch}
            invalid={invalid}
            status={status}
            error={error}
            onRetest={retestNow}
          />
          <RetestDiff result={retest} />
        </div>
      </div>
    </main>
  );
}
