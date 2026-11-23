import { expect, test, type Page, type Route } from "@playwright/test";

type SseEvent = [event: string, data: unknown];

function sse(events: SseEvent[]): string {
  return events
    .map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
    .join("");
}

const diagnosis = {
  request_id: "obs-1",
  status: 401,
  headers: { "content-type": "application/json" },
  body_preview: '{"error":"token_expired"}',
  latency_ms: 12,
  error_type: null,
  error_message: null,
  redirect_location: null,
  truncated: false,
};

async function mockDiagnosis(page: Page, patch: Record<string, unknown>) {
  await page.route("**/api/diagnose", async (route: Route) => {
    await route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream", "cache-control": "no-cache" },
      body: sse([
        ["observation", diagnosis],
        [
          "retrieval",
          {
            chunks: [
              {
                chunk_id: "KB-MDN-401",
                source_title: "401 Unauthorized (MDN)",
                source_url: "https://example.test/401",
                section: "Status 401",
                page: "",
              },
            ],
            insufficient: false,
          },
        ],
        ["provider", { name: "test:model" }],
        ["token", { text: "The access token has expired. " }],
        ["token", { text: "Refresh it and retry." }],
        [
          "done",
          {
            run_id: "run-1",
            diagnosis: "The access token has expired. Refresh it and retry.",
            citations: ["KB-MDN-401"],
            invalid_citations: [],
            patch,
            provider: "test:model",
          },
        ],
      ]),
    });
  });
}

test("loads the diagnose workspace and switches to Ask API", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();

  await page.getByRole("tab", { name: "Diagnose", exact: true }).click();
  await expect(page.getByRole("heading", { name: "AI diagnosis" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Run test" })).toBeVisible();

  await page.getByRole("tab", { name: "Ask API" }).click();
  await expect(page.getByRole("heading", { name: "API assistant" })).toBeVisible();
  await expect(page.getByText("Ask a question in plain English.")).toBeVisible();

  await page.getByRole("tab", { name: "Diagnose", exact: true }).click();
  await expect(page.getByRole("heading", { name: "AI diagnosis" })).toBeVisible();
});

test("diagnose, fill redacted secret, and retest successfully", async ({ page }) => {
  await mockDiagnosis(page, {
    set_headers: { Authorization: "Bearer <REDACTED>" },
    remove_headers: [],
    set_body: null,
    set_url: null,
  });

  let retestBody: any = null;
  await page.route("**/api/retest", async (route) => {
    retestBody = route.request().postDataJSON();
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        patched_request: {},
        observation: {
          request_id: "retest-1",
          status: 200,
          headers: { "content-type": "application/json" },
          body_preview: '{"id":42,"name":"Test User"}',
          latency_ms: 9,
          error_type: null,
          error_message: null,
          redirect_location: null,
        },
        diff: {
          status_before: 401,
          status_after: 200,
          latency_before_ms: 12,
          latency_after_ms: 9,
          body_changed: true,
          verdict: "changed",
        },
      }),
    });
  });

  await page.goto("/");
  await page.getByRole("tab", { name: "Diagnose", exact: true }).click();
  await page.getByRole("button", { name: "Run test" }).click();

  await expect(
    page.getByText("The access token has expired. Refresh it and retry.")
  ).toBeVisible();
  await expect(page.getByText("This patch contains redacted secrets")).toBeVisible();

  const retest = page.getByRole("button", { name: "Apply fix and re-run" });
  await expect(retest).toBeDisabled();

  await page.getByPlaceholder("Paste the real token or secret").fill("test-token");
  await expect(retest).toBeEnabled();
  await retest.click();

  await expect(page.getByText(/Retest Verdict:\s*changed/)).toBeVisible();
  await expect(page.getByText(/Status:\s*401\s*→\s*200/)).toBeVisible();
  expect(retestBody?.patch?.set_headers?.Authorization).toBe("Bearer test-token");
});

