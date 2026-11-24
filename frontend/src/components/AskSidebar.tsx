import { useEffect, useState } from "react";
import { MCP_APPS, statusBadgeClass, statusLabel } from "../data/mcpApps";
import type { McpApp } from "../data/mcpApps";
import type {
  ConnectionTool,
  ConnectionsResponse,
  GitHubConnection,
  GoogleConnection,
  SentryConnection,
} from "../types";

/**
 * ChatGPT-style Connections sidebar for the Ask tab.
 *
 * Desktop: an in-flow column that collapses to a 64px icon rail.
 * Mobile:  a slide-in drawer with a dimmed backdrop (closed by the
 *          backdrop, the toggle, or Escape).
 *
 * Honest by design: badges and buttons reflect the real state reported by
 * GET /api/connections. Nothing is shown as "Connected" unless the backend
 * verified it with the provider, and nothing pretends to be wired when it
 * is not.
 */
export function AskSidebar({
  open,
  onToggle,
  onClose,
  connections,
  connectionsError,
  onConnectGoogle,
  onDisconnectGoogle,
  googleBusy,
}: {
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  connections: ConnectionsResponse | null;
  connectionsError: string | null;
  onConnectGoogle: () => void;
  onDisconnectGoogle: () => void;
  googleBusy: boolean;
}) {
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const github = connections?.github ?? null;
  const google = connections?.google ?? null;
  const sentry = connections?.sentry ?? null;
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

  function isGoogleApp(app: McpApp): boolean {
    return app.id === "google-calendar" || app.id === "gmail";
  }

  function isConnected(app: McpApp): boolean {
    if (app.id === "github") return Boolean(github?.connected);
    if (app.id === "sentry") return Boolean(sentry?.connected);
    if (isGoogleApp(app)) return Boolean(google?.connected);
    return false;
  }

  function providerError(app: McpApp): string | null {
    if (app.id === "github") return github?.error ?? null;
    if (app.id === "sentry") return sentry?.error ?? null;
    if (isGoogleApp(app)) return google?.error ?? null;
    return null;
  }

  function badgeFor(app: McpApp): { label: string; className: string } {
    if (isConnected(app)) {
      return {
        label: "Connected",
        className: "border-emerald-200 bg-emerald-50 text-emerald-700",
      };
    }
    if (providerError(app)) {
      return {
        label: "Error",
        className: "border-amber-200 bg-amber-50 text-amber-700",
      };
    }
    return { label: statusLabel(app.status), className: statusBadgeClass(app.status) };
  }

  function accountFor(app: McpApp): string | null {
    if (app.id === "github") return github?.account ? `@${github.account}` : null;
    if (app.id === "sentry")
      return sentry?.org && sentry?.project
        ? `${sentry.org}/${sentry.project}`
        : null;
    if (isGoogleApp(app)) return google?.account ?? null;
    return null;
  }

  const connectedApps = filtered.filter((app) => isConnected(app));

  function emptyConnectedCopy(): string {
    if (statusUnavailable) return "Connection status unavailable — is the backend running?";
    if (github?.error || google?.error || sentry?.error) {
      return "A connection needs attention. Open the app for details.";
    }
    if (github?.configured || google?.configured || sentry?.configured) {
      return "No apps connected yet. Open an app below to connect it.";
    }
    return "No apps connected yet. Add GITHUB_PAT, Sentry, or Google OAuth credentials to the backend .env.";
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
            google={google}
            sentry={sentry}
            statusUnavailable={statusUnavailable}
            onConnectGoogle={onConnectGoogle}
            onDisconnectGoogle={onDisconnectGoogle}
            googleBusy={googleBusy}
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
              {connectedApps.length === 0 ? (
                <p className="mt-1.5 rounded-xl border border-dashed border-slate-200 px-2.5 py-2 text-[11px] leading-snug text-slate-500">
                  {emptyConnectedCopy()}
                </p>
              ) : (
                <ul className="mt-1.5 space-y-1">
                  {connectedApps.map((app) => (
                    <li key={app.id}>
                      <button
                        type="button"
                        onClick={() => setSelectedId(app.id)}
                        className="flex w-full items-center gap-2.5 rounded-xl border border-emerald-100 bg-emerald-50/50 px-2 py-2 text-left transition-colors hover:bg-emerald-50"
                      >
                        <AppIcon app={app} size="sm" />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[11px] font-semibold text-slate-800">
                            {app.name}
                          </span>
                          <span className="block truncate text-[10px] text-slate-500">
                            {accountFor(app) ?? app.tagline}
                          </span>
                        </span>
                        <span className="shrink-0 rounded-full border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 text-[9px] font-semibold text-emerald-700">
                          Connected
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
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
                    .filter((app) => !isConnected(app))
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
                                {app.tagline}
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
                : github?.connected || google?.connected || sentry?.connected
                  ? "Read-only tools only. Write actions need explicit approval."
                  : github?.configured || google?.configured || sentry?.configured
                    ? "Open an app to connect it. Credentials are configured on the backend."
                    : "Preview — add provider credentials to the backend .env."}
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
              isConnected(app)
                ? `${app.name} · connected${accountFor(app) ? ` as ${accountFor(app)}` : ""}`
                : app.name
            }
            aria-label={`Open ${app.name}`}
            className="relative rounded-xl p-1 transition hover:bg-slate-50"
          >
            <AppIcon app={app} size="sm" />
            {isConnected(app) && (
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
  google,
  sentry,
  statusUnavailable,
  onConnectGoogle,
  onDisconnectGoogle,
  googleBusy,
}: {
  app: McpApp;
  onBack: () => void;
  github: GitHubConnection | null;
  google: GoogleConnection | null;
  sentry: SentryConnection | null;
  statusUnavailable: boolean;
  onConnectGoogle: () => void;
  onDisconnectGoogle: () => void;
  googleBusy: boolean;
}) {
  const isGithub = app.id === "github";
  const isSentry = app.id === "sentry";
  const googleApp = app.id === "google-calendar" ? "calendar" : app.id === "gmail" ? "gmail" : null;
  const isGoogle = googleApp !== null;

  // One connection object per app keeps the rest of the panel honest and short.
  const connection: GitHubConnection | GoogleConnection | SentryConnection | null =
    isGithub ? github : isGoogle ? google : isSentry ? sentry : null;

  const tools: ConnectionTool[] =
    connection?.tools?.length
      ? isGoogle
        ? connection.tools.filter((tool) => tool.app === googleApp)
        : connection.tools
      : app.tools.map((tool) => ({
          name: tool.name,
          description: tool.description,
          write: Boolean(tool.write),
          available: !tool.write,
        }));

  const connected = Boolean(connection?.connected);
  const providerError = connection?.error ?? null;
  const rawAccount = connection?.account;
  // GitHub accounts read as @handle everywhere in the UI.
  const account = rawAccount ? (isGithub ? `@${rawAccount}` : rawAccount) : null;
  const configured = Boolean(connection?.configured);

  const status = connected
    ? {
        label: "Connected",
        className: "border-emerald-200 bg-emerald-50 text-emerald-700",
      }
    : providerError
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
            {connected && account ? account : app.tagline}
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

      {isSentry && sentry?.org && sentry?.project && (
        <p className="mt-2 text-[10px] text-slate-500">
          Project:{" "}
          <span className="font-mono font-semibold text-slate-700">
            {sentry.org}/{sentry.project}
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
                ) : connected && tool.available ? (
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
        {connected ? (
          <>
            <button
              type="button"
              disabled
              className="w-full cursor-default rounded-lg bg-emerald-600 px-3 py-2 text-[11px] font-bold text-white"
            >
              ✓ Connected{account ? ` as ${account}` : ""}
            </button>
            <p className="mt-1.5 text-[10px] leading-snug text-slate-500">
              {isGoogle
                ? "Google tokens are stored encrypted. Read-only in Ask — write actions need the approval flow."
                : isSentry
                  ? "Using the server-side SENTRY_AUTH_TOKEN. Read-only: issues and events only."
                  : "Using the server-side GITHUB_PAT. Read-only in Ask — write actions need the approval flow."}
            </p>
            {isGoogle && (
              <button
                type="button"
                onClick={onDisconnectGoogle}
                disabled={googleBusy}
                className="mt-1.5 w-full rounded-lg border border-slate-200 px-3 py-1.5 text-[10px] font-semibold text-slate-500 transition hover:border-rose-200 hover:text-rose-600 disabled:opacity-50"
              >
                Disconnect Google
              </button>
            )}
          </>
        ) : providerError ? (
          <>
            <button
              type="button"
              onClick={isGoogle ? onConnectGoogle : undefined}
              disabled={!isGoogle || googleBusy}
              className="w-full cursor-not-allowed rounded-lg bg-amber-500 px-3 py-2 text-[11px] font-bold text-white opacity-70"
            >
              {isGoogle ? "Reconnect with Google" : isSentry ? "Check Sentry settings" : "Reconnect needed"}
            </button>
            <p className="mt-1.5 text-[10px] leading-snug text-amber-700">
              {providerError}
            </p>
          </>
        ) : isGoogle ? (
          <>
            <button
              type="button"
              onClick={onConnectGoogle}
              disabled={!configured || googleBusy || statusUnavailable}
              title={
                statusUnavailable
                  ? "Connection status unavailable — is the backend running?"
                  : configured
                    ? "Open Google's consent screen"
                    : "Add GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and MCP_TOKEN_ENCRYPTION_KEY to the backend .env"
              }
              className="w-full rounded-lg bg-indigo-600 px-3 py-2 text-[11px] font-bold text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {googleBusy ? "Opening Google…" : "Connect with Google"}
            </button>
            <p className="mt-1.5 text-[10px] leading-snug text-slate-400">
              {statusUnavailable
                ? "Connection status unavailable — start the backend, then refresh this page."
                : configured
                  ? "Read-only access to Calendar and Gmail. You can disconnect at any time."
                  : "Google OAuth is not configured on the backend yet."}
            </p>
          </>
        ) : isSentry ? (
          <>
            <button
              type="button"
              disabled
              title={
                statusUnavailable
                  ? "Connection status unavailable — is the backend running?"
                  : configured
                    ? "Sentry is configured but not verified yet. Refresh in a moment."
                    : "Add SENTRY_AUTH_TOKEN, SENTRY_ORG and SENTRY_PROJECT to the backend .env, then restart the backend"
              }
              className="w-full cursor-not-allowed rounded-lg bg-purple-600 px-3 py-2 text-[11px] font-bold text-white opacity-50"
            >
              {configured ? "Checking Sentry…" : "Needs setup"}
            </button>
            <p className="mt-1.5 text-[10px] leading-snug text-slate-400">
              {statusUnavailable
                ? "Connection status unavailable — start the backend, then refresh this page."
                : configured
                  ? "Sentry is configured but has not been verified yet."
                  : "Add SENTRY_AUTH_TOKEN, SENTRY_ORG and SENTRY_PROJECT to the backend .env, then restart the backend."}
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
        )}
      </div>
    </div>
  );
}
