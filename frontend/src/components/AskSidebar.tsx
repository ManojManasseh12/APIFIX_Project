import { useEffect, useState } from "react";
import { MCP_APPS, statusBadgeClass, statusLabel } from "../data/mcpApps";
import type { McpApp } from "../data/mcpApps";
import type {
  ConnectionTool,
  ConnectionsResponse,
  GitHubConnection,
} from "../types";

/**
 * ChatGPT-style Connections sidebar for the Ask tab.
 *
 * Desktop: an in-flow column that collapses to a 64px icon rail.
 * Mobile:  a slide-in drawer with a dimmed backdrop (closed by the
 *          backdrop, the toggle, or Escape).
 *
 * Honest by design: GitHub's badge and Connect button reflect the real
 * state reported by GET /api/connections. Nothing is ever shown as
 * "Connected" unless the backend verified the token with GitHub.
 */
export function AskSidebar({
  open,
  onToggle,
  onClose,
  connections,
  connectionsError,
}: {
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  connections: ConnectionsResponse | null;
  connectionsError: string | null;
}) {
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const github = connections?.github ?? null;
  const githubApp = MCP_APPS.find((app) => app.id === "github") ?? MCP_APPS[0];
  const statusUnavailable = !connections && Boolean(connectionsError);
  const selected = MCP_APPS.find((app) => app.id === selectedId) ?? null;

  // Escape: leave the detail view first, then close the drawer.
  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (selectedId) setSelectedId(null);
      else onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, selectedId, onClose]);

  const term = query.trim().toLowerCase();
  const filtered = MCP_APPS.filter(
    (app) =>
      !term ||
      app.name.toLowerCase().includes(term) ||
      app.tagline.toLowerCase().includes(term)
  );

  function pick(appId: string) {
    setSelectedId(appId);
    if (!open) onToggle(); // collapsed rail → expand so the detail fits
  }

  function badgeFor(app: McpApp): { label: string; className: string } {
    if (app.id === "github" && github?.connected) {
      return {
        label: "Connected",
        className: "border-emerald-200 bg-emerald-50 text-emerald-700",
      };
    }
    if (app.id === "github" && github?.error) {
      return {
        label: "Error",
        className: "border-amber-200 bg-amber-50 text-amber-700",
      };
    }
    return { label: statusLabel(app.status), className: statusBadgeClass(app.status) };
  }

  return (
    <aside
      id="connections-panel"
      aria-label="Connections"
      className={[
        "z-40 flex shrink-0 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-xs transition-all duration-200",
        // Mobile: slide-in drawer.
        "fixed bottom-3 left-3 top-3 w-[280px]",
        open ? "translate-x-0" : "-translate-x-[calc(100%+1.5rem)]",
        // Desktop: in-flow, full-height column that animates between 260px and 64px.
        "lg:static lg:inset-auto lg:translate-x-0 lg:sticky lg:top-5 lg:h-[calc(100vh-2.5rem)]",
        open ? "lg:w-[260px]" : "lg:w-16",
      ].join(" ")}
    >
      {/* ── Expanded view (mobile drawer always uses this) ─────────────── */}
      <div
        className={
          open ? "flex h-full min-h-0 flex-col" : "flex h-full min-h-0 flex-col lg:hidden"
        }
      >
        <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
          <div>
            <h2 className="text-sm font-bold text-slate-900">Connections</h2>
            <p className="mt-0.5 text-[10px] text-slate-500">
              Apps the assistant can use
            </p>
          </div>
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={open}
            aria-controls="connections-panel"
            aria-label="Collapse connections"
            title="Collapse"
            className="rounded-lg border border-slate-200 p-1.5 text-slate-500 transition hover:border-slate-300 hover:text-slate-800"
          >
            <PanelIcon />
          </button>
        </div>

        {selected ? (
          <AppDetail
            app={selected}
            onBack={() => setSelectedId(null)}
            github={github}
            statusUnavailable={statusUnavailable}
          />
        ) : (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="px-4 pt-3">
              <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search apps…"
                aria-label="Search apps"
                className="w-full rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-[11px] text-slate-800 outline-none transition-colors placeholder:text-slate-400 focus:border-indigo-300 focus:bg-white"
              />
            </div>

            <div className="mt-3 min-h-0 flex-1 overflow-y-auto px-3 pb-2">
              <p className="px-1 text-[10px] font-bold uppercase tracking-wider text-slate-400">
                Connected
              </p>
              {github?.connected ? (
                <button
                  type="button"
                  onClick={() => setSelectedId("github")}
                  className="mt-1.5 flex w-full items-center gap-2.5 rounded-xl border border-emerald-100 bg-emerald-50/50 px-2 py-2 text-left transition-colors hover:bg-emerald-50"
                >
                  <AppIcon app={githubApp} size="sm" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[11px] font-semibold text-slate-800">
                      GitHub
                    </span>
                    <span className="block truncate text-[10px] text-slate-500">
                      @{github.account}
                    </span>
                  </span>
                  <span className="shrink-0 rounded-full border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 text-[9px] font-semibold text-emerald-700">
                    Connected
                  </span>
                </button>
              ) : (
                <p className="mt-1.5 rounded-xl border border-dashed border-slate-200 px-2.5 py-2 text-[11px] leading-snug text-slate-500">
                  {statusUnavailable
                    ? "Connection status unavailable — is the backend running?"
                    : github?.configured
                      ? "GitHub token found, but GitHub did not accept it. Open GitHub for details."
                      : "No apps connected yet. Add GITHUB_PAT to the backend .env to connect GitHub."}
                </p>
              )}

              <p className="mt-4 px-1 text-[10px] font-bold uppercase tracking-wider text-slate-400">
                Available
              </p>
              {filtered.length === 0 ? (
                <p className="mt-2 px-1 text-[11px] text-slate-500">
                  No app matches “{query.trim()}”.
                </p>
              ) : (
                <ul className="mt-1.5 space-y-1">
                  {filtered
                    // A connected app is listed once, under Connected.
                    .filter((app) => !(app.id === "github" && github?.connected))
                    .map((app) => {
                      const badge = badgeFor(app);
                      return (
                        <li key={app.id}>
                          <button
                            type="button"
                            onClick={() => setSelectedId(app.id)}
                            className="flex w-full items-center gap-2.5 rounded-xl px-2 py-2 text-left transition-colors hover:bg-slate-50"
                          >
                            <AppIcon app={app} size="sm" />
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-[11px] font-semibold text-slate-800">
                                {app.name}
                              </span>
                              <span className="block truncate text-[10px] text-slate-500">
                                {app.id === "github" && github?.connected
                                  ? `@${github.account}`
                                  : app.tagline}
                              </span>
                            </span>
                            <span
                              className={`shrink-0 rounded-full border px-1.5 py-0.5 text-[9px] font-semibold ${badge.className}`}
                            >
                              {badge.label}
                            </span>
                          </button>
                        </li>
                      );
                    })}
                </ul>
              )}
            </div>

            <p className="border-t border-slate-100 px-4 py-2.5 text-[10px] leading-snug text-slate-400">
              {statusUnavailable
                ? "Connection status unavailable — start the backend to see real state."
                : github?.connected
                  ? "GitHub is connected via a server-side token. Ask tools are read-only for now."
                  : github?.configured
                    ? "GitHub token found but not verified — open GitHub for details."
                    : "Preview — add GITHUB_PAT to the backend .env to connect GitHub."}
            </p>
          </div>
        )}
      </div>

      {/* ── Collapsed rail (desktop only) ──────────────────────────────── */}
      <div
        className={
          open ? "hidden" : "hidden h-full flex-col items-center gap-2 py-3 lg:flex"
        }
      >
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          aria-controls="connections-panel"
          aria-label="Expand connections"
          title="Expand connections"
          className="rounded-lg border border-slate-200 p-1.5 text-slate-500 transition hover:border-slate-300 hover:text-slate-800"
        >
          <PanelIcon />
        </button>
        <div className="my-1 h-px w-6 bg-slate-200" aria-hidden="true" />
        {MCP_APPS.map((app) => (
          <button
            key={app.id}
            type="button"
            onClick={() => pick(app.id)}
            title={
              app.id === "github" && github?.connected
                ? `GitHub · connected as @${github.account}`
                : app.name
            }
            aria-label={`Open ${app.name}`}
            className="relative rounded-xl p-1 transition hover:bg-slate-50"
          >
            <AppIcon app={app} size="sm" />
            {app.id === "github" && github?.connected && (
              <span
                className="absolute right-0.5 top-0.5 h-2 w-2 rounded-full bg-emerald-500 ring-2 ring-white"
                aria-hidden="true"
              />
            )}
          </button>
        ))}
      </div>
    </aside>
  );
}

