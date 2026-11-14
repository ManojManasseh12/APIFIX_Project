/**
 * Supabase Auth client (UI side only).
 *
 * Points at the separate auth project. This module never verifies tokens
 * and holds no secrets: the URL + anon key are designed to be public —
 * every security check happens in the FastAPI backend (app/core/auth.py),
 * which validates the JWT against the auth project's JWKS.
 *
 * When the VITE_SUPABASE_* variables are missing the client is null and
 * the app runs exactly as before (auth disabled, backend open).
 */

import { createClient } from "@supabase/supabase-js";

const AUTH_URL = (import.meta.env.VITE_SUPABASE_AUTH_URL ?? "").replace(/\/+$/, "");
const ANON_KEY = import.meta.env.VITE_SUPABASE_AUTH_ANON_KEY ?? "";

export const authConfigured = Boolean(AUTH_URL && ANON_KEY);

export const supabase = authConfigured
  ? createClient(AUTH_URL, ANON_KEY, {
      auth: {
        // Sessions survive reloads in localStorage and refresh themselves.
        persistSession: true,
        autoRefreshToken: true,
      },
    })
    : null;

/** The access token for API calls, or null when signed out / auth off. */
export async function getAccessToken(): Promise<string | null> {
  if (!supabase) return null;
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}

/** Authorization header map for fetch calls (empty when auth is off). */
export async function authHeaders(): Promise<Record<string, string>> {
  const token = await getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export type AuthUser = { email: string | null };

export async function signIn(email: string, password: string): Promise<string | null> {
  if (!supabase) return "Authentication is not configured.";
  const { error } = await supabase.auth.signInWithPassword({ email, password });
  return error ? error.message : null;
}

export async function signUp(email: string, password: string): Promise<string | null> {
  if (!supabase) return "Authentication is not configured.";
  const { error } = await supabase.auth.signUp({ email, password });
  return error ? error.message : null;
}

export async function signOut(): Promise<void> {
  if (!supabase) return;
  await supabase.auth.signOut();
}
