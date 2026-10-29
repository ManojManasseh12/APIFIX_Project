export type WorkspaceTab = "diagnose" | "ask";

export function Header({
  activeTab,
  onTabChange,
}: {
  activeTab: WorkspaceTab;
  onTabChange: (tab: WorkspaceTab) => void;
}) {
  const tabs: { id: WorkspaceTab; label: string }[] = [
    { id: "diagnose", label: "Diagnose" },
    { id: "ask", label: "Ask API" },
  ];

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
            className={`rounded-lg px-4 py-1.5 text-xs font-semibold transition-colors ${
              activeTab === tab.id
                ? "bg-white text-teal-700 shadow-xs"
                : "text-slate-500 hover:text-slate-800"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      {/* Status Badges */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-medium text-slate-600 shadow-2xs">
          Runner: <span className="font-semibold text-slate-800">Playwright</span>
        </div>
        <div className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-medium text-slate-600 shadow-2xs">
          Search: <span className="font-semibold text-slate-800">Qdrant Cloud, hybrid</span>
        </div>
        <div className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-medium text-slate-600 shadow-2xs">
          Model: <span className="font-semibold text-slate-800">Qwen3 4B, local</span>
        </div>
      </div>
    </header>
  );
}
