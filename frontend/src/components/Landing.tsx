/**
 * Public landing page: what APIFix AI is, how it works, and what it is
 * honest about. Every claim matches real behaviour — no invented
 * numbers. The only interactive element is the Login/Signup button,
 * which opens the auth screen; the workspace stays behind login.
 */

function BrandMark() {
  return (
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
  );
}

function GoogleG() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 24 24" aria-hidden="true">
      <path fill="#EA4335" d="M12 5.04c1.62 0 3.06.56 4.2 1.64l3.12-3.12C17.46 1.8 14.96.75 12 .75 7.62.75 3.84 3.22 2.04 6.8l3.66 2.84C6.54 7.14 9.03 5.04 12 5.04z" />
      <path fill="#4285F4" d="M23.25 12.27c0-.79-.07-1.54-.2-2.27H12v4.51h6.3c-.27 1.48-1.11 2.73-2.36 3.57l3.62 2.81c2.12-1.96 3.69-4.85 3.69-8.62z" />
      <path fill="#FBBC05" d="M5.7 14.36a7.2 7.2 0 0 1 0-4.72L2.04 6.8a11.26 11.26 0 0 0 0 10.4l3.66-2.84z" />
      <path fill="#34A853" d="M12 23.25c3.04 0 5.6-1 7.46-2.72l-3.62-2.81c-1 .68-2.29 1.08-3.84 1.08-2.97 0-5.46-2.1-6.3-4.9l-3.66 2.84c1.8 3.58 5.58 6.51 9.96 6.51z" />
    </svg>
  );
}

const STEPS = [
  {
    n: "1 · TEST",
    title: "Run the real request",
    body: "The backend sends your request with an async HTTP client and captures the true status code, headers, body and timing. No simulated responses.",
  },
  {
    n: "2 · DIAGNOSE",
    title: "Find the cause in the docs",
    body: "Hybrid retrieval (pgvector + BM25) finds the matching sections of the indexed documentation. The model explains the error and cites the exact chunks it used — unsupported causes are labelled as hypotheses.",
  },
  {
    n: "3 · VERIFY",
    title: "Re-run and confirm",
    body: "Apply the suggested fix and the same request runs again. A fix is only marked confirmed when the re-run actually succeeds — never assumed.",
  },
];

const FEATURES = [
  {
    title: "Answers cite sources",
    body: "Every diagnosis links to the documentation chunks it used. Click a citation to open the original page.",
  },
  {
    title: "Says when it doesn't know",
    body: "If the indexed docs don't cover the problem, you get \"insufficient evidence\" and next checks — not a confident guess.",
  },
  {
    title: "Your docs, your control",
    body: "Import an OpenAPI file and it is chunked, embedded and indexed for your API. No model retraining, no shared data.",
  },
  {
    title: "Live tools, clearly badged",
    body: "Time, DNS, IP lookups and JWT/epoch decoding run as real functions — answers made by a tool are badged as tool results.",
  },
  {
    title: "Streaming, live pipeline",
    body: "Diagnosis and Ask stream token by token over server-sent events, with a live pipeline feed: request → retrieval → provider → done.",
  },
  {
    title: "Private per account",
    body: "Sign in with Google or email. Runs and Ask history are saved only for your account and verified in the backend on every request.",
  },
];

const STACK = [
  ["Backend", "FastAPI + httpx async runner, LangChain prompt templates, provider fallback chain (Groq → Gemini → Mistral → NVIDIA)"],
  ["Retrieval", "Supabase Postgres with pgvector + BM25 keyword hybrid, reciprocal rank fusion"],
  ["Frontend", "React + Vite, server-sent events streaming"],
  ["Auth & data", "Supabase Auth (Google/email), per-account history in Postgres; tokens verified against the project's JWKS"],
];

