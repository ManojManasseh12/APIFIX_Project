import { useState } from "react";
import { signIn, signUp } from "../lib/supabase";

/**
 * Sign in / create account screen shown when Supabase Auth is configured
 * and no session exists. UI only: it talks to the auth project and holds
 * no secrets; the backend verifies every issued token independently.
 */
export function AuthPanel({ onAuthed }: { onAuthed: () => void }) {
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setInfo(null);

    if (!email.trim() || password.length < 6) {
      setError("Enter an email and a password of at least 6 characters.");
      return;
    }

    setBusy(true);
    try {
      const failure = mode === "signin" ? await signIn(email.trim(), password) : await signUp(email.trim(), password);
      if (failure) {
        setError(failure);
        return;
      }
      // signUp with "Confirm email" OFF signs the user straight in.
      // With it ON the user must click the emailed link; sign-in then
      // happens from that link, so show a notice instead of a redirect.
      if (mode === "signup") {
        setInfo(
          "Account created. If email confirmation is enabled, click the link in your inbox — otherwise you are already signed in."
        );
      }
      onAuthed();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-[80vh] items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-7 shadow-sm">
        <div className="mb-6 text-center">
          <div className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-xl bg-slate-950">
            <svg className="h-6 w-6 text-teal-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="m3 16 5-7 4 5 5-8 4 6" />
            </svg>
          </div>
          <h1 className="text-lg font-bold text-slate-900">APIFix AI</h1>
          <p className="mt-1 text-xs text-slate-500">
            {mode === "signin" ? "Sign in to your workspace" : "Create your workspace account"}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="auth-email" className="mb-1 block text-xs font-semibold text-slate-600">
              Email
            </label>
            <input
              id="auth-email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@example.com"
              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100"
            />
          </div>
          <div>
            <label htmlFor="auth-password" className="mb-1 block text-xs font-semibold text-slate-600">
              Password
            </label>
            <input
              id="auth-password"
              type="password"
              autoComplete={mode === "signin" ? "current-password" : "new-password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="At least 6 characters"
              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-100"
            />
          </div>

          {error && (
            <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-medium text-rose-700">
              {error}
            </div>
          )}
          {info && (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-medium text-emerald-800">
              {info}
            </div>
          )}

          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-lg bg-slate-950 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:opacity-50"
          >
            {busy ? "Please wait…" : mode === "signin" ? "Sign in" : "Create account"}
          </button>
        </form>

        <button
          type="button"
          onClick={() => {
            setMode(mode === "signin" ? "signup" : "signin");
            setError(null);
            setInfo(null);
          }}
          className="mt-4 w-full text-center text-xs font-medium text-teal-700 hover:underline"
        >
          {mode === "signin"
            ? "No account yet? Create one"
            : "Already have an account? Sign in"}
        </button>
      </div>
    </div>
  );
}