test("a failed retest is reported instead of a fake pass", async ({ page }) => {
  await mockDiagnosis(page, {
    set_headers: { Accept: "application/json" },
    remove_headers: [],
    set_body: null,
    set_url: null,
  });

  await page.route("**/api/retest", async (route) => {
    await route.fulfill({ status: 500, body: "boom" });
  });

  await page.goto("/");
  await page.getByRole("tab", { name: "Diagnose", exact: true }).click();
  await page.getByRole("button", { name: "Run test" }).click();
  await expect(page.getByText("The access token has expired. Refresh it and retry.")).toBeVisible();

  await page.getByRole("button", { name: "Apply fix and re-run" }).click();

  await expect(page.getByText(/Retest failed: Error: Retest request failed: HTTP 500/)).toBeVisible();
  await expect(page.getByText("Verified ✓")).toHaveCount(0);
});

test("Ask API streams an answer with a citation", async ({ page }) => {
  await page.route("**/api/ask", async (route) => {
    await route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream", "cache-control": "no-cache" },
      body: sse([
        [
          "retrieval",
          {
            chunks: [
              {
                chunk_id: "KB-IDEMP",
                source_title: "Idempotency keys",
                source_url: "https://example.test/idempotency",
                section: "Idempotency",
                page: "",
              },
            ],
            insufficient: false,
          },
        ],
        ["provider", { name: "test:model" }],
        ["token", { text: "Use an Idempotency-Key header. " }],
        ["token", { text: "It prevents duplicate side effects." }],
        [
          "done",
          {
            answer: "Use an Idempotency-Key header. It prevents duplicate side effects.",
            citations: ["KB-IDEMP"],
            invalid_citations: [],
            provider: "test:model",
            insufficient: false,
          },
        ],
      ]),
    });
  });

  await page.goto("/");
  await page.getByRole("tab", { name: "Ask API" }).click();
  await page
    .getByPlaceholder(/Ask any API question/)
    .fill("How do I make POST retries safe?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();

  await expect(
    page.getByText("Use an Idempotency-Key header. It prevents duplicate side effects.")
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "KB-IDEMP" }).first()).toBeVisible();
  await expect(page.getByText("test:model")).toBeVisible();

  await page.getByRole("button", { name: "New chat" }).click();
  await expect(page.getByText("Ask a question in plain English.")).toBeVisible();
});

test("an answer can be copied and regenerated at most twice", async ({ page }) => {
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);

  await page.route("**/api/ask", async (route) => {
    await route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream", "cache-control": "no-cache" },
      body: sse([
        ["provider", { name: "test:model" }],
        ["token", { text: "Use an Idempotency-Key header." }],
        [
          "done",
          {
            answer: "Use an Idempotency-Key header.",
            citations: [],
            invalid_citations: [],
            provider: "test:model",
            insufficient: false,
          },
        ],
      ]),
    });
  });

  await page.goto("/");
  await page.getByRole("tab", { name: "Ask API" }).click();
  await page
    .getByPlaceholder(/Ask any API question/)
    .fill("How do I make POST retries safe?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();

  const answer = page.getByText("Use an Idempotency-Key header.");
  await expect(answer).toBeVisible();

  // Copy writes the answer to the clipboard and confirms it.
  await page.getByRole("button", { name: "Copy answer" }).click();
  await expect(page.getByText("Copied")).toBeVisible();
  const clipboard = await page.evaluate(() => navigator.clipboard.readText());
  expect(clipboard).toBe("Use an Idempotency-Key header.");

  // Regenerate re-asks the same question in place, twice at most.
  const regenerate = page.getByRole("button", { name: "Regenerate answer" });
  await expect(regenerate).toBeEnabled();
  await regenerate.click();
  await expect(page.getByText("1/2")).toBeVisible();
  await expect(answer).toBeVisible();

  await regenerate.click();
  await expect(page.getByText("2/2")).toBeVisible();
  await expect(regenerate).toBeDisabled();
  await expect(page.getByText("Use an Idempotency-Key header.")).toBeVisible();
});

