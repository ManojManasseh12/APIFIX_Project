import { useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import { authedFetch, disconnectGoogle, getConnections, startGoogleConnect } from "../lib/api";
import { streamAsk } from "../lib/sse";
import { AskSidebar } from "./AskSidebar";
import { AskActivityIndicator, LiveToolBadge } from "./AskActivity";
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
  // What the running tool is doing (name + chip label), shown in the bubble.
  const [activeTool, setActiveTool] = useState<{
    name: string;
    label: string | null;
  } | null>(null);
  // Coarse phase for the activity line before a tool is chosen.
  const [phase, setPhase] = useState<"thinking" | "docs" | "tool">("thinking");
  // Index of the assistant message currently being streamed (null when idle).
  const [streamingIndex, setStreamingIndex] = useState<number | null>(null);
  // Regenerations per assistant message — ChatGPT-like, capped at 2.
  const [regenCounts, setRegenCounts] = useState<Record<number, number>>({});
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);
  // Inline editor for a sent question (pencil icon on the user bubble).
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [editText, setEditText] = useState("");
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
    await runAsk(question);
  }

  /**
   * Send one question. With ``replaceIndex`` the answer is streamed back into
   * that existing assistant message (Regenerate); without it a new user +
   * assistant pair is appended.
   */
  async function runAsk(question: string, replaceIndex?: number) {
    const trimmed = question.trim();
    if (!trimmed || busy) return;

    // The assistant message that receives the tokens.
    const assistantIndex = replaceIndex ?? messages.length + 1;
    // Regenerating must not send the question twice (it is already at
    // replaceIndex - 1), so history stops before it.
    const historyEnd =
      replaceIndex === undefined ? messages.length : Math.max(0, replaceIndex - 1);
    const history = messages
      .slice(0, historyEnd)
      .filter((message) => message.content.trim())
      .slice(-4)
      .map((message) => ({ role: message.role, content: message.content }));

    setMessages((prev) => {
      const next = [...prev];
      if (replaceIndex === undefined) {
        next.push({ role: "user", content: trimmed });
        next.push({ role: "assistant", content: "" });
      } else {
        next[replaceIndex] = { role: "assistant", content: "" };
      }
      return next;
    });
    setInput("");
    await streamAnswer(trimmed, history, assistantIndex);
  }

  /**
   * Edit a sent question the ChatGPT way: everything from that turn onward is
   * dropped, the edited question replaces it, and the answer streams again.
   */
  async function submitEdit(index: number, text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;

    const base = messages.slice(0, index);
    const history = base
      .filter((message) => message.content.trim())
      .slice(-4)
      .map((message) => ({ role: message.role, content: message.content }));

    setMessages([
      ...base,
      { role: "user", content: trimmed },
      { role: "assistant", content: "" },
    ]);
    setEditingIndex(null);
    setEditText("");
    setRegenCounts({});
    setCopiedIndex(null);
    await streamAnswer(trimmed, history, base.length + 1);
  }

  /** Stream one answer into ``assistantIndex``. */
  async function streamAnswer(
    question: string,
    history: { role: "user" | "assistant"; content: string }[],
    assistantIndex: number
  ) {
    streamAbortRef.current?.abort();
    const controller = new AbortController();
    streamAbortRef.current = controller;

    setError(null);
    setBusy(true);
    setStreamingIndex(assistantIndex);
    setProvider(null);
    setActiveTool(null);
    setPhase("thinking");
    setLatestSources([]);
    setLastTool(null);

    const patchAssistant = (patch: (message: AskMessage) => AskMessage) => {
      setMessages((prev) => {
        const next = [...prev];
        const current = next[assistantIndex];
        if (current?.role === "assistant") next[assistantIndex] = patch(current);
        return next;
      });
    };

    let retrieved: Citation[] = [];

    try {
      await streamAsk(
        { question, history, session_id: sessionRef.current },
        (event, data) => {
        if (event === "tool") {
          // The router decided: show the real activity, not a generic label.
          setPhase("tool");
          setActiveTool({
            name: data.name || "",
            label: data.label ?? null,
          });
        }

        if (event === "retrieval") {
          // Documentation search started — say exactly that.
          setPhase("docs");
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
          patchAssistant((message) => ({
            ...message,
            content: "",
            citations: undefined,
          }));
        }

        if (event === "token") {
          patchAssistant((message) => ({
            ...message,
            content: message.content + data.text,
          }));
        }

        if (event === "done") {
          patchAssistant((message) => {
            const ids = new Set<string>(data.citations ?? []);
            return {
              ...message,
              content: data.answer || message.content,
              citations: retrieved.filter((chunk) => ids.has(chunk.chunk_id)),
              provider: data.provider ?? undefined,
              insufficient: Boolean(data.insufficient),
              tool: data.tool ?? undefined,
              tool_label: data.tool_label ?? undefined,
            };
          });
          setProvider(data.provider ?? null);
          setActiveTool(null);
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
        setStreamingIndex(null);
        setActiveTool(null);
        setPhase("thinking");
        inputRef.current?.focus();
      }
    }
  }

  function regenerate(index: number) {
    if (busy) return;
    const used = regenCounts[index] ?? 0;
    if (used >= 2) return;
    const question = messages[index - 1];
    if (!question || question.role !== "user") return;
    setRegenCounts((prev) => ({ ...prev, [index]: used + 1 }));
    void runAsk(question.content, index);
  }

  async function copyAnswer(index: number, text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedIndex(index);
      window.setTimeout(
        () => setCopiedIndex((current) => (current === index ? null : current)),
        1500
      );
    } catch {
      setError("Copy failed — your browser blocked clipboard access.");
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
    setActiveTool(null);
    setPhase("thinking");
    setStreamingIndex(null);
    setRegenCounts({});
    setCopiedIndex(null);
    setEditingIndex(null);
    setEditText("");
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
    setActiveTool(null);
    setPhase("thinking");
    setStreamingIndex(null);
    setRegenCounts({});
    setCopiedIndex(null);
    setEditingIndex(null);
    setEditText("");
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
              className={`group flex ${
                message.role === "user" ? "justify-end" : "justify-start"
              }`}
            >
              {message.role === "user" ? (
                <div className="flex max-w-[85%] flex-col items-end gap-1">
                  {editingIndex === index ? (
                    <div className="w-full">
                      <textarea
                        autoFocus
                        value={editText}
                        onChange={(event) => setEditText(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === "Escape") {
                            setEditingIndex(null);
                            setEditText("");
                          }
                          if (event.key === "Enter" && !event.shiftKey) {
                            event.preventDefault();
                            void submitEdit(index, editText);
                          }
                        }}
                        rows={2}
                        aria-label="Edit question"
                        className="w-full resize-none rounded-2xl border border-teal-400 bg-white px-4 py-2.5 text-xs text-slate-800 outline-none focus:border-teal-500"
                      />
                      <div className="mt-1 flex justify-end gap-1">
                        <button
                          type="button"
                          onClick={() => {
                            setEditingIndex(null);
                            setEditText("");
                          }}
                          className="rounded-md px-2 py-1 text-[10px] font-semibold text-slate-500 transition-colors hover:bg-slate-100"
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          onClick={() => void submitEdit(index, editText)}
                          disabled={busy || !editText.trim()}
                          className="rounded-md bg-teal-600 px-2 py-1 text-[10px] font-semibold text-white transition-colors hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          Save &amp; resend
                        </button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="rounded-2xl rounded-br-sm bg-teal-700 px-4 py-2.5 text-xs leading-relaxed text-white shadow-xs">
                        <div className="whitespace-pre-wrap">{message.content}</div>
                      </div>
                      <div className="flex items-center gap-1 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100 max-lg:opacity-100">
                        <button
                          type="button"
                          onClick={() => void copyAnswer(index, message.content)}
                          aria-label="Copy message"
                          title={copiedIndex === index ? "Copied" : "Copy message"}
                          className="rounded-md p-1 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700"
                        >
                          {copiedIndex === index ? (
                            <svg
                              className="h-3.5 w-3.5 text-emerald-600"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              aria-hidden="true"
                            >
                              <path d="M20 6L9 17l-5-5" />
                            </svg>
                          ) : (
                            <svg
                              className="h-3.5 w-3.5"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              aria-hidden="true"
                            >
                              <rect x="9" y="9" width="11" height="11" rx="2" />
                              <path d="M5 15V5a2 2 0 0 1 2-2h10" />
                            </svg>
                          )}
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setEditingIndex(index);
                            setEditText(message.content);
                          }}
                          disabled={busy}
                          aria-label="Edit message"
                          title="Edit this question"
                          className="rounded-md p-1 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          <svg
                            className="h-3.5 w-3.5"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            aria-hidden="true"
                          >
                            <path d="M12 20h9" />
                            <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
                          </svg>
                        </button>
                      </div>
                    </>
                  )}
                </div>
              ) : (
              <div
                className={
                  "max-w-[92%] rounded-2xl rounded-bl-sm border border-slate-200 bg-slate-50 px-4 py-3 text-xs leading-relaxed text-slate-800"
                }
              >
                {message.role === "assistant" && streamingIndex === index && !message.content && (
                  <AskActivityIndicator
                    tool={activeTool?.name ?? null}
                    label={activeTool?.label ?? null}
                    mode={phase}
                  />
                )}
                {message.role === "assistant" && message.tool && message.content && (
                  <LiveToolBadge
                    tool={message.tool}
                    label={message.tool_label ?? null}
                  />
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

                {message.role === "assistant" &&
                  message.content &&
                  streamingIndex !== index && (
                    <div className="mt-2 flex items-center gap-1 border-t border-slate-200 pt-2">
                      <button
                        type="button"
                        onClick={() => void copyAnswer(index, message.content)}
                        aria-label="Copy answer"
                        className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[10px] font-semibold text-slate-500 transition-colors hover:bg-slate-200/60 hover:text-slate-800"
                      >
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
                          <rect x="9" y="9" width="11" height="11" rx="2" />
                          <path d="M5 15V5a2 2 0 0 1 2-2h10" />
                        </svg>
                        {copiedIndex === index ? "Copied" : "Copy"}
                      </button>
                      {index === messages.length - 1 && (
                        <button
                          type="button"
                          onClick={() => regenerate(index)}
                          disabled={busy || (regenCounts[index] ?? 0) >= 2}
                          aria-label="Regenerate answer"
                          title={
                            (regenCounts[index] ?? 0) >= 2
                              ? "Regenerate limit reached (2 per answer)"
                              : "Ask the same question again"
                          }
                          className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[10px] font-semibold text-slate-500 transition-colors hover:bg-slate-200/60 hover:text-slate-800 disabled:cursor-not-allowed disabled:opacity-40"
                        >
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
                            <path d="M21 12a9 9 0 1 1-2.6-6.4" />
                            <path d="M21 3v6h-6" />
                          </svg>
                          Regenerate
                        </button>
                      )}
                      {index === messages.length - 1 &&
                        (regenCounts[index] ?? 0) > 0 && (
                          <span className="ml-auto text-[10px] text-slate-400">
                            {regenCounts[index]}/2
                          </span>
                        )}
                    </div>
                  )}
              </div>
              )}
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
