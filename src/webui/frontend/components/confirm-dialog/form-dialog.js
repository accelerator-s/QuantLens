


import { el, selectMarkup } from "../../core/dom.js";
import { createSelect } from "../../core/select.js";
import { createMultiSelect } from "../../core/multi-select.js";

// 弹窗高度自适应：






function fitDialog(dialog, body) {
  body.style.minHeight = "";
  body.style.maxHeight = "";
  body.style.overflowY = "";
  const budget = Math.min(window.innerHeight - 48, Math.floor(window.innerHeight * 0.84));
  const chrome = dialog.offsetHeight - body.offsetHeight; 
  const need = body.scrollHeight;
  const room = budget - chrome;
  if (need <= room) {
    body.style.minHeight = `${need}px`;
    body.style.overflowY = "visible";
  } else {
    body.style.maxHeight = `${room}px`;
    body.style.overflowY = "auto";
    body.querySelector(".select.is-open .select__list")?.scrollIntoView({ block: "nearest" });
  }
}

export function formDialog({ title = "填写表单", submitLabel = "提交", message = "", fields = [] }) {
  return new Promise((resolve) => {
    const backdrop = el("div", "dialog-backdrop");
    const dialog = el("div", "dialog dialog--wide");
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    backdrop.append(dialog);
    dialog.append(el("h3", "dialog__title", title));

    const body = el("div", "dialog__body");
    if (message) body.append(el("p", "confirm__message", message));
    const controls = {};

    for (const [fieldIndex, f] of fields.entries()) {
      if (f.kind === "checkbox") {
        const wrap = el("label", "confirm__field confirm__field--inline");
        const input = document.createElement("input");
        input.type = "checkbox";
        input.checked = Boolean(f.value);
        wrap.append(input, el("span", "", f.label));
        if (f.hint) wrap.append(el("span", "item-row__meta", f.hint));
        body.append(wrap);
        controls[f.key] = input;
        continue;
      }
      const usesThemedSelect = f.kind === "select" || f.kind === "multiselect";
      const wrap = el(usesThemedSelect ? "div" : "label", "confirm__field");
      const label = el("span", "", f.required ? `${f.label}（必填）` : f.label);
      label.id = `form-field-${fieldIndex}-label`;
      wrap.append(label);
      if (f.kind === "select") {
        const selectRoot = el("div", "select confirm__select");
        selectRoot.innerHTML = selectMarkup(f.placeholder || "请选择");
        const trigger = selectRoot.querySelector("[data-select-trigger]");
        trigger.setAttribute("aria-labelledby", label.id);
        const themedSelect = createSelect(selectRoot, {
          onChange: () => selectRoot.classList.remove("is-invalid"),
        });
        themedSelect.setOptions(
          f.options || [],
          f.value ?? "",
          f.placeholder || "请选择",
        );
        wrap.append(selectRoot);
        if (f.hint) wrap.append(el("span", "item-row__meta", f.hint));
        body.append(wrap);
        controls[f.key] = {
          get value() {
            return themedSelect.value;
          },
          focus() {
            trigger.focus();
          },
          markInvalid() {
            selectRoot.classList.add("is-invalid");
          },
        };
        continue;
      }
      if (f.kind === "multiselect") {
        const selectRoot = el("div", "select multi-select confirm__select");
        selectRoot.innerHTML = selectMarkup(f.placeholder || "请选择（可多选）");
        const trigger = selectRoot.querySelector("[data-select-trigger]");
        const list = selectRoot.querySelector("[data-select-list]");
        trigger.setAttribute("aria-labelledby", label.id);
        list.setAttribute("aria-multiselectable", "true");
        const themedSelect = createMultiSelect(selectRoot, {
          emptyLabel: f.placeholder || "请选择（可多选）",
          onChange: () => selectRoot.classList.remove("is-invalid"),
        });
        themedSelect.setOptions(f.options || [], Array.isArray(f.value) ? f.value : []);
        wrap.append(selectRoot);
        if (f.hint) wrap.append(el("span", "item-row__meta", f.hint));
        body.append(wrap);
        controls[f.key] = {
          get value() {
            return themedSelect.value;
          },
          focus() {
            themedSelect.focus();
          },
          markInvalid() {
            selectRoot.classList.add("is-invalid");
          },
        };
        continue;
      }
      let input;
      if (f.kind === "textarea") {
        input = document.createElement("textarea");
        input.rows = f.rows || 6;
      } else {
        input = document.createElement("input");
        input.type = f.kind || "text";
      }
      input.className = "input";
      if (f.placeholder) input.placeholder = f.placeholder;
      if (f.value !== undefined) input.value = f.value;
      wrap.append(input);
      if (f.hint) wrap.append(el("span", "item-row__meta", f.hint));
      body.append(wrap);
      controls[f.key] = input;
    }
    dialog.append(body);

    const actions = el("div", "dialog__actions");
    const cancel = el("button", "btn btn--ghost", "取消");
    cancel.type = "button";
    const ok = el("button", "btn btn--primary", submitLabel);
    ok.type = "button";
    actions.append(cancel, ok);
    dialog.append(actions);

    const refit = () => requestAnimationFrame(() => fitDialog(dialog, body));
    
    
    const observer = new MutationObserver(refit);
    observer.observe(body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["class"],
    });
    window.addEventListener("resize", refit);

    function close(result) {
      observer.disconnect();
      window.removeEventListener("resize", refit);
      backdrop.remove();
      document.removeEventListener("keydown", onKey);
      resolve(result);
    }
    function submit() {
      const values = {};
      for (const f of fields) {
        const input = controls[f.key];
        if (f.kind === "checkbox") {
          values[f.key] = input.checked;
          continue;
        }
        if (f.kind === "multiselect") {
          const val = input.value;
          const required = f.required || f.requiredWhen?.(values);
          if (required && !val.length) {
            input.markInvalid();
            input.focus();
            return;
          }
          values[f.key] = val;
          continue;
        }
        const val = String(input.value ?? "").trim();
        if (f.required && !val) {
          if (typeof input.markInvalid === "function") input.markInvalid();
          else input.classList.add("is-invalid");
          input.focus();
          return;
        }
        values[f.key] = val;
      }
      close(values);
    }
    function onKey(ev) {
      if (ev.key === "Escape") close(null);
    }
    cancel.addEventListener("click", () => close(null));
    ok.addEventListener("click", submit);
    backdrop.addEventListener("click", (ev) => {
      if (ev.target === backdrop) close(null);
    });
    document.addEventListener("keydown", onKey);
    document.body.append(backdrop);
    refit();
    const firstInput = Object.values(controls)[0];
    (firstInput || ok).focus();
  });
}
