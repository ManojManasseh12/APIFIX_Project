import { useEffect, useState } from "react";
import { apiUrl } from "../lib/api";
import { ProfileSidebar } from "./ProfileSidebar";

export type WorkspaceTab = "dashboard" | "diagnose" | "ask";

type HealthInfo = {
  retrieval_backend?: string;
  providers?: string[];
};

export function Header({
  activeTab,
  onTabChange,
  userEmail = null,
  onSignOut,
}: {
  activeTab: WorkspaceTab;
  onTabChange: (tab: WorkspaceTab) => void;
  userEmail?: string | null;
  onSignOut?: () => void;
}) {
  const tabs: { id: WorkspaceTab; label: string; icon: string }[] = [
    { id: "dashboard", label: "Dashboard", icon: "M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" },
    { id: "diagnose", label: "Diagnose",  icon: "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" },
    { id: "ask",       label: "Ask API",  icon: "M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" },
  ];

  // Status pills reflect the real backend, fetched live from /api/health.
  const [health, setHealth] = useState<HealthInfo | null>(null);
  useEffect(() => {
    fetch(apiUrl("/api/health"))
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => setHealth(data))
      .catch(() => setHealth(null));
  }, []);

  const searchLabel =
    health?.retrieval_backend === "supabase"
      ? "pgvector + BM25 hybrid (Supabase)"
      : health?.retrieval_backend === "local"
        ? "Chroma + BM25 hybrid (local)"
        : "hybrid retrieval";
  const modelLabel =
    health?.providers && health.providers.length > 0
      ? health.providers
          .map((family) => family.charAt(0).toUpperCase() + family.slice(1))
          .join(" → ")
      : "LLM fallback chain";

  // Profile slide-over, opened from the identity chip.
  const [profileOpen, setProfileOpen] = useState(false);

  return (
    <header className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-200 bg-white px-6 py-3">
      {/* Brand */}
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-slate-950 text-white shadow-xs">
          <svg
            className="h-5 w-5 text-teal-400"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="m3 16 5-7 4 5 5-8 4 6" />
          </svg>
        </div>
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-base font-bold tracking-tight text-slate-900">APIFix AI</h1>
            <span className="hidden text-xs text-slate-500 sm:inline">
              Test the endpoint. Find the cause. Fix it.
            </span>
          </div>
          <span className="text-xs text-slate-500 sm:hidden">
            Test the endpoint. Find the cause. Fix it.
          </span>
        </div>
      </div>

      {/* Workspace tabs */}
      <nav className="flex items-center gap-1 rounded-xl border border-slate-200 bg-slate-50 p-1">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => onTabChange(tab.id)}
            className={`flex items-center gap-1.5 rounded-lg px-3.5 py-1.5 text-xs font-semibold transition-all ${
              activeTab === tab.id
                ? "bg-white text-teal-700 shadow-sm ring-1 ring-slate-200"
                : "text-slate-500 hover:text-slate-800"
            }`}
          >
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d={tab.icon} />
            </svg>
            {tab.label}
          </button>
        ))}
      </nav>

      {/* Status Badges */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-medium text-slate-600 shadow-2xs">
          Runner: <span className="font-semibold text-slate-800">HTTPX async</span>
        </div>
        <div className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-medium text-slate-600 shadow-2xs">
          Search: <span className="font-semibold text-slate-800">{searchLabel}</span>
        </div>
        <div className="hidden rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-medium text-slate-600 shadow-2xs xl:inline">
          Model: <span className="font-semibold text-slate-800">{modelLabel}</span>
        </div>
        {/* Logged-in identity (Supabase Auth). Hidden entirely when auth is off. */}
        {userEmail && (
          <button
            type="button"
            onClick={() => setProfileOpen(true)}
            title="Open profile"
            className="flex items-center gap-2 rounded-full border border-teal-200 bg-teal-50 px-3 py-1 text-xs font-medium text-teal-800 shadow-2xs transition hover:bg-teal-100"
          >
            <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
              <circle cx="12" cy="7" r="4" />
            </svg>
            <span className="max-w-[180px] truncate font-semibold">{userEmail}</span>
            <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="m6 9 6 6 6-6" />
            </svg>
          </button>
        )}
      </div>

      {userEmail && (
        <ProfileSidebar
          open={profileOpen}
          onClose={() => setProfileOpen(false)}
          onSignOut={async () => {
            setProfileOpen(false);
            await onSignOut?.();
          }}
        />
      )}
    </header>
  );
}
