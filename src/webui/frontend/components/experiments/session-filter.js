import {createSelect} from '../../core/select.js';
import {selectMarkup} from '../../core/dom.js';
import {iconEl} from '../../core/icons.js';

export function createSessionFilter(root, {ctx, key, allLabel, ariaLabel, onChange}) {
  let names = new Map(), value = ctx[key] || '';
  root.innerHTML = selectMarkup(allLabel);
  root.querySelector('[data-select-trigger]').setAttribute('aria-label', ariaLabel);
  iconEl('filter').then(icon => root.querySelector('[data-select-trigger]').prepend(icon));
  const select = createSelect(root, {onChange: next => {
    value = next;
    ctx[key] = next;
    ctx[`${key}Label`] = names.get(next) || next;
    onChange();
  }});
  function refresh() {
    if (value && !names.has(value)) names.set(value, ctx[`${key}Label`] || value);
    select.setOptions([{value: '', label: allLabel}, ...[...names].map(([value, label]) => ({value, label}))], value);
  }
  refresh();
  return {
    get value() { return value; },
    update(entries) { names = new Map(entries.filter(([name]) => name)); refresh(); },
    set(next = '', label = '') {
      const changed = value !== next;
      value = next; ctx[key] = next;
      if (label) ctx[`${key}Label`] = label;
      refresh();
      return changed;
    },
  };
}
