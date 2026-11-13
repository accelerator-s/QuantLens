import { labelText, displayText } from "./labels.js";
import { compactRunId } from "./fmt.js";

function setDisplayValue(node, value) {
  const full = displayText(value);
  const compact = compactRunId(full);
  node.textContent = compact;
  if (compact !== full) {
    node.title = full;
    node.setAttribute("aria-label", full);
    node.classList.add("run-id");
  }
}


export function el(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== "") {
    if (["pre", "code", "textarea"].includes(tag)) node.textContent = displayText(text);
    else setDisplayValue(node, text);
  }
  return node;
}


export function kvEl(pairs) {
  const dl = el("dl", "kv");
  for (const pair of pairs) {
    const [label, value, options = {}] = pair;
    const dt = el("dt", "kv__key", labelText(label));
    const dd = el("dd", "kv__val");
    if (value instanceof Node) dd.append(value);
    else setDisplayValue(dd, value === null || value === undefined || value === "" ? "—" : value);
    const row = el("div", "kv__row");
    if (options.wide) row.classList.add("kv__row--wide");
    if (options.scroll) dd.classList.add("kv__val--scroll");
    row.append(dt, dd);
    dl.append(row);
  }
  return dl;
}


export function tableEl(columns, rows, { onRowClick } = {}) {
  const wrap = el("div", "table-wrap");
  wrap.tabIndex = 0;
  wrap.setAttribute('aria-label', '数据表');
  const table = el("table", "table");
  const thead = el("thead");
  const headRow = el("tr");
  for (const col of columns) {
    headRow.append(el("th", col.num ? "is-num" : "", labelText(col.label)));
  }
  thead.append(headRow);
  const tbody = el("tbody");
  for (const row of rows) {
    const tr = el("tr");
    if (onRowClick) {
      tr.classList.add("is-clickable");
      tr.addEventListener("click", () => onRowClick(row));
    }
    for (const col of columns) {
      const td = el("td", col.num ? "is-num" : "");
      const raw = row[col.key];
      const value = col.format ? col.format(raw, row) : raw;
      if (value instanceof Node) td.append(value);
      else setDisplayValue(td, value === null || value === undefined || value === "" ? "—" : value);
      tr.append(td);
    }
    tbody.append(tr);
  }
  table.append(thead, tbody);
  wrap.append(table);
  return wrap;
}

export function codeEl(text) {
  const node = el("pre", "codeblock", text ?? "");
  node.tabIndex = 0;
  return node;
}


export function selectMarkup(placeholder = "请选择") {
  return `
    <button type="button" class="select__trigger" data-select-trigger aria-haspopup="listbox" aria-expanded="false">
      <span data-select-value>${placeholder}</span>
      <span class="select__chevron">▾</span>
    </button>
    <div class="select__menu" data-select-list role="listbox" hidden></div>
  `;
}
