/**
 * Static catalog of MCP apps shown in the Ask tab's Connections sidebar.
 *
 * The catalog only supplies identity (name, icon, tagline) and a fallback
 * tool list. Live state — connected/error, real tools — comes from
 * GET /api/connections, so nothing here claims a connection that does not
 * exist.
 *
 * Statuses:
 *   "preview"     → wired to a backend connector (GitHub, Google);
 *   "coming_soon" → no integration exists at all.
 */

export type McpTool = {
  name: string;
  description: string;
  /** true → changes data; will need explicit approval, like POST/PATCH. */
  write?: boolean;
};

export type McpApp = {
  id: string;
  name: string;
  tagline: string;
  category: "developer" | "productivity" | "communication";
  auth: "oauth" | "api_key";
  status: "preview" | "coming_soon";
  /** Single stroke path drawn in a 24×24 viewBox (same style as Header icons). */
  icon: string;
  /** Tailwind classes for the app's icon tile. */
  tint: string;
  tools: McpTool[];
};

export const MCP_APPS: McpApp[] = [
  {
    id: "github",
    name: "GitHub",
    tagline: "Issues and pull requests",
    category: "developer",
    auth: "oauth",
    status: "preview",
    tint: "bg-slate-100 text-slate-800",
    icon: "M9 19c-4.3 1.4-4.3-2.5-6-3m12 5v-3.5c0-1 .1-1.4-.5-2 2.8-.3 5.5-1.4 5.5-6a4.6 4.6 0 0 0-1.3-3.2 4.2 4.2 0 0 0-.1-3.2s-1.1-.3-3.5 1.3a12.3 12.3 0 0 0-6.2 0C6.5 2.8 5.4 3.1 5.4 3.1a4.2 4.2 0 0 0-.1 3.2A4.6 4.6 0 0 0 4 9.5c0 4.6 2.7 5.7 5.5 6-.6.6-.6 1.2-.5 2V21",
    tools: [
      { name: "search_issues", description: "Find issues by repo, label, or text" },
      { name: "get_pull_request", description: "Read a PR's status, checks, and diff" },
      { name: "create_issue", description: "Open a new issue", write: true },
      { name: "comment_on_issue", description: "Post a comment on an issue", write: true },
    ],
  },
  {
    id: "google-calendar",
    name: "Google Calendar",
    tagline: "Events and availability",
    category: "productivity",
    auth: "oauth",
    status: "preview",
    tint: "bg-blue-50 text-blue-700",
    icon: "M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z",
    tools: [
      { name: "list_events", description: "List events in a date range" },
      { name: "search_events", description: "Search events by title or attendee" },
      { name: "create_event", description: "Schedule a new event", write: true },
    ],
  },
  {
    id: "gmail",
    name: "Gmail",
    tagline: "Mail and threads",
    category: "communication",
    auth: "oauth",
    status: "preview",
    tint: "bg-rose-50 text-rose-700",
    icon: "M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM22 7l-10 6L2 7",
    tools: [
      { name: "search_threads", description: "Search mail with Gmail query syntax" },
      { name: "get_message", description: "Read one message" },
      { name: "create_draft", description: "Create a draft email", write: true },
    ],
  },
  {
    id: "slack",
    name: "Slack",
    tagline: "Channels and DMs",
    category: "communication",
    auth: "oauth",
    status: "coming_soon",
    tint: "bg-violet-50 text-violet-700",
    icon: "M21 11.5a8.4 8.4 0 0 1-9 8.4 8.5 8.5 0 0 1-3.9-.9L3 20l1-4.1a8.4 8.4 0 0 1-.9-3.9 8.5 8.5 0 0 1 8.5-8.5 8.4 8.4 0 0 1 8.4 8.5z",
    tools: [
      { name: "search_messages", description: "Search channel history" },
      { name: "list_channels", description: "List channels you can see" },
      { name: "post_message", description: "Send a channel message", write: true },
    ],
  },
  {
    id: "notion",
    name: "Notion",
    tagline: "Pages and docs",
    category: "productivity",
    auth: "oauth",
    status: "coming_soon",
    tint: "bg-slate-100 text-slate-800",
    icon: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6",
    tools: [
      { name: "search_pages", description: "Find pages by title or content" },
      { name: "read_page", description: "Read a page's blocks" },
      { name: "create_page", description: "Create a page in a database", write: true },
    ],
  },
  {
    id: "linear",
    name: "Linear",
    tagline: "Issues and projects",
    category: "developer",
    auth: "oauth",
    status: "coming_soon",
    tint: "bg-indigo-50 text-indigo-700",
    icon: "m12 2 9 5-9 5-9-5 9-5zM3 12l9 5 9-5M3 17l9 5 9-5",
    tools: [
      { name: "list_issues", description: "List issues by team or state" },
      { name: "get_issue", description: "Read one issue's details" },
      { name: "create_issue", description: "Open a new issue", write: true },
    ],
  },
];

/** "Preview" / "Coming soon" badge classes, kept in one place. */
export function statusBadgeClass(status: McpApp["status"]): string {
  return status === "preview"
    ? "border-indigo-200 bg-indigo-50 text-indigo-700"
    : "border-slate-200 bg-slate-50 text-slate-500";
}

export function statusLabel(status: McpApp["status"]): string {
  return status === "preview" ? "Preview" : "Coming soon";
}
