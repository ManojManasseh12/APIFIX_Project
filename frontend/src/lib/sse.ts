import { apiUrl } from "./api";
import { getAccessToken } from "./supabase";

export async function streamSSE(
  url: string,
  body: unknown,
  onEvent: (event: string, data: any) => void,
  signal?: AbortSignal
) {
  // Attach the Supabase Auth token; the backend verifies it before the
  // stream starts. An expired session surfaces as a 401, not a stream error.
  const token = await getAccessToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  const response = await fetch(apiUrl(url), {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok || !response.body) {
    if (response.status === 401) {
      throw new Error("Your session has expired — please sign in again.");
    }
    throw new Error(`Stream failed: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";

      for (const frame of frames) {
        let event = "message";
        let data = "";
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (!data) continue;
        try {
          onEvent(event, JSON.parse(data));
        } catch {
          // A malformed frame must not kill the whole stream; skip it.
          console.error(`Discarded malformed SSE ${event} frame`, data);
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export async function streamDiagnosis(
  body: unknown,
  onEvent: (event: string, data: any) => void,
  signal?: AbortSignal
) {
  return streamSSE("/api/diagnose", body, onEvent, signal);
}

export async function streamAsk(
  body: unknown,
  onEvent: (event: string, data: any) => void,
  signal?: AbortSignal
) {
  return streamSSE("/api/ask", body, onEvent, signal);
}
