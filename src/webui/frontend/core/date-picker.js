import { createSelect, selectMarkup } from './select.js';

const pad = value => String(value).padStart(2, '0');
const dateKey = date => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
const parse = value => /^\d{4}-\d{2}-\d{2}$/.test(value || '') ? new Date(`${value}T12:00:00`) : null;
const shiftDay = (date, days) => new Date(date.getFullYear(), date.getMonth(), date.getDate() + days, 12);
const pickerOptions = new WeakMap();

export function configureDatePicker(input, {dates, highlightedDates = []} = {}) {
  pickerOptions.set(input, {
    dates: dates ? [...new Set(dates)].sort() : null,
    allowed: dates ? new Set(dates) : null,
    highlighted: new Set(highlightedDates),
  });
}

export function initDatePickers() {
  const enhanced = new WeakSet();
  let owner = null, anchor = null, active = null, month = null, trigger = null;
  const panel = document.createElement('div');
  panel.id = 'ql-date-picker';
  panel.className = 'date-picker';
  panel.hidden = true;
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', '选择日期');
  panel.innerHTML = `<header class="date-picker__head">
    <button type="button" data-month-step="-1" aria-label="上个月">‹</button>
    <div class="date-picker__period"><div class="select" data-year>${selectMarkup('年份')}</div><div class="select" data-month>${selectMarkup('月份')}</div></div>
    <button type="button" data-month-step="1" aria-label="下个月">›</button>
  </header><div class="date-picker__week" aria-hidden="true">${['一','二','三','四','五','六','日'].map(day => `<span>${day}</span>`).join('')}</div>
  <div class="date-picker__days" role="grid" aria-label="日期"></div>
  <footer class="date-picker__footer"><span class="date-picker__legend date-picker__legend--adjacent">相邻月份</span><span class="date-picker__legend" data-date-legend hidden>调仓日</span><button type="button" data-clear>清空</button><button type="button" data-today>今天</button></footer>`;
  const yearHost = panel.querySelector('[data-year]');
  const monthHost = panel.querySelector('[data-month]');
  for (const [host, label] of [[yearHost, '年份'], [monthHost, '月份']]) {
    host.querySelector('[data-select-trigger]').setAttribute('aria-label', label);
    host.querySelector('[role="listbox"]').setAttribute('aria-label', label);
  }
  const yearSelect = createSelect(yearHost, {onChange: changePeriod, onOpen: () => openPeriod(yearHost, monthSelect)});
  const monthSelect = createSelect(monthHost, {onChange: changePeriod, onOpen: () => openPeriod(monthHost, yearSelect)});
  const grid = panel.querySelector('[role="grid"]');
  document.body.append(panel);

  function openPeriod(host, other) {
    other.close();
    const list = host.querySelector('[role="listbox"]');
    const selected = list.querySelector('[aria-selected="true"]');
    if (selected) list.scrollTop = selected.offsetTop - (list.clientHeight - selected.offsetHeight) / 2;
  }
  function allowed(date) {
    const value = dateKey(date);
    const dates = pickerOptions.get(owner)?.allowed;
    return (!owner.min || value >= owner.min) && (!owner.max || value <= owner.max) && (!dates || dates.has(value));
  }
  function close(restore = false) {
    if (!owner) return;
    yearSelect.close();
    monthSelect.close();
    panel.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
    if (restore && owner.isConnected) owner.focus({preventScroll: true});
    owner = null;
  }
  function position() {
    if (!owner) return;
    const viewport = window.visualViewport;
    const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
    const viewportWidth = viewport?.width || window.innerWidth;
    const viewportHeight = viewport?.height || window.innerHeight;
    panel.style.maxWidth = `${Math.max(0, viewportWidth - 16)}px`;
    panel.style.maxHeight = `${Math.max(0, viewportHeight - 16)}px`;
    const rect = anchor.getBoundingClientRect();
    const width = panel.offsetWidth, height = panel.offsetHeight;
    const preferredTop = rect.bottom + height + 6 > top + viewportHeight ? rect.top - height - 6 : rect.bottom + 6;
    panel.style.left = `${Math.max(left + 8, Math.min(rect.left, left + viewportWidth - width - 8))}px`;
    panel.style.top = `${Math.max(top + 8, Math.min(preferredTop, top + viewportHeight - height - 8))}px`;
  }
  function render(focus = false) {
    const year = month.getFullYear(), index = month.getMonth();
    const minYear = parse(owner.min)?.getFullYear() || Math.min(1900, year);
    const maxYear = parse(owner.max)?.getFullYear() || Math.max(new Date().getFullYear() + 100, year);
    yearSelect.setOptions(Array.from({length: maxYear - minYear + 1}, (_, i) => ({value: String(minYear + i), label: `${minYear + i}年`})), String(year));
    const months = Array.from({length: 12}, (_, i) => ({value: String(i), label: `${i + 1}月`}))
      .filter(({value}) => {
        const start = dateKey(new Date(year, Number(value), 1, 12));
        const end = dateKey(new Date(year, Number(value) + 1, 0, 12));
        return (!owner.min || end >= owner.min) && (!owner.max || start <= owner.max);
      });
    monthSelect.setOptions(months, String(index));
    const offset = (new Date(year, index, 1).getDay() + 6) % 7;
    const first = new Date(year, index, 1 - offset, 12);
    const today = dateKey(new Date());
    grid.replaceChildren();
    grid.setAttribute('aria-label', `${year}年${index + 1}月`);
    for (let i = 0; i < 42; i++) {
      const date = shiftDay(first, i), value = dateKey(date);
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.date = value;
      button.textContent = date.getDate();
      button.setAttribute('role', 'gridcell');
      button.setAttribute('aria-label', `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`);
      button.setAttribute('aria-selected', String(value === owner.value));
      if (value === today) button.setAttribute('aria-current', 'date');
      button.disabled = !allowed(date);
      button.classList.toggle('is-outside', date.getMonth() !== index);
      const highlighted = !button.disabled && !!pickerOptions.get(owner)?.highlighted.has(value);
      button.classList.toggle('is-highlighted', highlighted);
      if (highlighted) {
        button.title = `${value} · 调仓日`;
        button.setAttribute('aria-label', `${button.getAttribute('aria-label')}，调仓日`);
      }
      button.tabIndex = value === dateKey(active) ? 0 : -1;
      grid.append(button);
    }
    panel.querySelector('[data-today]').disabled = !allowed(new Date());
    panel.querySelector('[data-clear]').disabled = owner.required;
    const configured = pickerOptions.has(owner);
    panel.querySelector('[data-date-legend]').hidden = !configured;
    panel.querySelector('[data-clear]').hidden = configured;
    panel.querySelector('[data-today]').hidden = configured;
    panel.querySelectorAll('[data-month-step]').forEach(button => {
      const step = Number(button.dataset.monthStep);
      const edge = new Date(year, index + (step < 0 ? 0 : 1), step < 0 ? 0 : 1, 12);
      button.disabled = step < 0 ? !!owner.min && dateKey(edge) < owner.min : !!owner.max && dateKey(edge) > owner.max;
    });
    position();
    if (focus) grid.querySelector('[tabindex="0"]:not(:disabled)')?.focus();
  }
  function open(input, focus = false) {
    if (input.disabled || input.readOnly) return;
    close();
    owner = input;
    anchor = input.parentElement;
    trigger = anchor.querySelector('.date-input__toggle');
    active = parse(input.value) || new Date();
    if (input.min && dateKey(active) < input.min) active = parse(input.min);
    if (input.max && dateKey(active) > input.max) active = parse(input.max);
    month = new Date(active.getFullYear(), active.getMonth(), 1, 12);
    // Native modal dialogs have their own top layer; keep the calendar inside it.
    (input.closest('dialog[open]') || document.body).append(panel);
    panel.hidden = false;
    panel.scrollTop = 0;
    trigger.setAttribute('aria-expanded', 'true');
    render(focus);
  }
  function choose(value) {
    if (value && !allowed(parse(value))) return;
    const input = owner;
    if (input.value !== value) {
      input.value = value;
      input.dispatchEvent(new Event('input', {bubbles: true}));
      input.dispatchEvent(new Event('change', {bubbles: true}));
    }
    close(true);
  }
  function moveMonth(step) {
    month = new Date(month.getFullYear(), month.getMonth() + step, 1, 12);
    active = monthActiveDay();
    render();
  }
  function monthActiveDay() {
    const prefix = dateKey(month).slice(0, 7);
    const first = pickerOptions.get(owner)?.dates?.find(value => value.startsWith(prefix));
    const date = first ? parse(first) : new Date(month);
    if (owner.min && dateKey(date) < owner.min) return parse(owner.min);
    if (owner.max && dateKey(date) > owner.max) return parse(owner.max);
    return date;
  }
  panel.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!owner || !button || button.disabled) return;
    if (button.dataset.date) choose(button.dataset.date);
    else if (button.hasAttribute('data-clear')) choose('');
    else if (button.hasAttribute('data-today')) choose(dateKey(new Date()));
    else if (button.dataset.monthStep) moveMonth(Number(button.dataset.monthStep));
  });
  function changePeriod() {
    month = new Date(Number(yearSelect.value), Number(monthSelect.value), 1, 12);
    if (owner.min && dateKey(month).slice(0, 7) < owner.min.slice(0, 7)) month = new Date(parse(owner.min).getFullYear(), parse(owner.min).getMonth(), 1, 12);
    if (owner.max && dateKey(month).slice(0, 7) > owner.max.slice(0, 7)) month = new Date(parse(owner.max).getFullYear(), parse(owner.max).getMonth(), 1, 12);
    active = monthActiveDay();
    render();
  }
  panel.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(true); return; }
    if (!event.target.dataset.date) return;
    let next;
    const date = parse(event.target.dataset.date);
    const delta = {ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7}[event.key];
    if (delta) next = shiftDay(date, delta);
    else if (event.key === 'Home') next = shiftDay(date, -(date.getDay() + 6) % 7);
    else if (event.key === 'End') next = shiftDay(date, 6 - (date.getDay() + 6) % 7);
    else if (event.key === 'PageUp' || event.key === 'PageDown') {
      next = new Date(date.getFullYear(), date.getMonth() + (event.key === 'PageUp' ? -1 : 1), 1, 12);
      next.setDate(Math.min(date.getDate(), new Date(next.getFullYear(), next.getMonth() + 1, 0).getDate()));
    }
    if (!next) return;
    event.preventDefault();
    const dates = pickerOptions.get(owner)?.dates;
    if (!allowed(next) && dates?.length) {
      const forward = next > date;
      const candidates = forward ? dates : dates.slice().reverse();
      const value = candidates.find(value => forward ? value >= dateKey(next) : value <= dateKey(next));
      if (value) next = parse(value);
    }
    if (!allowed(next)) return;
    active = next;
    month = new Date(next.getFullYear(), next.getMonth(), 1, 12);
    render(true);
  });

  function enhance(input) {
    if (enhanced.has(input)) return;
    enhanced.add(input);
    const wrap = document.createElement('span');
    wrap.className = 'date-input';
    input.before(wrap);
    wrap.append(input);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'date-input__toggle';
    button.setAttribute('aria-label', '打开日历');
    button.setAttribute('aria-haspopup', 'dialog');
    button.setAttribute('aria-controls', panel.id);
    button.setAttribute('aria-expanded', 'false');
    button.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18"/></svg>';
    button.disabled = input.disabled || input.readOnly;
    wrap.append(button);
    button.addEventListener('click', event => {
      event.preventDefault();
      if (owner === input) close(true); else open(input, true);
    });
    input.addEventListener('click', event => { event.preventDefault(); open(input); });
    input.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown' && event.altKey || event.key === 'F4' || event.key === ' ') {
        event.preventDefault(); open(input, true);
      } else if (event.key === 'Escape' && owner === input) {
        event.preventDefault(); event.stopPropagation(); close();
      }
    });
    input.addEventListener('change', () => { if (owner === input) open(input); });
  }
  function scan(node) {
    if (!(node instanceof Element)) return;
    if (node.matches('input[type="date"]')) enhance(node);
    node.querySelectorAll('input[type="date"]').forEach(enhance);
  }
  scan(document.body);
  const observer = new MutationObserver(records => {
    records.forEach(record => record.addedNodes.forEach(scan));
    if (owner && (!owner.isConnected || owner.closest('[hidden]'))) close();
  });
  observer.observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['hidden']});
  document.addEventListener('pointerdown', event => {
    if (owner && !panel.contains(event.target) && !anchor.contains(event.target)) close();
  });
  document.addEventListener('focusin', event => {
    if (owner && !panel.contains(event.target) && !anchor.contains(event.target)) close();
  });
  document.addEventListener('scroll', event => { if (!panel.contains(event.target)) close(); }, true);
  window.addEventListener('resize', () => close());
  window.visualViewport?.addEventListener('resize', () => close());
  window.addEventListener('hashchange', () => close());
}
