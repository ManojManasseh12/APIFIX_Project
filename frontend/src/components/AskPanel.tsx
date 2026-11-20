import { useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import { authedFetch, disconnectGoogle, getConnections, startGoogleConnect } from "../lib/api";
import { streamAsk } from "../lib/sse";
import { AskSidebar } from "./AskSidebar";
import type {
  AskMessage,
  AskTurnSummary,
  Citation,
  ConnectionsResponse,
} from "../types";
const TOPICS = [
  {
    label: "Methods",
    prompt: "Explain GET, POST, PUT, PATCH, and DELETE with examples.",
  },
  {
    label: "Status codes",
    prompt: "What is the difference between 400, 401, 403, 404, and 422?",
  },
  {
    label: "Auth",
    prompt: "Explain authentication vs authorization in APIs.",
  },
  {
    label: "Rate limits",
    prompt: "How should I handle a 429 rate limit response?",
  },
  {
    label: "Pagination",
    prompt: "What is the difference between offset and cursor pagination?",
  },
  {
    label: "Idempotency",
    prompt: "What is idempotency and why do POST retries create duplicates?",
  },
  {
    label: "Webhooks",
    prompt: "How do webhooks work and how should I verify them?",
  },
  {
    label: "REST",
    prompt: "What makes an API RESTful?",
  },
  {
    label: "Path vs query",
    prompt: "When should I use a path parameter instead of a query parameter?",
  },
  {
    label: "Headers",
    prompt: "Which HTTP headers matter most when debugging an API request?",
  },
  {
    label: "Errors",
    prompt: "Design a consistent JSON error response for an API.",
  },
  {
    label: "Uploads",
    prompt: "How does multipart/form-data file upload work?",
  },
];

const STARTERS = [
  "Why does my API return 401 but my token looks valid?",
  "PUT vs PATCH — which one should I use?",
  "How do I design pagination for a large users table?",
  "My webhook handler receives the same event twice. What should I do?",
];

// One tap-through card per debug helper — real functions that answer instantly.
const TOOL_CARDS = [
  {
    tool: "decode",
    title: "Decode · JWT",
    desc: "See exp and scopes when you get a 401 or 403",
    question:
      "decode this JWT eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwiZXhwIjoxNzMwMDAwMDAwfQ.dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk",
  },
  {
    tool: "epoch",
    title: "Epoch · 1730000000",
    desc: "Turn a Unix timestamp into a date",
    question: "what is 1730000000 in date?",
  },
  {
    tool: "dns",
    title: "DNS · api.github.com",
    desc: "Check that a host resolves",
    question: "check dns of api.github.com",
  },
  {
    tool: "ip",
    title: "IP · 8.8.8.8",
    desc: "See where an address points",
    question: "where is 8.8.8.8",
  },
  {
    tool: "time",
    title: "Time · Tokyo",
    desc: "Get the current time in any city",
    question: "time in Tokyo",
  },
];

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const minutes = Math.round((Date.now() - then) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(then).toLocaleDateString();
}

export function AskPanel() {
  const [messages, setMessages] = useState<AskMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [provider, setProvider] = useState<string | null>(null);
  const [toolCall, setToolCall] = useState<string | null>(null);
  const [latestSources, setLatestSources] = useState<Citation[]>([]);
  // When the latest answer came from a live tool, the sources panel explains
  // that instead of showing documentation chunks.
  const [lastTool, setLastTool] = useState<{ tool: string; label: string | null } | null>(null);
  const [askHistory, setAskHistory] = useState<AskTurnSummary[]>([]);
  // History arrives from the backend after the panel mounts; show an
  // honest loading state instead of flashing "0 saved" first.
  const [historyLoading, setHistoryLoading] = useState(true);
  // ChatGPT-style Connections sidebar: remembered per browser. On a first
  // visit it opens on desktop and stays closed on phones (where it would
  // otherwise cover the chat behind a drawer).
  const [connectionsOpen, setConnectionsOpen] = useState<boolean>(() => {
    try {
      const stored = localStorage.getItem("apifix.ask.sidebar");
      if (stored === "closed") return false;
      if (stored === "open") return true;
      return window.innerWidth >= 1024;
    } catch {
      return true;
    }
  });
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  // Real connection state from the backend — never assumed by the UI.
  const [connections, setConnections] = useState<ConnectionsResponse | null>(null);
  const [connectionsError, setConnectionsError] = useState<string | null>(null);
  const [googleBusy, setGoogleBusy] = useState(false);
  // Result of the OAuth round trip (?google=connected|error&message=…).
  const [connectNotice, setConnectNotice] = useState<
    { kind: "ok" | "error"; text: string } | null
  >(null);
  // Cancel the in-flight stream if the panel unmounts mid-answer.
  const streamAbortRef = useRef<AbortController | null>(null);
  // One id per chat session so persisted turns can be grouped later.
  const sessionRef = useRef<string>(crypto.randomUUID());

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy]);

  useEffect(() => () => streamAbortRef.current?.abort(), []);

  async function refreshAskHistory() {
    try {
      const response = await authedFetch("/api/ask/history");
      if (response.ok) setAskHistory((await response.json()).turns ?? []);
    } catch {
      // History panel keeps whatever it had; the backend may be restarting.
    } finally {
      setHistoryLoading(false);
    }
  }

  async function refreshConnections() {
    try {
      setConnections(await getConnections());
      setConnectionsError(null);
    } catch (err) {
      // Backend asleep or auth expired: the sidebar says so honestly
      // instead of showing a fake "Connected" badge.
      setConnections(null);
      setConnectionsError(
        (err as Error)?.message ?? "Could not read connection status."
      );
    }
  }

  useEffect(() => {
    void refreshAskHistory();

    // The Google callback redirects back with ?google=connected or
    // ?google=error&message=… — show it, refresh state, then clean the URL.
    const params = new URLSearchParams(window.location.search);
    const outcome = params.get("google");
    if (outcome) {
      setConnectNotice(
        outcome === "connected"
          ? { kind: "ok", text: "Google connected. Calendar and Gmail tools are live." }
          : {
              kind: "error",
              text: params.get("message") || "Google connection failed.",
            }
      );
      params.delete("google");
      params.delete("message");
      const query = params.toString();
      window.history.replaceState(
        {},
        "",
        `${window.location.pathname}${query ? `?${query}` : ""}`
      );
    }

    void refreshConnections();
  }, []);

  async function connectGoogle() {
    setGoogleBusy(true);
    setConnectNotice(null);
    try {
      window.location.href = await startGoogleConnect();
    } catch (err) {
      setConnectNotice({
        kind: "error",
        text: (err as Error)?.message ?? "Could not start the Google sign-in.",
      });
      setGoogleBusy(false);
    }
  }

  async function disconnectGoogleAccount() {
    setGoogleBusy(true);
    setConnectNotice(null);
    try {
      await disconnectGoogle();
      setConnectNotice({ kind: "ok", text: "Google disconnected." });
      await refreshConnections();
    } catch (err) {
      setConnectNotice({
        kind: "error",
        text: (err as Error)?.message ?? "Could not disconnect Google.",
      });
    } finally {
      setGoogleBusy(false);
    }
  }

  // Remember the sidebar state across reloads.
  useEffect(() => {
    try {
      localStorage.setItem("apifix.ask.sidebar", connectionsOpen ? "open" : "closed");
    } catch {
      // Private mode / storage disabled — the state just won't persist.
    }
  }, [connectionsOpen]);

  async function ask(question: string) {
    const trimmed = question.trim();
    if (!trimmed || busy) return;

    streamAbortRef.current?.abort();
    const controller = new AbortController();
    streamAbortRef.current = controller;

    const history = messages
      .filter((message) => message.content.trim())
      .slice(-4)
      .map((message) => ({ role: message.role, content: message.content }));

    setMessages((prev) => [
      ...prev,
      { role: "user", content: trimmed },
      { role: "assistant", content: "" },
    ]);
    setInput("");
    setError(null);
    setBusy(true);
    setProvider(null);
    setToolCall(null);
    setLatestSources([]);
    setLastTool(null);

    let retrieved: Citation[] = [];

    try {
      await streamAsk(
        { question: trimmed, history, session_id: sessionRef.current },
        (event, data) => {
        if (event === "tool") {
          // Backend builds the chip text (covers all 5 debug helpers); fall back to the name.
          setToolCall(data.label || data.name);
        }

        if (event === "retrieval") {
          retrieved = data.chunks ?? [];
          setLatestSources(retrieved);
        }

        if (event === "provider") {
          setProvider(data.name);
        }

        if (event === "provider_fallback") {
          setProvider(null);
        }

        if (event === "reset") {
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant") {
              next[next.length - 1] = { ...last, content: "", citations: undefined };
            }
            return next;
          });
        }

        if (event === "token") {
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant") {
              next[next.length - 1] = { ...last, content: last.content + data.text };
            }
            return next;
          });
        }

        if (event === "done") {
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant") {
              const ids = new Set<string>(data.citations ?? []);
              next[next.length - 1] = {
                ...last,
                content: data.answer || last.content,
                citations: retrieved.filter((chunk) => ids.has(chunk.chunk_id)),
                provider: data.provider ?? undefined,
                insufficient: Boolean(data.insufficient),
                tool: data.tool ?? undefined,
                tool_label: data.tool_label ?? undefined,
              };
            }
            return next;
          });
          setProvider(data.provider ?? null);
          setToolCall(null);
          setLastTool(
            data.tool ? { tool: data.tool, label: data.tool_label ?? null } : null
          );
          void refreshAskHistory();
        }

        if (event === "error") {
          setError(
            data.code === "no_providers_configured"
              ? "No AI provider key configured. Add GEMINI_API_KEY (or GROQ_API_KEY) to .env and restart the backend."
              : data.message ?? data.code
          );
        }
        },
        controller.signal
      );
    } catch (err) {
      if ((err as Error)?.name === "AbortError") return; // superseded or unmounted
      setError(String(err));
    } finally {
      // Only the newest stream owns the busy state; an aborted older
      // stream must not clear it.
      if (streamAbortRef.current === controller) {
        streamAbortRef.current = null;
        setBusy(false);
        inputRef.current?.focus();
      }
    }
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    ask(input);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      ask(input);
    }
  }

  function resetChat() {
    if (busy) return;
    setMessages([]);
    setInput("");
    setError(null);
    setProvider(null);
    setToolCall(null);
    setLatestSources([]);
    setLastTool(null);
    inputRef.current?.focus();
  }

  function restoreTurn(turn: AskTurnSummary) {
    if (busy) return;
    setMessages([
      { role: "user", content: turn.question },
      {
        role: "assistant",
        content: turn.answer,
        citations: turn.citations ?? [],
        provider: turn.provider ?? undefined,
        tool: turn.tool ?? undefined,
        tool_label: turn.tool_label ?? undefined,
      },
    ]);
    setLatestSources(turn.citations ?? []);
    setProvider(turn.provider ?? null);
    setError(null);
    setToolCall(null);
    setLastTool(
      turn.tool ? { tool: turn.tool, label: turn.tool_label ?? null } : null
    );
  }

  return (
    <>
      {connectionsOpen && (
        <div
          className="fixed inset-0 z-30 bg-slate-900/40 lg:hidden"
          onClick={() => setConnectionsOpen(false)}
          aria-hidden="true"
        />
      )}
      <div className="mx-auto flex max-w-[1750px] items-start gap-5">
        <AskSidebar
          open={connectionsOpen}
          onToggle={() => setConnectionsOpen((value) => !value)}
          onClose={() => setConnectionsOpen(false)}
          connections={connections}
          connectionsError={connectionsError}
          onConnectGoogle={() => void connectGoogle()}
          onDisconnectGoogle={() => void disconnectGoogleAccount()}
          googleBusy={googleBusy}
        />
        <div className="grid min-w-0 flex-1 grid-cols-1 gap-5 lg:grid-cols-12">
          <section className="flex min-h-[70vh] flex-col rounded-2xl border-2 border-teal-400 bg-white p-5 shadow-xs lg:col-span-8">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-3">
          <div>
            <h2 className="text-sm font-bold text-slate-900">API assistant</h2>
            <p className="mt-1 text-xs text-slate-500">
              Ask about endpoints, status codes, auth, pagination, webhooks, and more.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setConnectionsOpen(true)}
              aria-label="Open connections"
              className="rounded-full border border-slate-200 bg-white px-3 py-1 text-[11px] font-semibold text-slate-600 transition-colors hover:border-slate-300 hover:text-slate-800 lg:hidden"
            >
              🔌 Connections
            </button>
            <button
              onClick={resetChat}
              disabled={busy || messages.length === 0}
              className="rounded-full border border-slate-200 bg-white px-3 py-1 text-[11px] font-semibold text-slate-600 transition-colors hover:border-slate-300 hover:text-slate-800 disabled:cursor-not-allowed disabled:opacity-40"
            >
              New chat
            </button>
            {provider && (
              <span className="rounded-full border border-teal-200 bg-teal-50 px-3 py-1 text-[11px] font-semibold text-teal-700">
                {provider}
              </span>
            )}
            {toolCall && (
              <span className="rounded-full border border-violet-200 bg-violet-50 px-3 py-1 text-[11px] font-semibold text-violet-700">
                🔧 {toolCall}
              </span>
            )}
            {busy && !toolCall && (
              <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-[11px] font-medium text-slate-500">
                thinking…
              </span>
            )}
          </div>
        </div>

        {connectNotice && (
          <div
            role="status"
            className={`mt-3 flex items-start justify-between gap-3 rounded-lg border px-3 py-2 text-[11px] leading-snug ${
              connectNotice.kind === "ok"
                ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                : "border-amber-200 bg-amber-50 text-amber-800"
            }`}
          >
            <span>{connectNotice.text}</span>
            <button
              type="button"
              onClick={() => setConnectNotice(null)}
              aria-label="Dismiss"
              className="shrink-0 text-slate-400 transition hover:text-slate-700"
            >
              ✕
            </button>
          </div>
        )}

        <div className="mt-4 flex-1 space-y-4 overflow-y-auto pr-1">
          {messages.length === 0 && (
            <div>
              <p className="text-xs leading-relaxed text-slate-600">
                Ask a question in plain English. API and documentation questions are
                answered from the grounded documentation index with citable sources;
                the debug helpers below are real functions that run live.
              </p>
              <p className="mt-4 text-[10px] font-bold uppercase tracking-wider text-slate-400">
                Debug helpers
              </p>
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                {TOOL_CARDS.map((card) => (
                  <button
                    key={card.tool}
                    onClick={() => ask(card.question)}
                    disabled={busy}
                    title={card.question}
                    className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-left transition-colors hover:border-violet-300 hover:bg-violet-50 disabled:opacity-50"
                  >
                    <span className="font-mono text-[11px] font-bold text-violet-700">
                      {card.title}
                    </span>
                    <span className="mt-0.5 block text-[11px] leading-snug text-slate-600">
                      {card.desc}
                    </span>
                  </button>
                ))}
              </div>
              <p className="mt-4 text-[10px] font-bold uppercase tracking-wider text-slate-400">
                From the docs
              </p>
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                {STARTERS.map((starter) => (
                  <button
                    key={starter}
                    onClick={() => ask(starter)}
                    className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-left text-xs font-medium text-slate-700 transition-colors hover:border-teal-300 hover:bg-teal-50 hover:text-teal-800"
                  >
                    {starter}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((message, index) => (
            <div
              key={index}
              className={message.role === "user" ? "flex justify-end" : "flex justify-start"}
            >
              <div
                className={
                  message.role === "user"
                    ? "max-w-[85%] rounded-2xl rounded-br-sm bg-slate-950 px-4 py-2.5 text-xs leading-relaxed text-white"
                    : "max-w-[92%] rounded-2xl rounded-bl-sm border border-slate-200 bg-slate-50 px-4 py-3 text-xs leading-relaxed text-slate-800"
                }
              >
                {message.role === "assistant" && !message.content && busy && (
                  <span className="text-slate-400">Retrieving docs and thinking…</span>
                )}
                {message.role === "assistant" && message.tool && message.content && (
                  <div className="mb-2">
                    <span className="inline-flex items-center gap-1 rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-[10px] font-semibold text-violet-700">
                      ⚡ Live tool · {message.tool_label || message.tool}
                    </span>
                    <p className="mt-1 text-[10px] leading-snug text-violet-500">
                      Computed now by a real function — not taken from the documentation.
                    </p>
                  </div>
                )}
                {message.role === "assistant" && !message.tool && message.content && (
                  <span className="mb-2 inline-flex items-center gap-1 rounded-full border border-teal-200 bg-teal-50 px-2 py-0.5 text-[10px] font-semibold text-teal-700">
                    📖 From the docs
                  </span>
                )}
                <div className="whitespace-pre-wrap">{message.content}</div>

                {message.insufficient && (
                  <p className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-2 py-1.5 text-[11px] text-amber-800">
                    Part of this answer is general guidance — the indexed docs did not cover
                    the whole question.
                  </p>
                )}

                {message.citations && message.citations.length > 0 && (
                  <div className="mt-3 border-t border-slate-200 pt-2">
                    <p className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
                      Sources
                    </p>
                    <ul className="mt-1.5 space-y-1">
                      {message.citations.map((citation) => (
                        <li key={citation.chunk_id} className="text-[11px]">
                          <a
                            href={citation.source_url}
                            target="_blank"
                            rel="noreferrer"
                            className="font-mono text-teal-700 underline decoration-teal-300 underline-offset-2"
                          >
                            {citation.chunk_id}
                          </a>{" "}
                          <span className="text-slate-600">
                            {citation.source_title}
                            {citation.section ? ` · ${citation.section}` : ""}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        {error && (
          <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50 p-2.5 text-xs text-rose-700">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="mt-3 border-t border-slate-100 pt-3">
          <div className="flex items-end gap-2">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleKeyDown}
              rows={2}
              placeholder="Ask any API question… (Enter to send, Shift+Enter for a new line)"
              className="min-h-[52px] flex-1 resize-none rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-xs text-slate-800 outline-none transition-colors placeholder:text-slate-400 focus:border-teal-400 focus:bg-white"
            />
            <button
              type="submit"
              disabled={busy || !input.trim()}
              className="rounded-xl bg-teal-600 px-4 py-3 text-xs font-bold text-white transition-colors hover:bg-teal-700 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {busy ? "Streaming" : "Ask"}
            </button>
          </div>
          <p className="mt-2 text-[10px] text-slate-400">
            Documentation-backed where possible. Only the last 4 messages are sent as context
            to keep answers fast and cheap.
          </p>
        </form>
      </section>

      <aside className="space-y-5 lg:col-span-4">
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
          <h2 className="text-sm font-bold text-slate-900">How to read an answer</h2>
          <ul className="mt-3 space-y-3">
            <li className="flex gap-2.5">
              <span className="mt-0.5 shrink-0 rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-[10px] font-semibold text-violet-700">
                ⚡ Live tool
              </span>
              <p className="text-[11px] leading-snug text-slate-600">
                A real function just ran (decoder, epoch, DNS, IP, clock). The
                value is computed at answer time — fresh and exact.
              </p>
            </li>
            <li className="flex gap-2.5">
              <span className="mt-0.5 shrink-0 rounded-full border border-teal-200 bg-teal-50 px-2 py-0.5 text-[10px] font-semibold text-teal-700">
                📖 From the docs
              </span>
              <p className="text-[11px] leading-snug text-slate-600">
                Written from your indexed documentation. Check the cited
                sources under the answer — they link to the exact pages used.
              </p>
            </li>
            <li className="flex gap-2.5">
              <span className="mt-0.5 shrink-0 rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-semibold text-amber-700">
                ⚠ Partial
              </span>
              <p className="text-[11px] leading-snug text-slate-600">
                Flagged when the docs didn't cover the whole question; the
                flagged part is general guidance, not a documented fact.
              </p>
            </li>
          </ul>
        </section>

        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
          <h2 className="text-sm font-bold text-slate-900">API concepts</h2>
          <p className="mt-1 text-xs text-slate-500">
            Tap a topic to ask about it.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {TOPICS.map((topic) => (
              <button
                key={topic.label}
                onClick={() => ask(topic.prompt)}
                disabled={busy}
                className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1.5 text-[11px] font-medium text-slate-600 transition-colors hover:border-teal-300 hover:bg-teal-50 hover:text-teal-800 disabled:opacity-50"
              >
                {topic.label}
              </button>
            ))}
          </div>
        </section>

        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-bold text-slate-900">Grounded sources</h2>
            <span className="text-[11px] text-slate-400">
              {latestSources.length} retrieved
            </span>
          </div>
          {latestSources.length === 0 ? (
            lastTool ? (
              <p className="mt-3 text-xs leading-relaxed text-slate-500">
                This answer came from the live{" "}
                <span className="font-semibold text-violet-700">{lastTool.tool}</span>{" "}
                tool{lastTool.label ? ` (${lastTool.label})` : ""} — it has no
                documentation sources. The violet “Live tool” badge on the answer
                shows which function produced it.
              </p>
            ) : (
              <p className="mt-3 text-xs text-slate-500">
                Sources used for the latest answer will appear here.
              </p>
            )
          ) : (
            <ul className="mt-3 space-y-2">
              {latestSources.map((source) => (
                <li
                  key={source.chunk_id}
                  className="rounded-xl border border-slate-200 bg-slate-50 p-2.5"
                >
                  <a
                    href={source.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-[11px] font-semibold text-teal-700 underline decoration-teal-300 underline-offset-2"
                  >
                    {source.chunk_id}
                  </a>
                  <p className="mt-1 text-[11px] leading-snug text-slate-600">
                    {source.source_title}
                    {source.section ? ` · ${source.section}` : ""}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-bold text-slate-900">Ask history</h2>
            <span className="text-[11px] text-slate-400">
              {historyLoading ? "loading…" : `${askHistory.length} saved`}
            </span>
          </div>
          {historyLoading ? (
            <ul className="mt-3 space-y-2" aria-hidden="true">
              {[0, 1].map((i) => (
                <li
                  key={i}
                  className="h-[52px] animate-pulse rounded-xl border border-slate-200 bg-slate-100"
                />
              ))}
            </ul>
          ) : askHistory.length === 0 ? (
            <p className="mt-3 text-xs text-slate-500">
              Answered questions are saved automatically — like diagnosis runs — and
              can be reopened here after a reload.
            </p>
          ) : (
            <ul className="mt-3 max-h-[320px] space-y-2 overflow-y-auto pr-1">
              {askHistory.map((turn) => (
                <li key={turn.turn_id}>
                  <button
                    onClick={() => restoreTurn(turn)}
                    disabled={busy}
                    title={`Reopen: ${turn.question}`}
                    className="w-full rounded-xl border border-slate-200 bg-slate-50 p-2.5 text-left transition-colors hover:border-teal-300 hover:bg-teal-50 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <p className="line-clamp-2 text-[11px] font-medium leading-snug text-slate-800">
                        {turn.question}
                      </p>
                      <span className="shrink-0 text-[10px] text-slate-400">
                        {relativeTime(turn.created_at)}
                      </span>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5">
                      {turn.tool ? (
                        <span className="rounded-full border border-violet-200 bg-violet-50 px-2 py-0.5 text-[10px] font-semibold text-violet-700">
                          ⚡ {turn.tool_label || turn.tool}
                        </span>
                      ) : (
                        <span className="rounded-full border border-teal-200 bg-teal-50 px-2 py-0.5 text-[10px] font-semibold text-teal-700">
                          📖 From the docs
                        </span>
                      )}
                      {turn.provider && (
                        <span className="text-[10px] text-slate-400">{turn.provider}</span>
                      )}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </aside>
        </div>
      </div>
    </>
  );
}
