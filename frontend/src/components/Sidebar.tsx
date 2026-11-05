import { useRef } from "react";
import type { RunSummary, ScenarioPreset } from "../types";

export function Sidebar({
  presets,
  runs,
  files,
  importing,
  bundledChunks,
  selectedPresetId,
  onSelectPreset,
  activeCollection,
  onSelectCollection,
  onImportOpenAPI,
}: {
  presets: ScenarioPreset[];
  runs?: RunSummary[];
  files?: { name: string; size: number }[];
  importing?: boolean;
  bundledChunks?: number | null;
  selectedPresetId: string;
  onSelectPreset: (preset: ScenarioPreset) => void;
  activeCollection: string;
  onSelectCollection: (col: string) => void;
  onImportOpenAPI: (file: File) => void;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);

  return (
    <aside className="flex flex-col rounded-2xl border border-slate-200 bg-white p-4 shadow-xs">
      {/* COLLECTIONS */}
      <div>
        <h2 className="mb-2.5 text-[11px] font-bold tracking-wider text-slate-400 uppercase">
          Collections
        </h2>
        <div className="space-y-1.5">
          <button
            onClick={() => onSelectCollection("github")}
            className={`w-full text-left transition-all ${
              activeCollection === "github"
                ? "rounded-xl border border-blue-200/80 bg-blue-50/80 p-3 shadow-2xs"
                : "rounded-xl p-3 text-slate-700 hover:bg-slate-50"
            }`}
          >
            <div className="text-xs font-bold text-slate-900">
              Bundled API docs
            </div>
            <div className="mt-0.5 text-[11px] text-slate-500">
              {bundledChunks
                ? `${bundledChunks} chunks indexed (MDN, RFC, specs)`
                : "Loading knowledge base..."}
            </div>
          </button>
          {activeCollection === "github" && (
            <div className="space-y-1 pl-2">
              {presets
                .filter((preset) => preset.collection === "github")
                .map((preset) => {
                  const isSelected = preset.id === selectedPresetId;
                  return (
                    <button
                      key={preset.id}
                      onClick={() => onSelectPreset(preset)}
                      className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left transition-colors ${
                        isSelected
                          ? "bg-amber-50 text-slate-900"
                          : "text-slate-600 hover:bg-slate-50"
                      }`}
                    >
                      <span className="text-[11px] font-bold">
                        {preset.method}
                      </span>
                      <span className="truncate font-mono text-[11px]">
                        {preset.path}
                      </span>
                    </button>
                  );
                })}
            </div>
          )}

          <button
            onClick={() => onSelectCollection("custom")}
            className={`w-full text-left transition-all ${
              activeCollection === "custom"
                ? "rounded-xl border border-blue-200/80 bg-blue-50/80 p-3 shadow-2xs"
                : "rounded-xl p-3 text-slate-700 hover:bg-slate-50"
            }`}
          >
            <div className="text-xs font-medium text-slate-800">My own API (OpenAPI file)</div>
            <div className="mt-0.5 text-[11px] text-slate-500">
              {files && files.length > 0
                ? `${files.length} imported file${files.length === 1 ? "" : "s"} indexed`
                : "Import a spec to index it"}
            </div>
          </button>
          {activeCollection === "custom" && files && files.length > 0 && (
            <div className="mt-1 space-y-1 pl-2">
              {files.map((file) => (
                <div
                  key={file.name}
                  className="truncate rounded-lg bg-slate-50 px-2.5 py-1.5 font-mono text-[11px] text-slate-600"
                  title={`${file.name} · ${(file.size / 1024).toFixed(1)} KB`}
                >
                  {file.name}
                </div>
              ))}
              {presets
                .filter((preset) => preset.collection === "custom")
                .map((preset) => {
                  const isSelected = preset.id === selectedPresetId;
                  return (
                    <button
                      key={preset.id}
                      onClick={() => onSelectPreset(preset)}
                      className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left transition-colors ${
                        isSelected
                          ? "bg-amber-50 text-slate-900"
                          : "text-slate-600 hover:bg-slate-50"
                      }`}
                    >
                      <span className="text-[11px] font-bold">
                        {preset.method}
                      </span>
                      <span className="truncate font-mono text-[11px]">
                        {preset.path}
                      </span>
                    </button>
                  );
                })}
            </div>
          )}
        </div>
      </div>

      {/* RECENT TESTS — real run history when available, presets otherwise */}
      <div className="mt-6 flex-1">
        <h2 className="mb-2.5 text-[11px] font-bold tracking-wider text-slate-400 uppercase">
          Recent Tests
        </h2>
        <div className="space-y-1.5">
          {(runs && runs.length > 0 ? runs : presets).map((item) => {
            const isRun = "run_id" in item;
            const status = isRun ? item.status : (item as ScenarioPreset).statusBadge;
            const method = isRun
              ? (item as RunSummary).method
              : (item as ScenarioPreset).method;
            const path = isRun
              ? (item as RunSummary).url.replace(/^https?:\/\/[^/]+/, "")
              : (item as ScenarioPreset).path;
            const badgeTone =
              (status ?? 0) >= 500
                ? "text-rose-700"
                : (status ?? 0) >= 400 && status !== 422
                ? "text-rose-600"
                : status === 422
                ? "text-amber-800"
                : "text-emerald-600";

            return (
              <div
                key={isRun ? (item as RunSummary).run_id : (item as ScenarioPreset).id}
                className={`flex items-center gap-2 rounded-xl p-2.5 ${
                  isRun ? "bg-slate-50/60" : ""
                }`}
                title={
                  isRun
                    ? `${(item as RunSummary).url} · ${(item as RunSummary).provider ?? ""}`
                    : undefined
                }
              >
                <span className={`text-xs font-bold ${badgeTone}`}>
                  {status ?? "—"}
                </span>
                <span className="text-xs font-bold text-slate-900">
                  {method}
                </span>
                <span className="truncate font-mono text-xs text-slate-600">
                  {path}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* IMPORT BUTTON */}
      <div className="mt-6 pt-2">
        <input
          type="file"
          ref={fileInputRef}
          className="hidden"
          accept=".json,.yaml,.yml"
          onChange={(e) => {
            if (e.target.files?.[0]) onImportOpenAPI(e.target.files[0]);
          }}
        />
        <button
          onClick={() => fileInputRef.current?.click()}
          className="w-full rounded-xl border border-slate-200 bg-white py-2.5 px-4 text-center text-xs font-semibold text-slate-700 shadow-2xs transition-colors hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-60"
          disabled={importing}
        >
          {importing ? "Uploading & indexing..." : "Import OpenAPI file"}
        </button>
      </div>
    </aside>
  );
}

