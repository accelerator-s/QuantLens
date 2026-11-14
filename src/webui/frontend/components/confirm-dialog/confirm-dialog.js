




export async function runWithConfirm({
  execute,
  title = "确认操作",
  message = "",
  requireDecidedBy = false,
  notePlaceholder = "",
  confirmLabel = "确认执行",
  danger = false,
  showPreview = true,
}) {
  const first = await execute({ confirm: false });
  if (!first || first.requires_confirm !== true) return first;
  const answer = await confirmDialog({
    title,
    message,
    preview: showPreview ? first.preview ?? null : null,
    requireDecidedBy,
    notePlaceholder,
    confirmLabel,
    danger,
  });
  if (!answer) return null;
  const extra = { confirm: true };
  if (answer.decidedBy) extra.decided_by = answer.decidedBy;
  if (answer.note) extra.note = answer.note;
  return execute(extra);
}

function previewNode(preview) {
  if (preview == null) return null;
  if (typeof preview === "string") {
    const pre = document.createElement("pre");
    pre.className = "codeblock";
    pre.textContent = preview;
    return pre;
  }
  const values = Object.values(preview);
  const flat = values.every((v) => v == null || ["string", "number", "boolean"].includes(typeof v));
  if (flat) {
    const dl = document.createElement("dl");
    dl.className = "kv";
    for (const [key, value] of Object.entries(preview)) {
      const dt = document.createElement("dt");
      dt.textContent = key;
      const dd = document.createElement("dd");
      dd.textContent = value == null ? "—" : String(value);
      dl.append(dt, dd);
    }
    return dl;
  }
  const pre = document.createElement("pre");
  pre.className = "codeblock";
  pre.textContent = JSON.stringify(preview, null, 2);
  return pre;
}

export function confirmDialog({
  title = "确认操作",
  message = "",
  preview = null,
  requireDecidedBy = false,
  notePlaceholder = "",
  confirmLabel = "确认执行",
  danger = false,
} = {}) {
  return new Promise((resolve) => {
    const backdrop = document.createElement("div");
    backdrop.className = "dialog-backdrop";
    const dialog = document.createElement("div");
    dialog.className = "dialog";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    backdrop.append(dialog);

    const h = document.createElement("h3");
    h.className = "dialog__title";
    h.textContent = title;
    dialog.append(h);

    const body = document.createElement("div");
    body.className = "dialog__body";
    if (message) {
      const p = document.createElement("p");
      p.className = "confirm__message";
      p.textContent = message;
      body.append(p);
    }
    const node = previewNode(preview);
    if (node) body.append(node);

    let byInput = null;
    if (requireDecidedBy) {
      const field = document.createElement("label");
      field.className = "confirm__field";
      const span = document.createElement("span");
      span.textContent = "决策人（必填）";
      byInput = document.createElement("input");
      byInput.className = "input";
      byInput.placeholder = "你的名字";
      field.append(span, byInput);
      body.append(field);
    }

    let noteInput = null;
    if (notePlaceholder) {
      const field = document.createElement("label");
      field.className = "confirm__field";
      const span = document.createElement("span");
      span.textContent = "备注（可选）";
      noteInput = document.createElement("input");
      noteInput.className = "input";
      noteInput.placeholder = notePlaceholder;
      field.append(span, noteInput);
      body.append(field);
    }
    dialog.append(body);

    const actions = document.createElement("div");
    actions.className = "dialog__actions";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "btn btn--ghost";
    cancel.textContent = "取消";
    const ok = document.createElement("button");
    ok.type = "button";
    ok.className = danger ? "btn btn--danger" : "btn btn--primary";
    ok.textContent = confirmLabel;
    actions.append(cancel, ok);
    dialog.append(actions);

    function close(result) {
      backdrop.remove();
      document.removeEventListener("keydown", onKey);
      resolve(result);
    }
    function submit() {
      const decidedBy = byInput ? byInput.value.trim() : "";
      if (requireDecidedBy && !decidedBy) {
        byInput.classList.add("is-invalid");
        byInput.focus();
        return;
      }
      close({ decidedBy, note: noteInput ? noteInput.value.trim() : "" });
    }
    function onKey(ev) {
      if (ev.key === "Escape") close(null);
      if (ev.key === "Enter" && ev.target !== cancel) submit();
    }

    cancel.addEventListener("click", () => close(null));
    ok.addEventListener("click", submit);
    backdrop.addEventListener("click", (ev) => {
      if (ev.target === backdrop) close(null);
    });
    document.addEventListener("keydown", onKey);

    document.body.append(backdrop);
    (byInput || ok).focus();
  });
}
