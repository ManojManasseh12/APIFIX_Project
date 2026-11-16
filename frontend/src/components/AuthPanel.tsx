import { useState } from "react";
import { signIn, signUp, supabase } from "../lib/supabase";

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

  /** Google sign-in: redirects to Google, then back to this origin
   * (which must be allow-listed in the auth project's URL Configuration).
   * The App's onAuthStateChange picks the session up on return. */
  async function handleGoogle() {
    if (!supabase) return;
    setError(null);
    const { error: oauthError } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: window.location.origin },
    });
    if (oauthError) setError(oauthError.message);
  }

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

        {/* Google sign-in (configured in the auth project's Providers). */}
        <div className="mt-4">
          <div className="relative text-center">
            <span className="absolute inset-x-0 top-1/2 h-px bg-slate-200" />
            <span className="relative bg-white px-2 text-[11px] font-medium uppercase tracking-wide text-slate-400">
              or
            </span>
          </div>
          <button
            type="button"
            onClick={handleGoogle}
            className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white py-2.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
          >
            <svg className="h-4 w-4" viewBox="0 0 24 24" aria-hidden="true">
              <path fill="#EA4335" d="M12 5.04c1.62 0 3.06.56 4.2 1.64l3.12-3.12C17.46 1.8 14.96.75 12 .75 7.62.75 3.84 3.22 2.04 6.8l3.66 2.84C6.54 7.14 9.03 5.04 12 5.04z" />
              <path fill="#4285F4" d="M23.25 12.27c0-.79-.07-1.54-.2-2.27H12v4.51h6.3c-.27 1.48-1.11 2.73-2.36 3.57l3.62 2.81c2.12-1.96 3.69-4.85 3.69-8.62z" />
              <path fill="#FBBC05" d="M5.7 14.36a7.2 7.2 0 0 1 0-4.72L2.04 6.8a11.26 11.26 0 0 0 0 10.4l3.66-2.84z" />
              <path fill="#34A853" d="M12 23.25c3.04 0 5.6-1 7.46-2.72l-3.62-2.81c-1 .68-2.29 1.08-3.84 1.08-2.97 0-5.46-2.1-6.3-4.9l-3.66 2.84c1.8 3.58 5.58 6.51 9.96 6.51z" />
            </svg>
            Continue with Google
          </button>
        </div>

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
