import { codeEl, el } from "./dom.js";


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
  if (code.textContent !== content) code.textContent = content;
  return details;
}
