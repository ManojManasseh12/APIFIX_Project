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

  await expect(page.getByRole("heading", { name: "APIFix AI" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "AI diagnosis" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Run test" })).toBeVisible();

  await page.getByRole("button", { name: "Ask API" }).click();
  await expect(page.getByRole("heading", { name: "API assistant" })).toBeVisible();
  await expect(page.getByText("Ask a question in plain English.")).toBeVisible();

  await page.getByRole("button", { name: "Diagnose" }).click();
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
  await page.getByRole("button", { name: "Ask API" }).click();
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
