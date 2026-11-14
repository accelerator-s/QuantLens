import { labelText, displayText } from "./labels.js";
import { compactRunId } from "./fmt.js";
import { createPagination } from "./pagination.js";
export { selectMarkup } from "./select.js";

export function initButtonTextOffset() {
  const selector = 'button, [role="button"], .btn';
  function wrapText(node) {
    if (!node.textContent.trim() || !node.parentElement?.closest(selector)
      || node.parentElement.closest('.button-text-offset, svg, script, style')) return;
    const label = document.createElement('span');
    label.className = 'button-text-offset';
    node.replaceWith(label);
    label.append(node);
  }
  function scan(node) {
    if (node.nodeType === Node.TEXT_NODE) { wrapText(node); return; }
    if (!(node instanceof Element)) return;
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    const texts = [];
    while (walker.nextNode()) texts.push(walker.currentNode);
    texts.forEach(wrapText);
  }
  scan(document.body);
  new MutationObserver(records => {
    for (const record of records) {
      if (record.type === 'characterData') scan(record.target);
      else record.addedNodes.forEach(scan);
    }
  }).observe(document.body, {childList: true, characterData: true, subtree: true});
}

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


export function tableEl(columns, rows, { onRowClick, onRowRender, pagination = 'auto', pageSize = 10, page = 1, onPageChange } = {}) {
  pagination = pagination === 'auto' ? rows.length > pageSize : pagination;
  const panel = el("div", "table-panel");
  panel.tabIndex = -1;
  const wrap = el("div", "table-wrap");
  wrap.tabIndex = 0;
  wrap.setAttribute('aria-label', '数据表');
  const table = el("table", "table");
  const thead = el("thead");
  const headRow = el("tr");
  for (const col of columns) {
    const th = el("th", col.num ? "is-num" : "", labelText(col.label));
    th.scope = 'col';
    headRow.append(th);
  }
  thead.append(headRow);
  const tbody = el("tbody");
  function render(target = page, limit = pageSize) {
    pageSize = limit;
    page = Math.max(1, Math.min(Math.ceil(rows.length / limit) || 1, target));
    tbody.replaceChildren();
    const offset = pagination ? (page - 1) * pageSize : 0;
    for (const [index, row] of rows.slice(offset, pagination ? offset + pageSize : rows.length).entries()) {
      const tr = el("tr");
      if (onRowClick) {
        tr.classList.add("is-clickable");
        tr.tabIndex = 0;
        tr.addEventListener("click", () => onRowClick(row));
        tr.addEventListener('keydown', event => {
          if (event.target !== tr || !['Enter', ' '].includes(event.key)) return;
          event.preventDefault();
          onRowClick(row);
        });
      }
      for (const col of columns) {
        const td = el("td", col.num ? "is-num" : "");
        const raw = row[col.key];
        const value = col.format ? col.format(raw, row) : raw;
        if (value instanceof Node) td.append(value);
        else setDisplayValue(td, value === null || value === undefined || value === "" ? "—" : value);
        tr.append(td);
      }
      onRowRender?.(tr, row, offset + index);
      tbody.append(tr);
    }
    if (!rows.length) {
      const td = el('td', 'table__empty', '暂无记录');
      td.colSpan = columns.length;
      const tr = el('tr');
      tr.append(td);
      tbody.append(tr);
    }
    pager?.update({page, pageSize});
    wrap.scrollTop = 0;
    onPageChange?.(page, pageSize);
  }
  const pager = pagination ? createPagination({total: rows.length, page, pageSize, onChange: render}) : null;
  table.append(thead, tbody);
  wrap.append(table);
  panel.append(wrap);
  if (pager) panel.append(pager.node);
  render();
  panel.setPage = render;
  panel.pageState = () => ({page, pageSize});
  return panel;
}

// API-backed tables fetch only the selected page; successful pages are reused.
export function remoteTableEl(columns, loadPage, { pageSize = 10, initialData, onRowClick, onRowRender, onData } = {}) {
  const panel = el('div', 'table-panel');
  const host = el('div', 'table-panel__body');
  const status = el('div', 'table-panel__status');
  status.setAttribute('role', 'status');
  const retry = el('button', 'btn btn--ghost btn--sm', '重试');
  retry.type = 'button';
  retry.hidden = true;
  const cache = new Map();
  if (initialData) cache.set(`1:${pageSize}`, initialData);
  let request = 0, page = 1, total = initialData?.total_rows || 0, failedPage = 1, failedSize = pageSize;
  const pager = createPagination({total, pageSize, onChange: show});
  panel.append(host, status, retry, pager.node);
  async function show(target = 1, limit = pageSize) {
    const version = ++request;
    failedPage = target; failedSize = limit;
    retry.hidden = true;
    const key = `${target}:${limit}`;
    const cached = cache.get(key);
    status.textContent = cached ? '' : '正在读取…';
    panel.setAttribute('aria-busy', String(!cached));
    pager.update({busy: true});
    try {
      const data = cached || await loadPage({limit, offset: (target - 1) * limit});
      if (version !== request) return;
      total = Number(data.total_rows ?? data.items.length);
      const last = Math.max(1, Math.ceil(total / limit));
      if (target > last) return show(last, limit);
      cache.set(key, data);
      if (cache.size > 20) cache.delete(cache.keys().next().value);
      page = target; pageSize = limit;
      host.replaceChildren(tableEl(columns, data.items, {pagination: false, onRowClick, onRowRender}));
      status.textContent = '';
      onData?.(data);
    } catch (error) {
      if (version !== request) return;
      status.textContent = `读取失败：${error.message}`;
      retry.hidden = false;
    } finally {
      if (version === request) {
        panel.setAttribute('aria-busy', 'false');
        pager.update({total, page, pageSize, busy: false});
      }
    }
  }
  retry.addEventListener('click', () => show(failedPage, failedSize));
  panel.setPage = show;
  panel.pageState = () => ({page, pageSize});
  panel.reload = () => { cache.clear(); return show(page, pageSize); };
  panel.ready = show();
  return panel;
}

export function codeEl(text) {
  const node = el("pre", "codeblock", text ?? "");
  node.tabIndex = 0;
  return node;
}
