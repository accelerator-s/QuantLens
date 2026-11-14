import { labelText } from "./labels.js";

export function compactRunId(value) {
  const text = String(value ?? "");
  return /^BT-[A-Za-z0-9._-]{13,}$/.test(text) ? `${text.slice(0, 9)}…${text.slice(-4)}` : text;
}



export function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}

export function fmtLogTime(iso) {
  const date = iso ? new Date(iso) : null;
  if (!date || Number.isNaN(date.getTime())) return '时间未记录';
  return new Intl.DateTimeFormat('sv-SE', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  }).format(date) + '+08:00';
}

export function fmtDate(iso) {
  if (!iso) return "—";
  const s = String(iso);
  return s.length >= 10 ? s.slice(0, 10) : s;
}


export function fmtNum(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (Number.isNaN(n)) return String(value);
  return n.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function fmtPct(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (Number.isNaN(n)) return String(value);
  return `${(n * 100).toFixed(digits)}%`;
}


export function signClass(value) {
  const n = Number(value);
  if (Number.isNaN(n) || n === 0) return "";
  return n > 0 ? "is-positive" : "is-negative";
}


const BADGE_TONES = {
  open: "accent",
  in_review: "warn",
  accepted: "safe",
  rejected: "danger",
  solved: "muted",
  draft: "warn",
  active: "safe",
  disabled: "muted",
  retired: "muted",
  pending: "warn",
  approved: "safe",
  committed: "safe",
  discarded: "muted",
  superseded: "muted",
  running: "accent",
  completed: "safe",
  failed: "danger",
  fresh: "safe",
  stale: "warn",
  missing: "danger",
  high: "danger",
  medium: "warn",
  low: "muted",
};

export function badge(text, tone) {
  const el = document.createElement("span");
  const t = tone || BADGE_TONES[String(text).toLowerCase()] || "muted";
  el.className = `badge badge--${t}`;
  el.textContent = labelText(text);
  return el;
}

export function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}
