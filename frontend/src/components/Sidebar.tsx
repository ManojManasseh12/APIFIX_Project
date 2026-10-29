import { useRef } from "react";
import type { ScenarioPreset } from "../types";

export function Sidebar({
  presets,
  selectedPresetId,
  onSelectPreset,
  activeCollection,
  onSelectCollection,
  onImportOpenAPI,
}: {
  presets: ScenarioPreset[];
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
            <div className="text-xs font-bold text-slate-900">GitHub REST API</div>
            <div className="mt-0.5 text-[11px] text-slate-500">1,242 indexed endpoints</div>
          </button>

          <button
            onClick={() => onSelectCollection("custom")}
            className={`w-full text-left transition-all ${
              activeCollection === "custom"
                ? "rounded-xl border border-blue-200/80 bg-blue-50/80 p-3 shadow-2xs"
                : "rounded-xl p-3 text-slate-700 hover:bg-slate-50"
            }`}
          >
            <div className="text-xs font-medium text-slate-800">My own API (OpenAPI file)</div>
          </button>
        </div>
      </div>

      {/* RECENT TESTS */}
      <div className="mt-6 flex-1">
        <h2 className="mb-2.5 text-[11px] font-bold tracking-wider text-slate-400 uppercase">
          Recent Tests
        </h2>
        <div className="space-y-1.5">
          {presets.map((preset) => {
            const isSelected = preset.id === selectedPresetId;
            const badgeTone =
              preset.statusBadge >= 500
                ? "text-rose-700"
                : preset.statusBadge >= 400 && preset.statusBadge !== 422
                ? "text-rose-600"
                : preset.statusBadge === 422
                ? "text-amber-800"
                : "text-emerald-600";

            return (
              <button
                key={preset.id}
                onClick={() => onSelectPreset(preset)}
                className={`flex w-full items-center gap-2 text-left transition-all ${
                  isSelected
                    ? "rounded-xl border border-amber-200/90 bg-[#fef9c3] p-2.5 shadow-2xs"
                    : "rounded-xl p-2.5 hover:bg-slate-50"
                }`}
              >
                <span className={`text-xs font-bold ${badgeTone}`}>
                  {preset.statusBadge}
                </span>
                <span className="text-xs font-bold text-slate-900">
                  {preset.method}
                </span>
                <span className="truncate font-mono text-xs text-slate-600">
                  {preset.path}
                </span>
              </button>
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
          className="w-full rounded-xl border border-slate-200 bg-white py-2.5 px-4 text-center text-xs font-semibold text-slate-700 shadow-2xs transition-colors hover:bg-slate-50 hover:text-slate-900"
        >
          Import OpenAPI file
        </button>
      </div>
    </aside>
  );
}