test("a sent question can be copied and edited", async ({ page }) => {
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);

  await page.route("**/api/ask", async (route) => {
    await route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream", "cache-control": "no-cache" },
      body: sse([
        ["provider", { name: "test:model" }],
        ["token", { text: "Use an Idempotency-Key header." }],
        [
          "done",
          {
            answer: "Use an Idempotency-Key header.",
            citations: [],
            invalid_citations: [],
            provider: "test:model",
            insufficient: false,
          },
        ],
      ]),
    });
  });

  await page.goto("/");
  await page.getByRole("tab", { name: "Ask API" }).click();
  await page
    .getByPlaceholder(/Ask any API question/)
    .fill("What is idempotency?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByText("Use an Idempotency-Key header.")).toBeVisible();

  // Copy the sent question itself.
  await page.getByRole("button", { name: "Copy message" }).click();
  const clipboard = await page.evaluate(() => navigator.clipboard.readText());
  expect(clipboard).toBe("What is idempotency?");

  // Pencil opens an inline editor; saving drops the old turn and re-asks.
  await page.getByRole("button", { name: "Edit message" }).click();
  const editor = page.getByLabel("Edit question");
  await expect(editor).toBeVisible();
  await expect(editor).toHaveValue("What is idempotency?");
  await editor.fill("What is a PUT request?");
  await page.getByRole("button", { name: "Save & resend" }).click();

  await expect(page.getByText("What is a PUT request?")).toBeVisible();
  await expect(page.getByText("What is idempotency?")).toHaveCount(0);
  await expect(page.getByText("Use an Idempotency-Key header.")).toBeVisible();
});

test("connections sidebar lists MCP apps and stays honest about preview", async ({
  page,
}) => {
  await page.route("**/api/connections", (route) =>
    route.fulfill({
      status: 200,
      json: {
        github: {
          configured: false,
          connected: false,
          account: null,
          name: null,
          avatar_url: null,
          default_repo: null,
          source: "personal_access_token",
          error: null,
          tools: [],
        },
        google: {
          configured: false,
          connected: false,
          account: null,
          expires_at: null,
          error: null,
          tools: [],
          apps: ["calendar", "gmail"],
          scopes: [],
        },
      },
    })
  );

  await page.goto("/");
  await page.getByRole("tab", { name: "Ask API" }).click();

  // Open by default with the catalog visible.
  await expect(page.getByRole("heading", { name: "Connections" })).toBeVisible();
  await expect(page.getByRole("button", { name: /GitHub/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Google Calendar/ })).toBeVisible();
  await expect(page.getByText(/No apps connected yet/)).toBeVisible();

  // Collapse → title hidden, expand → title back (state is real, not cosmetic).
  await page.getByRole("button", { name: "Collapse connections" }).click();
  await expect(page.getByRole("heading", { name: "Connections" })).toBeHidden();
  await page.getByRole("button", { name: "Expand connections" }).click();
  await expect(page.getByRole("heading", { name: "Connections" })).toBeVisible();

  // App detail: real tool list, write tools flagged, Connect disabled.
  await page.getByRole("button", { name: /GitHub/ }).click();
  await expect(page.getByText("search_issues")).toBeVisible();
  await expect(page.getByText("create_issue")).toBeVisible();
  await expect(page.getByText("⚠ needs approval").first()).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Connect", exact: true })
  ).toBeDisabled();
  await expect(
    page.getByText(/Add GITHUB_PAT to the backend \.env/)
  ).toBeVisible();

  // Back returns to the list.
  await page.getByRole("button", { name: "← All apps" }).click();
  await expect(page.getByRole("button", { name: /Google Calendar/ })).toBeVisible();
});

