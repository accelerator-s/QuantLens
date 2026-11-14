// The spinner follows trading lots; existing holdings may contain dividend odd lots.
export function setupShareQuantity(controls, rule, { holding = false } = {}) {
  const input = controls.qty;
  const code = controls.instrument_id;
  const wrapper = document.createElement("span");
  wrapper.className = "portfolio__quantity";
  input.replaceWith(wrapper);
  wrapper.append(input);
  input.inputMode = "numeric";
  input.required = true;
  let minimum = 100;
  let increment = 100;
  const buttons = [];

  function update() {
    const stock = code.value.trim();
    const board = /^(688|689)/.test(stock) ? "科创板"
      : /^30/.test(stock) ? "创业板"
        : /^(43|83|87|92)/.test(stock) ? "北交所" : "主板";
    const quantity = rule.quantity_rules?.find((item) => item.board === board);
    minimum = quantity?.min_unit ?? rule.min_unit;
    increment = quantity?.increment || minimum;
    const buy = !holding && controls.action.value === "buy";
    input.min = holding ? "0" : buy ? String(minimum) : "1";
    input.step = buy ? String(increment) : "1";
    input.classList.remove("is-invalid");
    for (const [button, direction] of buttons) {
      button.title = `${direction > 0 ? "增加" : "减少"} ${increment} 股`;
    }
  }

  function move(direction) {
    const value = input.valueAsNumber;
    const floor = Number(input.min);
    const buy = !holding && controls.action.value === "buy";
    let next;
    if (!Number.isFinite(value)) next = direction > 0 ? minimum : floor;
    else if (buy) {
      const index = (value - minimum) / increment;
      next = minimum + (direction > 0 ? Math.floor(index) + 1 : Math.ceil(index) - 1) * increment;
    } else if (value === 0 && direction > 0) next = minimum;
    else next = Math.trunc(value) + direction * increment;
    input.value = String(Math.max(floor, next));
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
  }

  for (const [direction, symbol, label] of [[1, "▴", "增加股数"], [-1, "▾", "减少股数"]]) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `portfolio__quantity-${direction > 0 ? "up" : "down"}`;
    button.textContent = symbol;
    button.setAttribute("aria-label", label);
    button.addEventListener("click", () => move(direction));
    wrapper.append(button);
    buttons.push([button, direction]);
  }
  input.addEventListener("keydown", (event) => {
    if (!["ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    move(event.key === "ArrowUp" ? 1 : -1);
  });
  input.addEventListener("wheel", (event) => {
    if (document.activeElement !== input || event.deltaY === 0) return;
    event.preventDefault();
    move(event.deltaY < 0 ? 1 : -1);
  }, { passive: false });
  input.addEventListener("input", () => input.classList.remove("is-invalid"));
  code.addEventListener("input", update);
  code.addEventListener("change", update);
  code.closest(".dialog__body").addEventListener("change", (event) => {
    if (event.target.name === "action") update();
  });
  update();
}
