// Chat responses bypass the JSON request cache: cloning/reading a streaming
// response there would buffer the entire reply before the first visible token.
export async function requestChat(path, payload, { onDelta, signal } = {}) {
  const response = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload), signal,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(data?.detail?.message || `请求失败 (${response.status})`);
  }
  const reader = response.body.getReader(), decoder = new TextDecoder();
  const content = [], reasoning = [];
  let buffer = "", model = "", finished = false;
  function consume(line) {
    if (!line.trim()) return;
    const event = JSON.parse(line);
    if (event.type === "error") throw new Error(event.message || "回复中断，请重试。");
    if (event.type === "start") model = event.model || "";
    if (event.type === "done") finished = true;
    if (event.type === "delta" && typeof event.text === "string") {
      if (event.channel === "reasoning") reasoning.push(event.text);
      else if (event.channel === "content") content.push(event.text);
      else return;
      onDelta?.(event);
    }
  }
  try {
    while (!finished) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let newline;
      while ((newline = buffer.indexOf("\n")) >= 0) {
        consume(buffer.slice(0, newline));
        buffer = buffer.slice(newline + 1);
      }
      if (done) { consume(buffer); break; }
    }
    if (!finished) throw new Error("回复连接意外断开，请重试。");
    return { reply: content.join(""), reasoning: reasoning.join(""), model };
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
