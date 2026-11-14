import { authHeaders } from "./supabase";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");

export function apiUrl(path: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${API_BASE_URL}${normalizedPath}`;
}

/**
 * fetch() with the Supabase Auth bearer token attached automatically.
 * The token lives in the auth project's session; nothing is verified or
 * stored here — the backend does the actual JWT verification (401 back
 * means "sign in again").
 */
export async function authedFetch(
  path: string,
  init: RequestInit = {}
): Promise<Response> {
  const headers = new Headers(init.headers);
  for (const [key, value] of Object.entries(await authHeaders())) {
    headers.set(key, value);
  }
  const response = await fetch(apiUrl(path), { ...init, headers });
  if (response.status === 401) {
    throw new Error("Your session has expired — please sign in again.");
  }
  return response;
}
