import { api } from './api.js';
import { pageScale } from './layout.js';

let nextId = 0;
const mounted = new WeakMap();

export function attachStockPicker(input, { search = (query) => api.stockSuggestions(query) } = {}) {
  if (mounted.has(input)) return mounted.get(input);
  const wrapper = document.createElement('div');
  wrapper.className = 'stock-picker';
  input.before(wrapper);
  wrapper.append(input);
  const menu = document.createElement('div');
  menu.className = 'stock-picker__menu';
  menu.id = `stock-candidates-${++nextId}`;
  menu.setAttribute('role', 'listbox');
  menu.setAttribute('aria-label', '股票候选');
  menu.hidden = true;
  document.body.append(menu);
  input.type = 'text';
  input.autocomplete = 'off';
  input.spellcheck = false;
  input.pattern = '[0-9]{6}';
  input.setAttribute('role', 'combobox');
  input.setAttribute('aria-autocomplete', 'list');
  input.setAttribute('aria-controls', menu.id);
  input.setAttribute('aria-expanded', 'false');
  let items = [];
  let active = -1;
  let focused = false;
  let destroyed = false;
  let sequence = 0;
  let pending = false;
  let loadingTimer;

  function stopLoading() {
    clearTimeout(loadingTimer);
    pending = false;
    menu.removeAttribute('aria-busy');
  }

  function close() {
    stopLoading();
    menu.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
  }
  function position() {
    if (menu.hidden) return;
    const scale = pageScale();
    const bounds = input.getBoundingClientRect();
    const rect = Object.fromEntries(['top', 'bottom', 'left', 'width'].map(key => [key, bounds[key] / scale]));
    const viewportWidth = innerWidth / scale, viewportHeight = innerHeight / scale;
    if (!input.isConnected || rect.bottom < 0 || rect.top > viewportHeight) { close(); return; }
    const below = viewportHeight - rect.bottom - 12;
    const above = rect.top - 12;
    const upwards = below < 180 && above > below;
    const height = Math.max(40, Math.min(240, upwards ? above : below));
    menu.style.maxHeight = `${height}px`;
    menu.style.width = `${Math.min(rect.width, viewportWidth - 16)}px`;
    menu.style.left = `${Math.max(8, Math.min(rect.left, viewportWidth - menu.offsetWidth - 8))}px`;
    menu.style.top = `${upwards ? Math.max(8, rect.top - menu.offsetHeight - 4) : rect.bottom + 4}px`;
  }
  function open() {
    if (!focused || destroyed) return;
    menu.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    position();
  }
  function message(text) {
    const row = document.createElement('div');
    row.className = 'stock-picker__message';
    row.textContent = text;
    menu.replaceChildren(row);
    input.removeAttribute('aria-activedescendant');
    open();
  }
  function highlight(scroll = false) {
    [...menu.querySelectorAll('[role=option]')].forEach((option, index) => {
      option.classList.toggle('is-active', index === active);
      option.setAttribute('aria-selected', String(index === active));
    });
    const option = menu.querySelectorAll('[role=option]')[active];
    if (option) {
      input.setAttribute('aria-activedescendant', option.id);
      if (scroll) option.scrollIntoView({ block: 'nearest' });
    }
  }
  function choose(item) {
    if (pending || !item) return;
    ++sequence;
    input.value = item.code;
    input.classList.remove('is-invalid');
    input.dispatchEvent(new Event('change', { bubbles: true }));
    close();
  }
  function render() {
    if (!items.length) { message('暂无匹配的股票'); return; }
    const existing = new Map([...menu.querySelectorAll('[role=option]')].map(option => [option.dataset.code, option]));
    const options = [];
    for (const [index, item] of items.entries()) {
      let option = existing.get(item.code);
      if (!option) {
        option = document.createElement('button');
        option.type = 'button';
        option.className = 'stock-picker__option';
        option.setAttribute('role', 'option');
        option.dataset.code = item.code;
        const code = document.createElement('span');
        code.className = 'stock-picker__code';
        code.textContent = item.code;
        const name = document.createElement('span');
        name.className = 'stock-picker__name';
        option.append(code, name);
        option.addEventListener('pointerdown', event => event.preventDefault());
        option.addEventListener('click', () => choose(items.find(candidate => candidate.code === option.dataset.code)));
      }
      option.id = `${menu.id}-${index}`;
      const name = option.querySelector('.stock-picker__name');
      if (name.textContent !== (item.name || '')) name.textContent = item.name || '';
      options.push(option);
    }
    if (menu.children.length !== options.length || options.some((option, index) => menu.children[index] !== option)) menu.replaceChildren(...options);
    open();
    highlight();
    position();
  }
  async function update() {
    const version = ++sequence;
    const query = input.value.trim();
    clearTimeout(loadingTimer);
    pending = true;
    menu.setAttribute('aria-busy', 'true');
    input.classList.remove('is-invalid');
    // Keep the current rows and dimensions while the next query is in flight.
    if (!menu.children.length) loadingTimer = setTimeout(() => {
      if (version === sequence && focused && !destroyed) message('正在查找…');
    }, 160);
    try {
      const result = await search(query);
      if (destroyed || version !== sequence || !focused || !input.isConnected) return;
      stopLoading();
      const activeCode = items[active]?.code;
      items = result.items || [];
      active = items.findIndex(item => item.code === activeCode);
      if (active < 0 && items.length) active = 0;
      render();
    } catch {
      if (!destroyed && version === sequence && focused) {
        stopLoading();
        items = [];
        active = -1;
        message('候选加载失败，请重新输入');
      }
    }
  }
  function onFocus() { focused = true; update(); }
  function onBlur() { focused = false; ++sequence; close(); }
  function onInput() { focused = document.activeElement === input; update(); }
  function onKey(event) {
    if (event.key === 'Escape' && !menu.hidden) {
      event.preventDefault(); event.stopPropagation(); ++sequence; close(); return;
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (menu.hidden) { update(); return; }
      if (!pending && items.length) { active = (active + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length; highlight(true); }
    } else if (event.key === 'Enter' && !menu.hidden && active >= 0) {
      event.preventDefault(); event.stopPropagation(); choose(items[active]);
    } else if (event.key === 'Tab') close();
  }
  input.addEventListener('focus', onFocus);
  input.addEventListener('blur', onBlur);
  input.addEventListener('input', onInput);
  input.addEventListener('keydown', onKey);
  const resizeObserver = new ResizeObserver(position);
  resizeObserver.observe(input);
  window.addEventListener('resize', position);
  window.addEventListener('scroll', position, true);
  const instance = { destroy() {
    destroyed = true; ++sequence;
    resizeObserver.disconnect();
    close(); menu.remove();
    input.removeEventListener('focus', onFocus);
    input.removeEventListener('blur', onBlur);
    input.removeEventListener('input', onInput);
    input.removeEventListener('keydown', onKey);
    window.removeEventListener('resize', position);
    window.removeEventListener('scroll', position, true);
    wrapper.replaceWith(input);
    mounted.delete(input);
  } };
  mounted.set(input, instance);
  return instance;
}
