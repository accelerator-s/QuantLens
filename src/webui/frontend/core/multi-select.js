import { labelText } from "./labels.js";

export function createMultiSelect(
  root,
  { emptyLabel = "请选择（可多选）", onChange } = {},
) {
  const trigger = root.querySelector("[data-select-trigger]");
  const valueEl = root.querySelector("[data-select-value]");
  const list = root.querySelector("[data-select-list]");
  let options = [];
  let selected = new Set();
  let activeIndex = -1;

  function selectedValues() {
    return options.filter((item) => selected.has(item.value)).map((item) => item.value);
  }

  function refreshSummary() {
    const labels = options.filter((item) => selected.has(item.value)).map((item) => item.label);
    if (!labels.length) {
      valueEl.textContent = emptyLabel;
      trigger.removeAttribute("title");
      return;
    }
    valueEl.textContent = labels.join("、");
    trigger.title = labels.join("、");
  }

  function refreshOptions() {
    list.querySelectorAll("[role=option]").forEach((node, index) => {
      const checked = selected.has(node.dataset.value);
      node.setAttribute("aria-selected", String(checked));
      node.classList.toggle("is-active", index === activeIndex);
      const mark = node.querySelector("[data-multi-mark]");
      if (mark) mark.textContent = checked ? "✓" : "";
    });
  }

  function open() {
    if (!options.length) return;
    root.classList.add("is-open");
    list.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    refreshOptions();
  }

  function close() {
    root.classList.remove("is-open");
    list.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    activeIndex = -1;
    refreshOptions();
  }

  function toggle(option) {
    if (selected.has(option.value)) selected.delete(option.value);
    else selected.add(option.value);
    refreshOptions();
    refreshSummary();
    onChange?.(selectedValues());
  }

  function move(delta) {
    if (!options.length) return;
    open();
    activeIndex = activeIndex < 0
      ? (delta > 0 ? 0 : options.length - 1)
      : (activeIndex + delta + options.length) % options.length;
    refreshOptions();
    list.children[activeIndex]?.scrollIntoView({ block: "nearest" });
  }

  function setOptions(next, preferred = []) {
    options = (next || []).map((item) =>
      typeof item === "string" ? { value: item, label: labelText(item) } : item,
    );
    const valid = new Set(options.map((item) => item.value));
    selected = new Set((preferred || []).filter((value) => valid.has(value)));
    activeIndex = -1;
    list.innerHTML = "";
    for (const item of options) {
      const option = document.createElement("button");
      option.type = "button";
      option.className = "select__option multi-select__option";
      option.dataset.value = item.value;
      option.setAttribute("role", "option");
      const mark = document.createElement("span");
      mark.className = "multi-select__mark";
      mark.dataset.multiMark = "";
      mark.setAttribute("aria-hidden", "true");
      const label = document.createElement("span");
      label.textContent = item.label;
      option.append(mark, label);
      option.addEventListener("click", () => toggle(item));
      list.append(option);
    }
    refreshOptions();
    refreshSummary();
    if (!options.length) close();
  }

  trigger.addEventListener("click", () => {
    if (root.classList.contains("is-open")) close();
    else open();
  });
  trigger.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      move(event.key === "ArrowDown" ? 1 : -1);
    } else if (
      (event.key === "Enter" || event.key === " ")
      && root.classList.contains("is-open")
      && activeIndex >= 0
    ) {
      event.preventDefault();
      toggle(options[activeIndex]);
    } else if (event.key === "Escape") {
      close();
    }
  });
  document.addEventListener("click", (event) => {
    if (!root.contains(event.target)) close();
  });

  return {
    get value() {
      return selectedValues();
    },
    setOptions,
    focus() {
      trigger.focus();
    },
  };
}
