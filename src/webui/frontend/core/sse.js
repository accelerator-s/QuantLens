


export async function streamSSE(url, { method = "GET", body, signal, onEvent } = {}) {
  const opts = { method, signal, headers: { Accept: "text/event-stream" } };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  if (!res.ok || !res.body) {
    let detail = null;
    try {
      detail = (await res.json())?.detail;
    } catch {
      detail = null;
    }
    const message =
      (detail && typeof detail === "object" && detail.message) ||
      (typeof detail === "string" && detail) ||
      `流式请求失败 (${res.status})`;
    const error = new Error(message);
    error.status = res.status;
    if (detail && typeof detail === "object") {
      error.code = detail.code || "";
      error.suggestedAction = detail.suggested_action || "";
    }
    throw error;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const dispatch = (frame) => {
    let event = "message";
    const dataLines = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
    }
    if (!dataLines.length) return;
    const raw = dataLines.join("\n");
    let data = raw;
    try {
      data = JSON.parse(raw);
    } catch {
      data = raw;
    }
    onEvent?.(event, data);
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, idx).replace(/\r/g, "");
      buffer = buffer.slice(idx + 2);
      if (frame.trim()) dispatch(frame);
    }
  }
  if (buffer.trim()) dispatch(buffer.replace(/\r/g, ""));
}
