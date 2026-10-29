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
  size_kb?: string;
  cors_status?: string;
};

export type Citation = {
  chunk_id: string;
  source_title: string;
  source_url: string;
  section?: string;
  page?: number;
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