test("connections sidebar shows a verified GitHub connection", async ({ page }) => {
  await page.route("**/api/connections", (route) =>
    route.fulfill({
      status: 200,
      json: {
        github: {
          configured: true,
          connected: true,
          account: "test-user",
          name: "Test User",
          avatar_url: null,
          default_repo: "octocat/Hello-World",
          source: "personal_access_token",
          error: null,
          tools: [
            {
              name: "list_issues",
              description: "List issues in a repository",
              write: false,
              available: true,
            },
            {
              name: "create_issue",
              description: "Open a new issue",
              write: true,
              available: false,
            },
          ],
        },
      },
    })
  );

  await page.goto("/");
  await page.getByRole("tab", { name: "Ask API" }).click();

  // Real state: connected account appears in both sections.
  await expect(page.getByText("@test-user").first()).toBeVisible();
  await expect(page.getByText("Connected").first()).toBeVisible();

  await page.getByRole("button", { name: /GitHub/ }).first().click();
  await expect(
    page.getByRole("button", { name: /Connected as @test-user/ })
  ).toBeDisabled();
  await expect(page.getByText("list_issues")).toBeVisible();
  await expect(page.getByText("live").first()).toBeVisible();
  await expect(page.getByText("create_issue")).toBeVisible();
  await expect(page.getByText("⚠ needs approval").first()).toBeVisible();
  await expect(page.getByText("Default repo:")).toBeVisible();
  await expect(page.getByText("octocat/Hello-World")).toBeVisible();
});

test("connections sidebar shows a connected Google account", async ({ page }) => {
  await page.route("**/api/connections", (route) =>
    route.fulfill({
      status: 200,
      json: {
        github: {
          configured: true,
          connected: false,
          account: null,
          name: null,
          avatar_url: null,
          default_repo: null,
          source: "personal_access_token",
          error: null,
          tools: [],
        },
        google: {
          configured: true,
          connected: true,
          account: "me@example.com",
          expires_at: "2026-11-20T12:00:00+00:00",
          error: null,
          apps: ["calendar", "gmail"],
          scopes: [],
          tools: [
            {
              app: "calendar",
              name: "list_events",
              description: "List events in a date range",
              write: false,
              available: true,
            },
            {
              app: "calendar",
              name: "create_event",
              description: "Schedule a new event",
              write: true,
              available: false,
            },
            {
              app: "gmail",
              name: "search_threads",
              description: "Search mail with Gmail query syntax",
              write: false,
              available: true,
            },
            {
              app: "gmail",
              name: "create_draft",
              description: "Create a draft email",
              write: true,
              available: false,
            },
          ],
        },
      },
    })
  );

  await page.goto("/");
  await page.getByRole("tab", { name: "Ask API" }).click();

  // Both Google apps share the one OAuth grant, so both read Connected.
  await expect(page.getByText("me@example.com").first()).toBeVisible();
  await expect(page.getByText("Connected").first()).toBeVisible();

  // Calendar detail: only calendar tools, live badge, real disconnect button.
  await page.getByRole("button", { name: /Google Calendar/ }).first().click();
  await expect(
    page.getByRole("button", { name: /Connected as me@example\.com/ })
  ).toBeDisabled();
  await expect(page.getByText("list_events")).toBeVisible();
  await expect(page.getByText("live").first()).toBeVisible();
  await expect(page.getByText("create_event")).toBeVisible();
  await expect(page.getByText("⚠ needs approval").first()).toBeVisible();
  await expect(page.getByText("search_threads")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Disconnect Google" })
  ).toBeVisible();

  // Gmail detail: its own tools only.
  await page.getByRole("button", { name: "← All apps" }).click();
  await page.getByRole("button", { name: /Gmail/ }).first().click();
  await expect(page.getByText("search_threads")).toBeVisible();
  await expect(page.getByText("list_events")).toHaveCount(0);
  await expect(page.getByText("create_draft")).toBeVisible();
});
