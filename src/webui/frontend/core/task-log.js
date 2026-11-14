import { codeEl, el } from "./dom.js";

const LOG_TIME = /^\s*(\[?\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?\]?)\s+/;

function logEntryLines(entry) {
  let timestamp = '[时间未记录]';
  return String(entry ?? '').split(/\r?\n/).filter(line => line.trim()).map(line => {
    if (line.startsWith('[时间未记录]')) return line;
    const existing = line.match(LOG_TIME);
    if (existing) {
      timestamp = existing[1];
      return line;
    }
    return `${timestamp}  ${line}`;
  });
}

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
  { subject = "", className = "" } = {},
) {
  const items = (Array.isArray(lines) ? lines : [])
    .map(logEntryLines)
    .filter(entry => entry.length);
  if (!items.length) return null;
  let details = existing;
  if (!details || (subject && details.dataset.subject !== subject)) {
    details = el("details", ["task-log", className].filter(Boolean).join(" "));
    details.dataset.taskLog = "";
    details.dataset.subject = subject;
    details.append(el("summary"), codeEl(""));
    observeLogSize(details);
    details.addEventListener('toggle', () => {
      if (details.open) fitLog(details, true);
    });
  } else {
    details.className = ["task-log", className].filter(Boolean).join(" ");
  }
  // Reverse entries, keeping multiline messages readable and the source array intact.
  const total = items.reduce((count, entry) => count + entry.length, 0);
  const visible = items.slice().reverse().flat().slice(0, 200);
  const summary = details.querySelector("summary");
  summary.textContent = total > visible.length
    ? `运行日志（${visible.length}/${total} 行）`
    : `运行日志（展示 ${visible.length} 行）`;
  summary.title = `展示最近 ${visible.length} 行，共 ${total} 行`;
  const content = visible.join("\n");
  const code = details.querySelector(".codeblock");
  if (code.textContent !== content) {
    code.textContent = content;
    fitLog(details, true);
  }
  return details;
}

// Measure after callers mount or move the disclosure. Only its own viewport scrolls.
function fitLog(details, follow = false) {
  requestAnimationFrame(() => {
    if (!details.isConnected || !details.open) return;
    const code = details.querySelector('.codeblock');
    const style = getComputedStyle(code);
    const lineHeight = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.5;
    const chrome = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom)
      + parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
    const viewport = window.visualViewport?.height || window.innerHeight;
    const minRows = viewport < 400 ? 4 : 6;
    let rows = Math.max(minRows, Math.min(16, Math.floor(viewport * .28 / lineHeight)));
    const top = code.getBoundingClientRect().top;
    const below = viewport - top - 16 - chrome;
    if (top >= 0 && below >= minRows * lineHeight) rows = Math.min(rows, Math.floor(below / lineHeight));
    code.style.setProperty('--task-log-max-height', `${Math.ceil(Math.min(rows * lineHeight + chrome, viewport * .45))}px`);
    if (follow) code.scrollTop = 0;
  });
}

let observingResize = false;
let logResizeObserver;
function observeLogSize(details) {
  if (!observingResize) {
    const resize = () => document.querySelectorAll('[data-task-log][open]').forEach(log => fitLog(log, true));
    window.addEventListener('resize', resize);
    window.visualViewport?.addEventListener('resize', resize);
    observingResize = true;
  }
  if (typeof ResizeObserver === 'undefined') return;
  logResizeObserver ||= new ResizeObserver(entries => {
    for (const {target, contentRect} of entries) {
      const log = target.closest('[data-task-log]');
      if (!log?.isConnected) { logResizeObserver.unobserve(target); continue; }
      const width = Math.round(contentRect.width);
      if (width > 0 && target.dataset.logWidth !== String(width)) {
        target.dataset.logWidth = String(width);
        fitLog(log, true);
      }
    }
  });
  logResizeObserver.observe(details.querySelector('.codeblock'));
}
