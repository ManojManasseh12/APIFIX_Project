import { useState } from "react";
import type { RequestSpec } from "../types";

export function RequestBuilder({
  spec,
  onChangeSpec,
  onRunTest,
  busy,
}: {
  spec: RequestSpec;
  onChangeSpec: (spec: RequestSpec) => void;
  onRunTest: () => void;
  busy: boolean;
}) {
  const [activeTab, setActiveTab] = useState<"body" | "headers" | "auth" | "params">("body");

  // Format headers count for tab label
  const headerCount = Object.keys(spec.headers).length;

  const bodyString =
    typeof spec.body === "string"
      ? spec.body
      : spec.body
      ? JSON.stringify(spec.body, null, 2)
      : "";

  const headersString = Object.entries(spec.headers)
    .map(([k, v]) => `${k}: ${v}`)
    .join("\n");

  const bearerToken =
    spec.headers["Authorization"]?.replace(/^Bearer\s+/i, "") ||
    spec.headers["authorization"]?.replace(/^Bearer\s+/i, "") ||
    "";

  function handleBodyChange(text: string) {
    let parsed: unknown = text;
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
    onChangeSpec({ ...spec, body: parsed });
  }

  function handleHeadersChange(text: string) {
    const headers: Record<string, string> = {};
    for (const line of text.split("\n")) {
      const idx = line.indexOf(":");
      if (idx > 0) {
        headers[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
      }
    }
    onChangeSpec({ ...spec, headers });
  }

  function handleAuthChange(token: string) {
    const updated = { ...spec.headers };
    if (token.trim()) {
      updated["Authorization"] = `Bearer ${token.trim()}`;
    } else {
      delete updated["Authorization"];
      delete updated["authorization"];
    }
    onChangeSpec({ ...spec, headers: updated });
  }

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs">
      {/* URL BAR ROW */}
      <div className="flex items-center gap-2">
        <select
          value={spec.method}
          onChange={(e) => onChangeSpec({ ...spec, method: e.target.value })}
          className="rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-xs font-bold text-slate-800 shadow-2xs focus:border-blue-500 focus:outline-none"
        >
          {["POST", "GET", "PUT", "PATCH", "DELETE", "HEAD"].map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>

        <input
          type="text"
          value={spec.url}
          onChange={(e) => onChangeSpec({ ...spec, url: e.target.value })}
          placeholder="https://api.example.com/..."
          className="min-w-0 flex-1 rounded-xl border border-slate-200 bg-white px-3.5 py-2 font-mono text-xs text-slate-800 shadow-2xs focus:border-blue-500 focus:outline-none"
        />

        <button
          onClick={onRunTest}
          disabled={busy}
          className="rounded-xl bg-[#2563eb] px-5 py-2 text-xs font-semibold text-white shadow-xs transition-colors hover:bg-[#1d4ed8] disabled:cursor-not-allowed disabled:opacity-60"
        >
          {busy ? "Running..." : "Run test"}
        </button>
      </div>

      {/* TABS ROW */}
      <div className="mt-3.5 flex border-b border-slate-200 text-xs">
        <button
          onClick={() => setActiveTab("body")}
          className={`px-3 py-2 font-semibold transition-colors ${
            activeTab === "body"
              ? "border-b-2 border-slate-900 text-slate-900"
              : "text-slate-500 hover:text-slate-800"
          }`}
        >
          Body
        </button>
        <button
          onClick={() => setActiveTab("headers")}
          className={`px-3 py-2 font-medium transition-colors ${
            activeTab === "headers"
              ? "border-b-2 border-slate-900 font-semibold text-slate-900"
              : "text-slate-500 hover:text-slate-800"
          }`}
        >
          Headers ({headerCount || 2})
        </button>
        <button
          onClick={() => setActiveTab("auth")}
          className={`px-3 py-2 font-medium transition-colors ${
            activeTab === "auth"
              ? "border-b-2 border-slate-900 font-semibold text-slate-900"
              : "text-slate-500 hover:text-slate-800"
          }`}
        >
          Auth: Bearer
        </button>
        <button
          onClick={() => setActiveTab("params")}
          className={`px-3 py-2 font-medium transition-colors ${
            activeTab === "params"
              ? "border-b-2 border-slate-900 font-semibold text-slate-900"
              : "text-slate-500 hover:text-slate-800"
          }`}
        >
          Params
        </button>
      </div>

      {/* TAB CONTENT (Dark Code Block) */}
      <div className="mt-3">
        {activeTab === "body" && (
          <div className="overflow-hidden rounded-xl bg-[#0d1527] p-3.5 shadow-inner">
            <textarea
              rows={5}
              value={bodyString}
              onChange={(e) => handleBodyChange(e.target.value)}
              placeholder={'{\n  "body": "Steps to reproduce the crash"\n}'}
              className="w-full resize-none bg-transparent font-mono text-xs text-slate-100 placeholder-slate-500 focus:outline-none"
            />
          </div>
        )}

        {activeTab === "headers" && (
          <div className="overflow-hidden rounded-xl bg-[#0d1527] p-3.5 shadow-inner">
            <textarea
              rows={5}
              value={headersString}
              onChange={(e) => handleHeadersChange(e.target.value)}
              placeholder="Header-Name: value"
              className="w-full resize-none bg-transparent font-mono text-xs text-slate-100 placeholder-slate-500 focus:outline-none"
            />
          </div>
        )}

        {activeTab === "auth" && (
          <div className="rounded-xl bg-[#0d1527] p-4 text-xs font-mono text-slate-200">
            <label className="mb-2 block text-slate-400">Bearer Token:</label>
            <input
              type="text"
              value={bearerToken}
              onChange={(e) => handleAuthChange(e.target.value)}
              placeholder="ghp_xxx or token"
              className="w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100 focus:border-blue-500 focus:outline-none"
            />
          </div>
        )}

        {activeTab === "params" && (
          <div className="rounded-xl bg-[#0d1527] p-4 text-xs font-mono text-slate-300">
            <p className="text-slate-400">Query parameters are extracted from the URL query string.</p>
          </div>
        )}
      </div>
    </section>
  );
}
