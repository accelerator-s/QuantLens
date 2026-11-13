import { codeEl, el } from "./dom.js";

export function captureLogScroll(log) {
  const nodes = new Set(log?.querySelectorAll('pre') || []);
  for (let node = log; node; node = node.parentElement) nodes.add(node);
  const positions = [...nodes].map(node => [node, node.scrollTop, node.scrollLeft]);
  return () => {
    for (const [node, top, left] of positions) {
      if (node.isConnected) {
        node.scrollTop = top;
        node.scrollLeft = left;
      }
    }
  };
}

export function updateTaskLog(
  existing,
  lines,
  { limit = 24, subject = "", className = "" } = {},
) {
  const items = Array.isArray(lines) ? lines : [];
  if (!items.length) return null;
  let details = existing;
  if (!details || (subject && details.dataset.subject !== subject)) {
    details = el("details", ["task-log", className].filter(Boolean).join(" "));
    details.dataset.taskLog = "";
    details.dataset.subject = subject;
    details.append(el("summary"), codeEl(""));
  } else {
    details.className = ["task-log", className].filter(Boolean).join(" ");
  }
  const visible = items.slice(-limit);
  details.querySelector("summary").textContent = `运行日志（最近 ${visible.length} 行）`;
  const content = visible.join("\n");
  const code = details.querySelector(".codeblock");
  if (code.textContent !== content) {
    const restoreScroll = captureLogScroll(details);
    code.textContent = content;
    restoreScroll();
  }
  return details;
}
