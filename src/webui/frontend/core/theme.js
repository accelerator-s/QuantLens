


const KEY = "quant-theme";
const listeners = new Set();

function systemPref() {
  return window.matchMedia &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export function currentTheme() {
  return document.documentElement.dataset.theme || "light";
}

function apply(theme) {
  document.documentElement.dataset.theme = theme;
  listeners.forEach((fn) => fn(theme));
}

export function initTheme() {
  let stored = null;
  try {
    stored = localStorage.getItem(KEY);
  } catch {
    stored = null;
  }
  apply(stored || systemPref());
}

export function toggleTheme() {
  const next = currentTheme() === "dark" ? "light" : "dark";
  try {
    localStorage.setItem(KEY, next);
  } catch {
    /* 浏览器可能禁用本地存储 */
  }
  apply(next);
  return next;
}

export function onThemeChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
