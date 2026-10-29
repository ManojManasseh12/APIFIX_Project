import { useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import { streamAsk } from "../lib/sse";
import type { AskMessage, Citation } from "../types";

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

export function AskPanel() {
  const [messages, setMessages] = useState<AskMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [provider, setProvider] = useState<string | null>(null);
  const [latestSources, setLatestSources] = useState<Citation[]>([]);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy]);

  async function ask(question: string) {
    const trimmed = question.trim();
    if (!trimmed || busy) return;

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
    setLatestSources([]);

    let retrieved: Citation[] = [];

    try {
      await streamAsk({ question: trimmed, history }, (event, data) => {
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
              };
            }
            return next;
          });
          setProvider(data.provider ?? null);
        }

        if (event === "error") {
          setError(
            data.code === "no_providers_configured"
              ? "No AI provider key configured. Add GEMINI_API_KEY (or GROQ_API_KEY) to .env and restart the backend."
              : data.message ?? data.code
          );
        }
      });
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
      inputRef.current?.focus();
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
    setLatestSources([]);
    inputRef.current?.focus();
  }

  return (
    <div className="mx-auto grid max-w-[1550px] grid-cols-1 gap-5 lg:grid-cols-12">
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
            {busy && (
              <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-[11px] font-medium text-slate-500">
                thinking…
              </span>
            )}
          </div>
        </div>

        <div className="mt-4 flex-1 space-y-4 overflow-y-auto pr-1">
          {messages.length === 0 && (
            <div>
              <p className="text-xs leading-relaxed text-slate-600">
                Ask a question in plain English. Answers use the same grounded documentation
                index as diagnosis, and every supported claim gets a citable source.
              </p>
              <div className="mt-4 grid gap-2 sm:grid-cols-2">
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
            <p className="mt-3 text-xs text-slate-500">
              Sources used for the latest answer will appear here.
            </p>
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
      </aside>
    </div>
  );
}
