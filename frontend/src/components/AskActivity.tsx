import { MCP_APPS } from "../data/mcpApps";
import type { McpApp } from "../data/mcpApps";

/**
 * What the assistant is doing right now, in ChatGPT's style.
 *
 * The Ask stream only knows the tool once the router has decided, so the
 * indicator starts as "Thinking" and switches to the real activity ("Checking
 * your Gmail", "Reading your calendar") the moment the backend reports it.
 * Nothing is guessed: the title comes from the tool that actually ran.
 */

const GENERIC_PATH =
  "M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z";
const BOOK_PATH =
  "M4 19.5A2.5 2.5 0 0 1 6.5 17H20M4 19.5A2.5 2.5 0 0 1 6.5 22H20V2H6.5A2.5 2.5 0 0 0 4 4.5z";

export type ActivityMode = "thinking" | "docs" | "tool";

export type Activity = {
  app: McpApp | null;
  /** Verb phrase shown next to the icon. */
  title: string;
  /** Tool-specific detail (query, range) without the app prefix. */
  detail: string | null;
};

function appForTool(tool: string): string | null {
  if (tool.startsWith("gmail")) return "gmail";
  if (tool.startsWith("gcal")) return "google-calendar";
  if (tool.startsWith("github")) return "github";
  return null;
}

function titleFor(appId: string | null): string {
  switch (appId) {
    case "gmail":
      return "Checking your Gmail";
    case "google-calendar":
      return "Reading your calendar";
    case "github":
      return "Looking at GitHub";
    default:
      return "Running a live helper";
  }
}

export function activityFor(
  tool: string | null,
  label: string | null
): Activity | null {
  if (!tool) return null;
  const appId = appForTool(tool);
  const app = appId
    ? MCP_APPS.find((candidate) => candidate.id === appId) ?? null
    : null;
  // Labels read like "gmail · newer_than:1d in:inbox"; the app name is
  // already in the title, so keep only the part after the separator.
  const detail = label
    ? label.includes(" · ")
      ? label.split(" · ").slice(1).join(" · ")
      : label
    : null;
  return { app, title: titleFor(appId), detail };
}

export function AskActivityIndicator({
  tool,
  label,
  mode = "thinking",
}: {
  tool: string | null;
  label: string | null;
  mode?: ActivityMode;
}) {
  const activity = activityFor(tool, label);
  const path =
    activity?.app?.icon ?? (mode === "docs" ? BOOK_PATH : GENERIC_PATH);
  const tint =
    activity?.app?.tint ??
    (mode === "docs" ? "bg-teal-50 text-teal-700" : "bg-slate-100 text-slate-600");
  const title =
    activity?.title ??
    (mode === "docs" ? "Searching the documentation" : "Thinking");

  return (
    <span
      role="status"
      aria-live="polite"
      className="inline-flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-slate-500"
    >
      <span
        className={`flex h-5 w-5 shrink-0 animate-pulse items-center justify-center rounded-md ${tint}`}
        aria-hidden="true"
      >
        <svg
          className="h-3 w-3"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d={path} />
        </svg>
      </span>
      <span className="font-medium text-slate-600">
        {title}
      </span>
      {activity?.detail && (
        <span className="max-w-[18rem] truncate font-mono text-[10px] text-slate-400">
          {activity.detail}
        </span>
      )}
      <span className="flex items-center gap-0.5" aria-hidden="true">
        <span className="h-1 w-1 animate-bounce rounded-full bg-slate-400 [animation-delay:-0.3s]" />
        <span className="h-1 w-1 animate-bounce rounded-full bg-slate-400 [animation-delay:-0.15s]" />
        <span className="h-1 w-1 animate-bounce rounded-full bg-slate-400" />
      </span>
    </span>
  );
}

/** The badge above a finished answer that came from a live tool. */
export function LiveToolBadge({
  tool,
  label,
}: {
  tool: string;
  label: string | null;
}) {
  const activity = activityFor(tool, label);
  return (
    <div className="mb-2">
      <span className="inline-flex items-center gap-1 rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-[10px] font-semibold text-violet-700">
        {activity?.app ? (
          <svg
            className="h-3 w-3"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d={activity.app.icon} />
          </svg>
        ) : (
          <span aria-hidden="true">⚡</span>
        )}
        Live tool · {label || tool}
      </span>
      <p className="mt-1 text-[10px] leading-snug text-violet-500">
        Computed now by a real function — not taken from the documentation.
      </p>
    </div>
  );
}
