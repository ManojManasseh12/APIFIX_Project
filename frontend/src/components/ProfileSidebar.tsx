import { useEffect, useState } from "react";
import { supabase } from "../lib/supabase";

type UserMeta = {
  email: string | null;
  provider: string;
  createdAt: string | null;
  lastSignInAt: string | null;
};

function initialsOf(email: string | null): string {
  if (!email) return "??";
  const local = email.split("@")[0];
  return local.slice(0, 2).toUpperCase();
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/**
 * Slide-over profile panel. Reads the live Supabase Auth user — the same
 * identity the backend verified on every request — and shows only facts
 * the auth project actually returns (no invented data).
 */
export function ProfileSidebar({
  open,
  onClose,
  onSignOut,
}: {
  open: boolean;
  onClose: () => void;
  onSignOut: () => void;
}) {
  const [user, setUser] = useState<UserMeta | null>(null);
  const [signingOut, setSigningOut] = useState(false);

  useEffect(() => {
    if (!open || !supabase) return;
    void supabase.auth.getUser().then(({ data }) => {
      const u = data.user;
      setUser(
        u
          ? {
              email: u.email ?? null,
              provider: (u.app_metadata?.provider as string | undefined) ?? "email",
              createdAt: u.created_at ?? null,
              lastSignInAt: u.last_sign_in_at ?? null,
            }
          : null
      );
    });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50">
      {/* Backdrop */}
      <button
        type="button"
        aria-label="Close profile"
        onClick={onClose}
        className="absolute inset-0 h-full w-full cursor-default bg-slate-950/30 backdrop-blur-[2px]"
      />

      {/* Panel */}
      <aside className="absolute right-0 top-0 flex h-full w-[320px] max-w-[85vw] translate-x-0 flex-col border-l border-slate-200 bg-white shadow-2xl transition-transform duration-200">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
          <h2 className="text-sm font-bold text-slate-900">Profile</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
            aria-label="Close"
          >
            <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Identity card */}
        <div className="flex flex-col items-center gap-3 border-b border-slate-200 px-5 py-6">
          <div className="flex h-16 w-16 items-center justify-center rounded-full bg-gradient-to-br from-slate-900 to-slate-700 text-lg font-bold text-teal-300 shadow-md">
            {initialsOf(user?.email ?? null)}
          </div>
          <div className="text-center">
            <p className="max-w-[240px] truncate text-sm font-semibold text-slate-900">
              {user?.email ?? "…"}
            </p>
            <span className="mt-1.5 inline-flex items-center gap-1 rounded-full border border-teal-200 bg-teal-50 px-2.5 py-0.5 text-[11px] font-semibold text-teal-700">
              {user?.provider === "google" ? (
                <>
                  <svg className="h-3 w-3" viewBox="0 0 24 24" aria-hidden="true">
                    <path fill="#EA4335" d="M12 5.04c1.62 0 3.06.56 4.2 1.64l3.12-3.12C17.46 1.8 14.96.75 12 .75 7.62.75 3.84 3.22 2.04 6.8l3.66 2.84C6.54 7.14 9.03 5.04 12 5.04z" />
                    <path fill="#4285F4" d="M23.25 12.27c0-.79-.07-1.54-.2-2.27H12v4.51h6.3c-.27 1.48-1.11 2.73-2.36 3.57l3.62 2.81c2.12-1.96 3.69-4.85 3.69-8.62z" />
                    <path fill="#FBBC05" d="M5.7 14.36a7.2 7.2 0 0 1 0-4.72L2.04 6.8a11.26 11.26 0 0 0 0 10.4l3.66-2.84z" />
                    <path fill="#34A853" d="M12 23.25c3.04 0 5.6-1 7.46-2.72l-3.62-2.81c-1 .68-2.29 1.08-3.84 1.08-2.97 0-5.46-2.1-6.3-4.9l-3.66 2.84c1.8 3.58 5.58 6.51 9.96 6.51z" />
                  </svg>
                  Google account
                </>
              ) : (
                "Email & password"
              )}
            </span>
          </div>
        </div>

        {/* Account details */}
        <dl className="space-y-4 px-5 py-5 text-xs">
          <div>
            <dt className="font-semibold uppercase tracking-wide text-slate-400">Email</dt>
            <dd className="mt-0.5 break-all font-medium text-slate-800">{user?.email ?? "—"}</dd>
          </div>
          <div>
            <dt className="font-semibold uppercase tracking-wide text-slate-400">Member since</dt>
            <dd className="mt-0.5 font-medium text-slate-800">{formatDate(user?.createdAt ?? null)}</dd>
          </div>
          <div>
            <dt className="font-semibold uppercase tracking-wide text-slate-400">Last sign-in</dt>
            <dd className="mt-0.5 font-medium text-slate-800">{formatDate(user?.lastSignInAt ?? null)}</dd>
          </div>
          <div>
            <dt className="font-semibold uppercase tracking-wide text-slate-400">Data privacy</dt>
            <dd className="mt-0.5 font-medium text-slate-800">
              Diagnosis runs and Ask history are saved only for this account.
            </dd>
          </div>
        </dl>

        {/* Footer */}
        <div className="mt-auto space-y-2 border-t border-slate-200 px-5 py-4">
          <button
            type="button"
            disabled={signingOut}
            onClick={async () => {
              setSigningOut(true);
              await onSignOut();
              setSigningOut(false);
            }}
            className="w-full rounded-lg border border-rose-200 bg-rose-50 py-2.5 text-sm font-semibold text-rose-700 transition hover:bg-rose-100 disabled:opacity-50"
          >
            {signingOut ? "Signing out…" : "Sign out"}
          </button>
          <p className="text-center text-[11px] text-slate-400">
            Secured by Supabase Auth · tokens verified in the backend
          </p>
        </div>
      </aside>
    </div>
  );
}
