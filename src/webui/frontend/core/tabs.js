export async function mountTabs(root, ctx, page, definitions, initial) {
  root.replaceChildren();
  const bar = document.createElement('div');
  bar.className = 'section-tabs'; bar.setAttribute('role', 'tablist'); bar.setAttribute('aria-label', '页面内容');
  root.append(bar);
  const panels = new Map(), buttons = new Map(), pending = new Map();
  let active = '';
  async function activate(id, focus = false) {
    if (!definitions.some(d => d.id === id)) id = definitions[0].id;
    active = id;
    for (const [key, button] of buttons) {
      button.setAttribute('aria-selected', String(key === id)); button.tabIndex = key === id ? 0 : -1;
      panels.get(key).hidden = key !== id;
    }
    if (focus) buttons.get(id).focus();
    if (!pending.has(id)) {
      const definition = definitions.find(d => d.id === id), panel = panels.get(id);
      const work = (async () => {
        if (definition.html !== undefined) {
          panel.innerHTML = definition.html;
          await definition.mount(panel, ctx);
        } else if (definition.mount) await definition.mount(panel, ctx);
        else {
          const response = await fetch(`/components/${definition.component}/${definition.component}.html`);
          if (!response.ok) throw new Error('子页模板读取失败');
          panel.innerHTML = await response.text();
          const mod = await import(`/components/${definition.component}/${definition.component}.js`);
          await mod.mount(panel, ctx);
        }
      })().catch(error => { pending.delete(id); panel.textContent = error.message; throw error; });
      pending.set(id, work);
    }
    try { await pending.get(id); } catch (error) { ctx.bus.emit('toast', {kind:'error',title:'子页读取失败',detail:error.message}); }
  }
  for (const definition of definitions) {
    const button = document.createElement('button'), panel = document.createElement('section');
    button.type = 'button'; button.className='btn btn--ghost'; button.textContent = definition.label; button.id = `${page}-tab-${definition.id}`;
    button.setAttribute('role', 'tab'); button.setAttribute('aria-controls', `${page}-panel-${definition.id}`);
    panel.id = `${page}-panel-${definition.id}`; panel.setAttribute('role','tabpanel'); panel.setAttribute('aria-labelledby',button.id);
    panel.className = 'section-panel'; panel.dataset.pane = definition.pane || definition.component || page;
    panel.hidden = true; buttons.set(definition.id,button); panels.set(definition.id,panel); bar.append(button); root.append(panel);
    button.onclick = () => activate(definition.id);
    button.onkeydown = event => {
      const keys = ['ArrowLeft','ArrowRight','Home','End']; if (!keys.includes(event.key)) return;
      event.preventDefault(); const index = definitions.findIndex(d => d.id === active);
      const target = event.key === 'Home' ? 0 : event.key === 'End' ? definitions.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + definitions.length) % definitions.length;
      activate(definitions[target].id,true);
    };
  }
  ctx.bus.on('subtab', detail => { if (detail.page === page) activate(detail.tab); });
  await activate(initial || ctx.tabState?.[page] || definitions[0].id);
}
