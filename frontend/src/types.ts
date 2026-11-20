export type RequestSpec = {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: unknown;
  timeout_s: number;
  expected_status?: number | null;
  question: string;
  approved: boolean;
};

export type Observation = {
  request_id: string;
  status: number | null;
  headers: Record<string, string>;
  body_preview: string;
  latency_ms: number;
  error_type: string | null;
  error_message: string | null;
  redirect_location: string | null;
  truncated?: boolean;
};

export type Citation = {
  chunk_id: string;
  source_title: string;
  source_url: string;
  section?: string;
  page?: number;
};

export type AskMessage = {
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  provider?: string;
  insufficient?: boolean;
  /** Set when the router answered this message via a live tool (decode/epoch/dns/ip/time). */
  tool?: string;
  /** Short chip text built by the backend from the tool result. */
  tool_label?: string;
};

/** One persisted Ask turn (question + answer) from /api/ask/history. */
export type AskTurnSummary = {
  turn_id: string;
  created_at: string;
  question: string;
  answer: string;
  provider: string | null;
  tool: string | null;
  tool_label: string | null;
  citations: Citation[];
};

/** One tool a backend connector reports (GET /api/connections). */
export type ConnectionTool = {
  name: string;
  description: string;
  /** true → changes data and needs explicit approval. */
  write: boolean;
  /** false → planned but not callable yet. */
  available: boolean;
  /** Google only: which app the tool belongs to. */
  app?: "calendar" | "gmail";
};

/**
 * GitHub connection state. Honest by construction:
 * configured = a token exists, connected = GitHub accepted it.
 */
export type GitHubConnection = {
  configured: boolean;
  connected: boolean;
  account: string | null;
  name: string | null;
  avatar_url: string | null;
  default_repo: string | null;
  source: string;
  error: string | null;
  tools: ConnectionTool[];
};

/**
 * Google connection state (Calendar + Gmail share one OAuth grant).
 * connected = the backend holds an encrypted grant for this user.
 */
export type GoogleConnection = {
  configured: boolean;
  connected: boolean;
  account: string | null;
  expires_at: string | null;
  error: string | null;
  tools: ConnectionTool[];
  apps: string[];
  scopes: string[];
};

export type ConnectionsResponse = {
  github: GitHubConnection;
  google: GoogleConnection;
};

export type Patch = {
  set_headers: Record<string, string>;
  remove_headers: string[];
  set_body: unknown;
  set_url: string | null;
};

export type RetestResult = {
  patched_request: Record<string, unknown>;
  observation: Observation;
  diff: {
    status_before: number | null;
    status_after: number | null;
    latency_before_ms: number;
    latency_after_ms: number;
    body_changed: boolean;
    verdict: string;
  };
};

export type ScenarioPreset = {
  id: string;
  collection: string;
  name: string;
  statusBadge: number;
  method: string;
  path: string;
  spec: RequestSpec;
  sampleObservation?: Observation;
  sampleDiagnosis?: {
    whatWentWrong: string;
    suggestedFix: string;
    patch: Patch;
    sources: { id: string; label: string; url?: string }[];
  };
};

export type RunSummary = {
  run_id: string;
  created_at: string;
  method: string;
  url: string;
  status: number | null;
  provider: string | null;
};
