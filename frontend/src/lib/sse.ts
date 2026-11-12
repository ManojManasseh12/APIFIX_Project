import { apiUrl } from "./api";

export async function streamSSE(
  url: string,
  body: unknown,
  onEvent: (event: string, data: any) => void,
  signal?: AbortSignal
) {
  const response = await fetch(apiUrl(url), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok || !response.body) {
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
