import { api } from './api.js';

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

  function close() {
    menu.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
  }
  function position() {
    if (menu.hidden) return;
    const rect = input.getBoundingClientRect();
    if (!input.isConnected || rect.bottom < 0 || rect.top > innerHeight) { close(); return; }
    const below = innerHeight - rect.bottom - 12;
    const above = rect.top - 12;
    const upwards = below < 180 && above > below;
    const height = Math.max(40, Math.min(240, upwards ? above : below));
    menu.style.maxHeight = `${height}px`;
    menu.style.width = `${Math.min(rect.width, innerWidth - 16)}px`;
    menu.style.left = `${Math.max(8, Math.min(rect.left, innerWidth - menu.offsetWidth - 8))}px`;
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
  function highlight() {
    [...menu.querySelectorAll('[role=option]')].forEach((option, index) => {
      option.classList.toggle('is-active', index === active);
      option.setAttribute('aria-selected', String(index === active));
    });
    const option = menu.querySelectorAll('[role=option]')[active];
    if (option) {
      input.setAttribute('aria-activedescendant', option.id);
      option.scrollIntoView({ block: 'nearest' });
    }
  }
  function choose(item) {
    ++sequence;
    input.value = item.code;
    input.classList.remove('is-invalid');
    input.dispatchEvent(new Event('change', { bubbles: true }));
    close();
  }
  function render() {
    menu.replaceChildren();
    if (!items.length) { message('暂无匹配的股票'); return; }
    for (const [index, item] of items.entries()) {
      const option = document.createElement('button');
      option.type = 'button';
      option.className = 'stock-picker__option';
      option.id = `${menu.id}-${index}`;
      option.setAttribute('role', 'option');
      option.dataset.code = item.code;
      const code = document.createElement('span');
      code.className = 'stock-picker__code';
      code.textContent = item.code;
      const name = document.createElement('span');
      name.className = 'stock-picker__name';
      name.textContent = item.name || '';
      option.append(code, name);
      option.addEventListener('pointerdown', event => event.preventDefault());
      option.addEventListener('click', () => choose(item));
      menu.append(option);
    }
    open();
    highlight();
    position();
  }
  async function update() {
    const version = ++sequence;
    const query = input.value.trim();
    items = [];
    active = -1;
    input.classList.remove('is-invalid');
    message('正在查找…');
    try {
      const result = await search(query);
      if (destroyed || version !== sequence || !focused || !input.isConnected) return;
      items = result.items || [];
      active = items.length ? 0 : -1;
      render();
    } catch {
      if (!destroyed && version === sequence && focused) message('候选加载失败，请重新输入');
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
      if (items.length) { active = (active + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length; highlight(); }
    } else if (event.key === 'Enter' && !menu.hidden && active >= 0) {
      event.preventDefault(); event.stopPropagation(); choose(items[active]);
    } else if (event.key === 'Tab') close();
  }
  input.addEventListener('focus', onFocus);
  input.addEventListener('blur', onBlur);
  input.addEventListener('input', onInput);
  input.addEventListener('keydown', onKey);
  window.addEventListener('resize', position);
  window.addEventListener('scroll', position, true);
  const instance = { destroy() {
    destroyed = true; ++sequence;
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