function PanelIcon() {
  return (
    <svg
      className="h-4 w-4"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M9 4v16" />
    </svg>
  );
}

function AppIcon({ app, size }: { app: McpApp; size: "sm" | "lg" }) {
  const box = size === "lg" ? "h-10 w-10" : "h-7 w-7";
  const glyph = size === "lg" ? "h-5 w-5" : "h-3.5 w-3.5";
  return (
    <span
      className={`flex shrink-0 items-center justify-center rounded-lg ${box} ${app.tint}`}
      aria-hidden="true"
    >
      <svg
        className={glyph}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d={app.icon} />
      </svg>
    </span>
  );
}

function AppDetail({
  app,
  onBack,
  github,
  statusUnavailable,
}: {
  app: McpApp;
  onBack: () => void;
  github: GitHubConnection | null;
  statusUnavailable: boolean;
}) {
  const isGithub = app.id === "github";
  const tools: ConnectionTool[] =
    isGithub && github?.tools?.length
      ? github.tools
      : app.tools.map((tool) => ({
          name: tool.name,
          description: tool.description,
          write: Boolean(tool.write),
          available: !tool.write,
        }));

  const status = isGithub && github?.connected
    ? {
        label: "Connected",
        className: "border-emerald-200 bg-emerald-50 text-emerald-700",
      }
    : isGithub && github?.error
      ? {
          label: "Error",
          className: "border-amber-200 bg-amber-50 text-amber-700",
        }
      : {
          label: statusLabel(app.status),
          className: statusBadgeClass(app.status),
        };

  return (
    <div className="flex min-h-0 flex-1 flex-col px-4 pb-3 pt-3">
      <button
        type="button"
        onClick={onBack}
        className="self-start text-[11px] font-medium text-slate-500 transition hover:text-slate-800"
      >
        ← All apps
      </button>

      <div className="mt-3 flex items-center gap-3">
        <AppIcon app={app} size="lg" />
        <div className="min-w-0">
          <p className="truncate text-sm font-bold text-slate-900">{app.name}</p>
          <p className="truncate text-[11px] text-slate-500">
            {isGithub && github?.connected ? `@${github.account}` : app.tagline}
          </p>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <span
          className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${status.className}`}
        >
          {status.label}
        </span>
        <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[10px] font-medium text-slate-500">
          {app.auth === "oauth" ? "OAuth" : "API key"}
        </span>
      </div>

      {isGithub && github?.default_repo && (
        <p className="mt-2 text-[10px] text-slate-500">
          Default repo:{" "}
          <span className="font-mono font-semibold text-slate-700">
            {github.default_repo}
          </span>
        </p>
      )}

      <div className="mt-4 min-h-0 flex-1 overflow-y-auto">
        <p className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
          Tools
        </p>
        <ul className="mt-1.5 space-y-1.5">
          {tools.map((tool) => (
            <li
              key={tool.name}
              className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5"
            >
              <p className="font-mono text-[10px] font-semibold text-slate-800">
                {tool.name}
                {tool.write ? (
                  <span
                    className="ml-1.5 rounded bg-amber-100 px-1 py-0.5 font-sans text-[9px] font-semibold text-amber-700"
                    title="Changes data — needs explicit approval when enabled"
                  >
                    ⚠ needs approval
                  </span>
                ) : isGithub && github?.connected && tool.available ? (
                  <span
                    className="ml-1.5 rounded bg-emerald-100 px-1 py-0.5 font-sans text-[9px] font-semibold text-emerald-700"
                    title="Callable now from the Ask tab"
                  >
                    live
                  </span>
                ) : null}
              </p>
              <p className="mt-0.5 text-[10px] leading-snug text-slate-500">
                {tool.description}
              </p>
            </li>
          ))}
        </ul>
      </div>

      <div className="mt-3 border-t border-slate-100 pt-3">
        {isGithub ? (
          github?.connected ? (
            <>
              <button
                type="button"
                disabled
                className="w-full cursor-default rounded-lg bg-emerald-600 px-3 py-2 text-[11px] font-bold text-white"
              >
                ✓ Connected as @{github.account}
              </button>
              <p className="mt-1.5 text-[10px] leading-snug text-slate-500">
                Using the server-side GITHUB_PAT. Read-only in Ask — write
                actions need the approval flow.
              </p>
            </>
          ) : github?.configured ? (
            <>
              <button
                type="button"
                disabled
                className="w-full cursor-not-allowed rounded-lg bg-amber-500 px-3 py-2 text-[11px] font-bold text-white opacity-70"
              >
                Reconnect needed
              </button>
              <p className="mt-1.5 text-[10px] leading-snug text-amber-700">
                {github.error ?? "GitHub did not accept the token."}
              </p>
            </>
          ) : (
            <>
              <button
                type="button"
                disabled
                title={
                  statusUnavailable
                    ? "Connection status unavailable — is the backend running?"
                    : "Add GITHUB_PAT to the backend .env, then restart the backend"
                }
                className="w-full cursor-not-allowed rounded-lg bg-indigo-600 px-3 py-2 text-[11px] font-bold text-white opacity-50"
              >
                Connect
              </button>
              <p className="mt-1.5 text-[10px] leading-snug text-slate-400">
                {statusUnavailable
                  ? "Connection status unavailable — start the backend, then refresh this page."
                  : "Add GITHUB_PAT to the backend .env, then restart the backend."}
              </p>
            </>
          )
        ) : (
          <>
            <button
              type="button"
              disabled
              title="This app is not wired to a backend yet"
              className="w-full cursor-not-allowed rounded-lg bg-indigo-600 px-3 py-2 text-[11px] font-bold text-white opacity-50"
            >
              Connect
            </button>
            <p className="mt-1.5 text-[10px] leading-snug text-slate-400">
              Preview only — this app is not wired to a backend yet.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
