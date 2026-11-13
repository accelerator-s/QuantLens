


let host = null;

export function initToast(bus) {
  host = document.createElement("div");
  host.className = "toasts";
  document.body.append(host);
  bus.on("toast", show);
}

export function show({ kind = "info", title = "", detail = "", timeout = 3000 } = {}) {
  if (!host) return;
  const el = document.createElement("div");
  el.className = `toast${kind === "error" ? " toast--error" : kind === "success" ? " toast--success" : ""}`;
  if (title) {
    const t = document.createElement("div");
    t.className = "toast__title";
    t.textContent = title;
    el.append(t);
  }
  if (detail) {
    const d = document.createElement("div");
    d.className = "toast__detail";
    d.textContent = detail;
    el.append(d);
  }
  host.append(el);
  setTimeout(() => {
    el.classList.add("is-leaving");
    setTimeout(() => el.remove(), 180);
  }, timeout);
}


export function toastError(bus, error, title = "操作失败") {
  const parts = [error?.message || String(error)];
  if (error?.suggestedAction) parts.push(`建议：${error.suggestedAction}`);
  bus.emit("toast", { kind: "error", title, detail: parts.join(" "), timeout: 4500 });
}
