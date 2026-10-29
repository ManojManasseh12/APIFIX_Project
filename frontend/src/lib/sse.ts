export async function streamSSE(
  url: string,
  body: unknown,
  onEvent: (event: string, data: any) => void
) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!response.ok || !response.body) {
    throw new Error(`Stream failed: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

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
      if (data) onEvent(event, JSON.parse(data));
    }
  }
}

export async function streamDiagnosis(
  body: unknown,
  onEvent: (event: string, data: any) => void
) {
  return streamSSE("/api/diagnose", body, onEvent);
}

export async function streamAsk(
  body: unknown,
  onEvent: (event: string, data: any) => void
) {
  return streamSSE("/api/ask", body, onEvent);
}
