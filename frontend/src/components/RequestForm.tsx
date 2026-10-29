import { useState } from "react";
import type { RequestSpec } from "../types";

const EMPTY: RequestSpec = {
  method: "GET",
  url: "http://localhost:8001/v1/profile",
  headers: {},
  body: null,
  timeout_s: 5,
  expected_status: 200,
  question: "Why does this request return 401?",
  approved: false,
};

export function RequestForm({
  onSubmit,
  busy,
}: {
  onSubmit: (spec: RequestSpec) => void;
  busy: boolean;
}) {
  const [spec, setSpec] = useState<RequestSpec>(EMPTY);
  const [headersText, setHeadersText] = useState("Accept: application/json");
  const [bodyText, setBodyText] = useState("");

  function submit() {
    const headers: Record<string, string> = {};
    for (const line of headersText.split("\n")) {
      const index = line.indexOf(":");
      if (index > 0) headers[line.slice(0, index).trim()] = line.slice(index + 1).trim();
    }

    let body: unknown = null;
    if (bodyText.trim()) {
      try {
        body = JSON.parse(bodyText);
      } catch {
        body = bodyText;
      }
    }

    onSubmit({ ...spec, headers, body });
  }

  return (
    <section className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
      <h1 className="text-xl font-semibold">APIFix AI</h1>
      <p className="mt-1 text-sm text-slate-400">
        Test a request, retrieve evidence, diagnose, then verify the fix.
      </p>

      <div className="mt-4 flex gap-2">
        <select
          className="rounded-lg bg-slate-800 px-3 py-2"
          value={spec.method}
          onChange={(event) => setSpec({ ...spec, method: event.target.value })}
        >
          {["GET", "POST", "PUT", "PATCH", "DELETE"].map((method) => (
            <option key={method}>{method}</option>
          ))}
        </select>
        <input
          className="min-w-0 flex-1 rounded-lg bg-slate-800 px-3 py-2"
          value={spec.url}
          onChange={(event) => setSpec({ ...spec, url: event.target.value })}
        />
      </div>

      <label className="mt-4 block text-xs uppercase tracking-wide text-slate-400">
        Headers (one per line)
      </label>
      <textarea
        className="mt-1 h-24 w-full rounded-lg bg-slate-800 p-3 font-mono text-sm"
        value={headersText}
        onChange={(event) => setHeadersText(event.target.value)}
      />

      <label className="mt-4 block text-xs uppercase tracking-wide text-slate-400">
        Body (JSON or raw text)
      </label>
      <textarea
        className="mt-1 h-28 w-full rounded-lg bg-slate-800 p-3 font-mono text-sm"
        value={bodyText}
        onChange={(event) => setBodyText(event.target.value)}
      />

      <label className="mt-4 block text-xs uppercase tracking-wide text-slate-400">
        Question
      </label>
      <input
        className="mt-1 w-full rounded-lg bg-slate-800 px-3 py-2"
        value={spec.question}
        onChange={(event) => setSpec({ ...spec, question: event.target.value })}
      />

      <label className="mt-4 flex items-center gap-2 text-sm text-slate-300">
        <input
          type="checkbox"
          checked={spec.approved}
          onChange={(event) => setSpec({ ...spec, approved: event.target.checked })}
        />
        I approve sending this request (required for POST/PUT/PATCH/DELETE)
      </label>

      <button
        className="mt-5 w-full rounded-lg bg-emerald-500 px-4 py-2 font-medium text-slate-950 disabled:opacity-50"
        disabled={busy}
        onClick={submit}
      >
        {busy ? "Running..." : "Test and diagnose"}
      </button>
    </section>
  );
}
