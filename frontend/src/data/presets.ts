import type { ScenarioPreset } from "../types";

export const PRESETS: ScenarioPreset[] = [
  {
    id: "gh-issues-422",
    collection: "github",
    name: "POST /repos/.../issues",
    statusBadge: 422,
    method: "POST",
    path: "/repos/.../issues",
    spec: {
      method: "POST",
      url: "https://api.github.com/repos/octocat/hello-world/issues",
      headers: {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer ghp_sampletoken...",
        "Content-Type": "application/json",
      },
      body: {
        body: "Steps to reproduce the crash",
      },
      timeout_s: 5,
      expected_status: 201,
      question: "Why does this request return 422?",
      approved: true,
    },
    sampleObservation: {
      request_id: "obs-422-octocat",
      status: 422,
      headers: {
        "content-type": "application/json; charset=utf-8",
        "x-github-media-type": "github.v3; format=json",
      },
      body_preview: JSON.stringify(
        {
          message: "Validation Failed",
          errors: [
            {
              resource: "Issue",
              code: "missing_field",
              field: "title",
            },
          ],
          documentation_url: "https://docs.github.com/rest/issues/issues#create-an-issue",
        },
        null,
        2
      ),
      latency_ms: 412,
      error_type: null,
      error_message: null,
      redirect_location: null,
      size_kb: "0.3 KB",
      cors_status: "allowed",
    },
    sampleDiagnosis: {
      whatWentWrong:
        "The request is missing the required title field. The endpoint validates the body and returns 422 when a required field is absent.",
      suggestedFix: JSON.stringify(
        {
          title: "App crashes on launch",
          body: "Steps to reproduce the crash",
        },
        null,
        2
      ),
      patch: {
        set_headers: {},
        remove_headers: [],
        set_body: {
          title: "App crashes on launch",
          body: "Steps to reproduce the crash",
        },
        set_url: null,
      },
      sources: [
        {
          id: "gh-issues-create",
          label: "gh-issues-create · Create an issue",
          url: "https://docs.github.com/rest/issues/issues#create-an-issue",
        },
        {
          id: "http-422",
          label: "http-422 · Unprocessable Entity",
          url: "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/422",
        },
      ],
    },
  },
  {
    id: "local-profile-401",
    collection: "local",
    name: "GET /v1/profile",
    statusBadge: 401,
    method: "GET",
    path: "/v1/profile",
    spec: {
      method: "GET",
      url: "http://localhost:8001/v1/profile",
      headers: {
        Accept: "application/json",
      },
      body: null,
      timeout_s: 5,
      expected_status: 200,
      question: "Why does this request return 401?",
      approved: false,
    },
    sampleObservation: {
      request_id: "obs-401-mock",
      status: 401,
      headers: {
        "content-type": "application/json",
        "www-authenticate": "Bearer error=\"invalid_token\"",
      },
      body_preview: JSON.stringify(
        {
          error: "missing_credentials",
          detail: "Authorization header with Bearer scheme is required",
        },
        null,
        2
      ),
      latency_ms: 18,
      error_type: null,
      error_message: null,
      redirect_location: null,
      size_kb: "0.1 KB",
      cors_status: "allowed",
    },
    sampleDiagnosis: {
      whatWentWrong:
        "The request lacks valid authentication credentials. The endpoint expects an Authorization header with Bearer token scheme.",
      suggestedFix: JSON.stringify(
        {
          headers: {
            Authorization: "Bearer valid-user-token",
          },
        },
        null,
        2
      ),
      patch: {
        set_headers: {
          Authorization: "Bearer valid-user-token",
        },
        remove_headers: [],
        set_body: null,
        set_url: null,
      },
      sources: [
        {
          id: "KB-MDN-401",
          label: "KB-MDN-401 · 401 Unauthorized (MDN)",
          url: "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401",
        },
        {
          id: "KB-API-V1-PROFILE",
          label: "KB-API-V1-PROFILE · GET /v1/profile Auth spec",
          url: "http://localhost:8001/docs",
        },
      ],
    },
  },
  {
    id: "gh-user-200",
    collection: "github",
    name: "GET /user",
    statusBadge: 200,
    method: "GET",
    path: "/user",
    spec: {
      method: "GET",
      url: "https://api.github.com/user",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: "Bearer ghp_validToken...",
      },
      body: null,
      timeout_s: 5,
      expected_status: 200,
      question: "Inspect user details",
      approved: false,
    },
    sampleObservation: {
      request_id: "obs-200-gh",
      status: 200,
      headers: {
        "content-type": "application/json; charset=utf-8",
      },
      body_preview: JSON.stringify(
        {
          login: "octocat",
          id: 583231,
          name: "The Octocat",
          company: "@github",
          public_repos: 8,
        },
        null,
        2
      ),
      latency_ms: 195,
      error_type: null,
      error_message: null,
      redirect_location: null,
      size_kb: "1.2 KB",
      cors_status: "allowed",
    },
    sampleDiagnosis: {
      whatWentWrong: "Request succeeded with 200 OK. No failure detected.",
      suggestedFix: "No changes needed.",
      patch: {
        set_headers: {},
        remove_headers: [],
        set_body: null,
        set_url: null,
      },
      sources: [],
    },
  },
  {
    id: "gh-releases-404",
    collection: "github",
    name: "DELETE /repos/.../releases/123",
    statusBadge: 404,
    method: "DELETE",
    path: "/repos/.../releases/123",
    spec: {
      method: "DELETE",
      url: "https://api.github.com/repos/octocat/hello-world/releases/123",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: "Bearer ghp_token...",
      },
      body: null,
      timeout_s: 5,
      expected_status: 204,
      question: "Why did DELETE return 404?",
      approved: true,
    },
    sampleObservation: {
      request_id: "obs-404-gh",
      status: 404,
      headers: {
        "content-type": "application/json; charset=utf-8",
      },
      body_preview: JSON.stringify(
        {
          message: "Not Found",
          documentation_url: "https://docs.github.com/rest/releases/releases#delete-a-release",
        },
        null,
        2
      ),
      latency_ms: 220,
      error_type: null,
      error_message: null,
      redirect_location: null,
      size_kb: "0.2 KB",
      cors_status: "allowed",
    },
    sampleDiagnosis: {
      whatWentWrong:
        "The targeted release ID 123 does not exist in this repository or the authenticated account lacks access.",
      suggestedFix: "Verify the release ID with GET /repos/octocat/hello-world/releases before deleting.",
      patch: {
        set_headers: {},
        remove_headers: [],
        set_body: null,
        set_url: "https://api.github.com/repos/octocat/hello-world/releases",
      },
      sources: [
        {
          id: "http-404",
          label: "http-404 · Not Found (MDN)",
          url: "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/404",
        },
      ],
    },
  },
];