export function Landing({ onLogin }: { onLogin: () => void }) {
  return (
    <div className="min-h-screen bg-white text-slate-800 antialiased">
      {/* Top nav */}
      <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-5 py-3">
          <div className="flex items-center gap-3">
            <BrandMark />
            <div>
              <span className="text-base font-bold tracking-tight text-slate-900">APIFix AI</span>
              <span className="ml-2 hidden text-xs text-slate-500 sm:inline">
                Documentation-grounded API debugging
              </span>
            </div>
          </div>
          <nav className="flex items-center gap-5">
            <a href="#how" className="hidden text-xs font-medium text-slate-600 transition hover:text-slate-900 sm:inline">
              How it works
            </a>
            <a href="#features" className="hidden text-xs font-medium text-slate-600 transition hover:text-slate-900 sm:inline">
              What you get
            </a>
            <a href="#stack" className="hidden text-xs font-medium text-slate-600 transition hover:text-slate-900 md:inline">
              Under the hood
            </a>
            <button
              type="button"
              onClick={onLogin}
              className="rounded-lg bg-slate-950 px-4 py-2 text-xs font-semibold text-white transition hover:bg-slate-800"
            >
              Login / Sign up
            </button>
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-5">
        {/* Hero */}
        <section className="grid items-center gap-10 py-14 lg:grid-cols-2 lg:py-20">
          <div>
            <span className="inline-flex items-center rounded-full border border-teal-200 bg-teal-50 px-3 py-1 text-[11px] font-semibold text-teal-700">
              Test · Diagnose · Verify
            </span>
            <h1 className="mt-4 text-4xl font-black leading-tight tracking-tight text-slate-900 sm:text-5xl">
              Test your API. Know why it failed. Fix it.
            </h1>
            <p className="mt-4 max-w-xl text-sm leading-relaxed text-slate-600">
              Send a request, see the real response, and get the cause and a concrete fix
              grounded in the API's own documentation — with sources you can check and a
              re-run that proves the fix.
            </p>
            <div className="mt-6 flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={onLogin}
                className="flex items-center gap-2 rounded-lg bg-slate-950 px-5 py-3 text-sm font-semibold text-white transition hover:bg-slate-800"
              >
                <GoogleG />
                Get started — Login / Sign up
              </button>
              <a
                href="#how"
                className="rounded-lg border border-slate-200 px-5 py-3 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
              >
                See how it works
              </a>
            </div>
            <p className="mt-4 text-[11px] text-slate-400">
              Google or email sign-in · your history stays private to your account
            </p>
          </div>

          {/* Sample illustration — labelled, not fake live data */}
          <div className="rounded-2xl border border-slate-200 bg-slate-50 p-5 shadow-sm">
            <div className="flex items-center justify-between">
              <code className="rounded-md bg-slate-900 px-2.5 py-1 text-[11px] font-semibold text-teal-300">
                POST /repos/octocat/hello-world/issues
              </code>
              <span className="rounded-full bg-amber-100 px-2.5 py-0.5 text-[11px] font-bold text-amber-700">
                422
              </span>
            </div>
            <pre className="mt-3 overflow-x-auto rounded-xl bg-slate-900 p-3.5 text-[11px] leading-relaxed text-slate-100">
{`{
  "errors": [
    { "code": "missing_field", "field": "title" }
  ]
}`}
            </pre>
            <div className="mt-3 rounded-xl border border-teal-200 bg-white p-3.5">
              <p className="text-xs font-bold text-slate-900">
                Diagnosis: the required <code className="rounded bg-slate-100 px-1">title</code> field is missing. Add it and re-run.
              </p>
              <p className="mt-1.5 text-[11px] text-teal-700">Source: gh-issues-create · chunk citation you can open</p>
            </div>
            <p className="mt-3 text-center text-[10px] font-medium uppercase tracking-wide text-slate-400">
              Example with sample data — your runs use real requests
            </p>
          </div>
        </section>

        {/* How it works */}
        <section id="how" className="scroll-mt-20 border-t border-slate-200 py-14">
          <h2 className="text-2xl font-bold tracking-tight text-slate-900">Three steps, one loop</h2>
          <div className="mt-8 grid gap-5 md:grid-cols-3">
            {STEPS.map((step) => (
              <div key={step.n} className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
                <span className="text-[11px] font-bold uppercase tracking-wider text-teal-600">{step.n}</span>
                <h3 className="mt-2 text-sm font-bold text-slate-900">{step.title}</h3>
                <p className="mt-2 text-xs leading-relaxed text-slate-600">{step.body}</p>
              </div>
            ))}
          </div>
        </section>

        {/* Features */}
        <section id="features" className="scroll-mt-20 border-t border-slate-200 py-14">
          <h2 className="text-2xl font-bold tracking-tight text-slate-900">What you get</h2>
          <div className="mt-8 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((feature) => (
              <div key={feature.title} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                <h3 className="text-sm font-bold text-slate-900">{feature.title}</h3>
                <p className="mt-2 text-xs leading-relaxed text-slate-600">{feature.body}</p>
              </div>
            ))}
          </div>
        </section>

        {/* Stack */}
        <section id="stack" className="scroll-mt-20 border-t border-slate-200 py-14">
          <h2 className="text-2xl font-bold tracking-tight text-slate-900">Under the hood</h2>
          <p className="mt-2 text-xs text-slate-500">
            Runs on your machine — the knowledge base and accounts live in the cloud.
          </p>
          <dl className="mt-8 grid gap-4 sm:grid-cols-2">
            {STACK.map(([term, body]) => (
              <div key={term} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                <dt className="text-[11px] font-bold uppercase tracking-wider text-slate-400">{term}</dt>
                <dd className="mt-1.5 text-xs leading-relaxed text-slate-600">{body}</dd>
              </div>
            ))}
          </dl>
        </section>
      </main>

      <footer className="border-t border-slate-200 py-8">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-5">
          <div className="flex items-center gap-2">
            <BrandMark />
            <span className="text-xs font-semibold text-slate-700">APIFix AI</span>
          </div>
          <p className="text-[11px] text-slate-400">
            Documentation-grounded API debugging · FastAPI · React · Supabase
          </p>
        </div>
      </footer>
    </div>
  );
}
