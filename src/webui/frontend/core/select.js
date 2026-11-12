import { labelText } from "./labels.js";


export function createSelect(root, { emptyLabel = "（无可选项）", onChange } = {}) {
  const trigger = root.querySelector("[data-select-trigger]");
  const valueEl = root.querySelector("[data-select-value]");
  const list = root.querySelector("[data-select-list]");
  let options = [];
  let selected = "";
  let activeIndex = -1;

  function open() {
    if (!options.length) return;
    root.classList.add("is-open");
    list.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    activeIndex = Math.max(0, options.findIndex((item) => item.value === selected));
    refreshActive();
  }

  function close() {
    root.classList.remove("is-open");
    list.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
  }

  function choose(option) {
    const changed = option.value !== selected;
    selected = option.value;
    valueEl.textContent = option.label;
    list.querySelectorAll("[role=option]").forEach((item) => {
      item.setAttribute("aria-selected", String(item.dataset.value === selected));
    });
    close();
    trigger.focus();
    if (changed) onChange?.(selected);
  }

  function refreshActive() {
    list.querySelectorAll("[role=option]").forEach((item, index) => {
      item.classList.toggle("is-active", index === activeIndex);
    });
  }

  function move(delta) {
    if (!options.length) return;
    open();
    activeIndex = (activeIndex + delta + options.length) % options.length;
    refreshActive();
    list.children[activeIndex]?.scrollIntoView({ block: "nearest" });
  }

  function setOptions(next, preferred = "", placeholder = emptyLabel) {
    options = next.map((item) =>
      typeof item === "string" ? { value: item, label: labelText(item) } : item,
    );
    list.innerHTML = "";
    if (!options.length) {
      selected = preferred || "";
      valueEl.textContent = preferred || placeholder;
      close();
      return;
    }
    const current = options.find((item) => item.value === preferred) || options[0];
    selected = current.value;
    for (const item of options) {
      const option = document.createElement("button");
      option.type = "button";
      option.className = "select__option";
      option.dataset.value = item.value;
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", String(item.value === selected));
      option.textContent = item.label;
      option.addEventListener("click", () => choose(item));
      list.append(option);
    }
    valueEl.textContent = current.label;
  }

  trigger.addEventListener("click", () => {
    if (root.classList.contains("is-open")) close();
    else open();
  });
  trigger.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      move(event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "Enter" && root.classList.contains("is-open")) {
      event.preventDefault();
      choose(options[activeIndex]);
    } else if (event.key === "Escape") {
      close();
    }
  });
  document.addEventListener("click", (event) => {
    if (!root.contains(event.target)) close();
  });

  return {
    get value() {
      return selected;
    },
    setOptions,
  };
}
