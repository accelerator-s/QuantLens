// Shared pager for local collections and API-backed tables.
import { createSelect, selectMarkup } from './select.js';

export function createPagination({ total = 0, page = 1, pageSize = 10, onChange }) {
  const node = document.createElement('nav');
  node.className = 'table-pagination';
  node.setAttribute('aria-label', '表格分页');
  node.innerHTML = `<span class="table-pagination__summary" role="status" aria-live="polite"></span>
    <div class="table-pagination__controls">
      <div class="table-pagination__size"><span>每页</span><div class="select" data-page-size>${selectMarkup()}</div><span>条</span></div>
      <div class="table-pagination__steps">
        <button type="button" class="btn btn--ghost btn--sm" data-page="first">首页</button>
        <button type="button" class="btn btn--ghost btn--sm" data-page="prev">上一页</button>
        <span class="table-pagination__current"></span>
        <button type="button" class="btn btn--ghost btn--sm" data-page="next">下一页</button>
        <button type="button" class="btn btn--ghost btn--sm" data-page="last">末页</button>
      </div>
      <form class="table-pagination__jump"><label>跳至 <input type="number" min="1" step="1" inputmode="numeric" aria-label="跳转页码" required> 页</label><button type="submit" class="btn btn--ghost btn--sm">跳转</button></form>
    </div>`;
  const sizeRoot = node.querySelector('[data-page-size]');
  sizeRoot.querySelector('[data-select-trigger]').setAttribute('aria-label', '每页条数');
  sizeRoot.querySelector('[data-select-list]').setAttribute('aria-label', '每页条数');
  const size = createSelect(sizeRoot, {
    onChange: value => change(1, Number(value)),
    onOpen: () => {
      const rect = sizeRoot.getBoundingClientRect();
      const below = window.innerHeight - rect.bottom;
      const above = rect.top > below;
      sizeRoot.classList.toggle('select--above', above);
      sizeRoot.querySelector('[data-select-list]').style.maxHeight = `${Math.min(240, Math.max(48, (above ? rect.top : below) - 8))}px`;
    },
  });
  const input = node.querySelector('input');
  let busy = false;
  const pages = () => Math.max(1, Math.ceil(total / pageSize));
  function update(next = {}) {
    total = Math.max(0, Number(next.total ?? total) || 0);
    pageSize = next.pageSize ?? pageSize;
    page = Math.max(1, Math.min(pages(), next.page ?? page));
    busy = next.busy ?? busy;
    node.hidden = total <= 10;
    const start = total ? (page - 1) * pageSize + 1 : 0;
    node.querySelector('.table-pagination__summary').textContent = `共 ${total} 条 · ${start}–${Math.min(page * pageSize, total)} 条`;
    node.querySelector('.table-pagination__current').textContent = `${page} / ${pages()} 页`;
    input.max = String(pages());
    input.value = String(page);
    size.setOptions([...new Set([10, 20, 50, 100, pageSize])].sort((a, b) => a - b)
      .map(value => ({value: String(value), label: String(value)})), String(pageSize));
    if (busy) size.close();
    node.querySelectorAll('button, input').forEach(control => { control.disabled = busy; });
    for (const action of ['first', 'prev']) node.querySelector(`[data-page="${action}"]`).disabled = busy || page === 1;
    for (const action of ['next', 'last']) node.querySelector(`[data-page="${action}"]`).disabled = busy || page === pages();
  }
  function change(target, limit = pageSize) {
    if (busy || !Number.isFinite(target)) return;
    target = Math.max(1, Math.min(Math.max(1, Math.ceil(total / limit)), Math.trunc(target)));
    if (target !== page || limit !== pageSize) onChange(target, limit);
  }
  node.querySelectorAll('[data-page]').forEach(button => button.addEventListener('click', () => {
    change(({ first: 1, prev: page - 1, next: page + 1, last: pages() })[button.dataset.page]);
  }));
  node.querySelector('form').addEventListener('submit', event => { event.preventDefault(); change(Number(input.value)); });
  update();
  return { node, update };
}

// String-rendered Lens tables keep their existing cell handlers when paged.
export function initMarkupTablePagination(root) {
  function scan() {
    root.querySelectorAll('.ql-table-wrap:not([data-paginated])').forEach(wrap => {
      wrap.dataset.paginated = 'true';
      const tbody = wrap.querySelector('tbody');
      if (!tbody) return;
      const rows = [...tbody.children];
      if (rows.length <= 10) return;
      const panel = document.createElement('div');
      panel.className = 'table-panel';
      wrap.replaceWith(panel);
      panel.append(wrap);
      const pager = createPagination({total: rows.length, onChange: render});
      function render(page = 1, pageSize = 10) {
        tbody.replaceChildren(...rows.slice((page - 1) * pageSize, page * pageSize));
        wrap.scrollTop = 0;
        pager.update({page, pageSize});
      }
      panel.append(pager.node);
      render();
    });
  }
  new MutationObserver(scan).observe(root, {childList: true, subtree: true});
  scan();
}
