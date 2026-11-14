import { api } from './core/api.js';
import { icons } from './core/icons.js';
import { createBus } from './core/bus.js';
import { initHealth, createRestartDetector } from './core/health.js';
import { initTheme } from './core/theme.js';
import { initDatePickers } from './core/date-picker.js';
import { initButtonTextOffset } from './core/dom.js';
import { initToast, toastError } from './core/toast.js';
import { mountRail } from './components/rail/rail.js';
import { mountTopbar } from './components/topbar/topbar.js';
import { mount as mountLens } from './components/lens/lens.js';
import { createPageCache, prefetchApi, invalidateRequests } from './core/page-cache.js';

const LENS = new Set(['dashboard','stocks','screen']);
const PAGES = new Set([...LENS,'factors','strategy-research','backtest','portfolio','trash','settings']);
initTheme();
initDatePickers();
initButtonTextOffset();
const bus = createBus();
initHealth(bus);
initToast(bus);
const workspace = document.getElementById('workspace');
const views = new Map();
const pending = new Map();
const assets = new Map();
const pageCache = createPageCache();
const shared = {api, icons, navigate, stock:'', backtestRun:'', tabState:{}};
let currentRoute = '';
let navigationVersion = 0;
bus.on('backtest:open', id => {
  shared.backtestRun = id;
  (shared.tabState.backtest ||= {}).requestedRun = id;
  pageCache.invalidate('backtest');
});
window.addEventListener('quantlens:changed', () => pageCache.invalidate());
document.querySelector('[data-rail-backdrop]').addEventListener('click', () => document.body.classList.remove('rail-open'));

function loadAssets(id) {
  if (!assets.has(id)) {
    const promise = Promise.all([
      fetch(`/components/${id}/${id}.html`).then(response => {
        if (!response.ok) throw new Error(`页面模板加载失败（${response.status}）`);
        return response.text();
      }),
      import(`/components/${id}/${id}.js`),
    ]).catch(error => { assets.delete(id); throw error; });
    assets.set(id, promise);
  }
  return assets.get(id);
}

const READ_AHEAD = {dashboard:['/api/overview'], factors:['/api/factors'], settings:['/api/settings/data']};
async function prefetchPage(id) {
  if (!PAGES.has(id) || views.has(id)) return;
  if (!LENS.has(id)) await loadAssets(id);
  for (const path of READ_AHEAD[id] || []) await prefetchApi(path);
}

async function ensureView(id) {
  if (pending.has(id)) return pending.get(id);
  if (views.has(id)) return views.get(id);
  const host = document.createElement('section');
  host.className = `view pane${LENS.has(id) ? ' ql-page' : ''}`;
  host.dataset.view = id;
  host.dataset.pane = id;
  host.hidden = id !== currentRoute;
  workspace.append(host);
  views.set(id, host);
  const subscriptions = [];
  const ctx = Object.create(shared);
  Object.defineProperty(ctx, 'stock', {get:() => shared.stock, set:value => {
    if (shared.stock !== value) pageCache.invalidate('stocks');
    shared.stock = value;
  }});
  ctx.bus = {
    on(event, fn) {
      const off = bus.on(event === 'route' ? 'view:activate' : event, fn);
      subscriptions.push(off);
      return off;
    },
    emit(event, payload) {
      if (event === 'route') return navigate(payload);
      bus.emit(event, payload);
    },
  };
  const promise = (async () => {
    try {
      if (LENS.has(id)) {
        await mountLens(host, ctx);
      } else {
        const [html, mod] = await loadAssets(id);
        host.innerHTML = html;
        await mod.mount(host, ctx);
      }
      pageCache.mark(id);
      return host;
    } catch (error) {
      subscriptions.forEach(off => off());
      views.delete(id);
      host.remove();
      throw error;
    } finally {
      pending.delete(id);
    }
  })();
  pending.set(id, promise);
  return promise;
}

async function navigate(target, {refresh = false} = {}) {
  let [page, tab] = String(target).split('/');
  if (page === 'backtest') tab = undefined;
  const id = PAGES.has(page) ? page : 'dashboard';
  if (tab) shared.tabState[id] = tab;
  closeAssistant();
  const version = ++navigationVersion;
  const mounted = views.has(id) && !pending.has(id);
  currentRoute = id;
  const hash = `#${id}${tab ? '/'+tab : ''}`;
  if (location.hash !== hash) history.pushState(null, '', hash);
  document.body.classList.remove('rail-open');
  views.forEach((view, key) => { view.hidden = key !== id; });
  bus.emit('route', id);
  try {
    const view = await ensureView(id);
    if (version !== navigationVersion) return;
    view.hidden = false;
    if (tab) bus.emit('subtab', {page:id,tab});
    if (mounted && (refresh || pageCache.needsRefresh(id))) {
      pageCache.mark(id);
      bus.emit('view:activate', id);
    }
  } catch (error) {
    toastError(bus, error, '页面加载失败');
  }
}

let assistantPanel = null, assistantTrigger = null, assistantReady = null;
function closeAssistant() {
  if (assistantPanel && !assistantPanel.hidden) { assistantPanel.hidden = true; assistantTrigger?.focus(); }
}
bus.on('assistant:open', async () => {
  const openingVersion = navigationVersion;
  assistantTrigger = document.activeElement;
  if (!assistantPanel) {
    assistantPanel = document.createElement('aside'); assistantPanel.className='assistant-drawer'; assistantPanel.hidden=true;
    assistantPanel.setAttribute('role','dialog'); assistantPanel.setAttribute('aria-modal','true'); assistantPanel.setAttribute('aria-label','研究助手');
    const close=document.createElement('button'); close.type='button'; close.className='btn btn--ghost assistant-drawer__close'; close.textContent='关闭助手'; close.onclick=closeAssistant;
    const content=document.createElement('section'); content.dataset.pane='assistant';
    assistantPanel.append(close,content); document.body.append(assistantPanel);
    assistantPanel.addEventListener('keydown',event=>{
      if(event.key==='Escape') closeAssistant();
      if(event.key==='Tab') {
        const focusable=[...assistantPanel.querySelectorAll('button,input,textarea,a[href]')].filter(el=>!el.disabled);
        const first=focusable[0],last=focusable.at(-1);
        if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
        else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
      }
    });
    assistantReady=mountLens(content,{...shared,bus});
  }
  await assistantReady;
  if (openingVersion !== navigationVersion) { assistantPanel.hidden=true; return; }
  assistantPanel.hidden=false; assistantPanel.querySelector('button').focus();
});

let healthTimer, healthRequest = 0;
const backendRestarted = createRestartDetector();
async function pollHealth() {
  clearTimeout(healthTimer);
  const requestId = ++healthRequest;
  let starting = false;
  try {
    const status = await api.status();
    if (requestId !== healthRequest) return;
    if (backendRestarted(status)) { window.location.reload(); return; }
    starting = status.service_startup?.status === 'running';
    bus.emit('health', status);
  }
  catch { if (requestId !== healthRequest) return; bus.emit('health', null); }
  healthTimer = setTimeout(pollHealth, starting ? 2000 : 10000);
}
bus.on('health:refresh', pollHealth);

async function boot() {
  await Promise.all([
    mountRail(document.getElementById('rail-slot'), bus, navigate),
    mountTopbar(document.getElementById('topbar-slot'), bus, () => {
      pollHealth();
      invalidateRequests();
      if (currentRoute) navigate(currentRoute, {refresh: true});
    }),
  ]);
  await pollHealth();
  window.addEventListener('hashchange', () => {
    const id = location.hash.slice(1);
    if (id !== currentRoute || id.includes('/')) navigate(id);
  });
  await navigate(location.hash.slice(1));
  const rail = document.getElementById('rail-slot');
  for (const event of ['pointerover', 'focusin']) rail.addEventListener(event, e => {
    const id = e.target.closest('[data-route]')?.dataset.route;
    if (id) prefetchPage(id).catch(() => {});
  });
  // Warm templates/modules serially while idle; never mount hidden pages or run actions.
  const queue = [...PAGES].filter(id => !LENS.has(id));
  const idle = window.requestIdleCallback || (fn => setTimeout(fn, 200));
  const warm = () => idle(async () => {
    const id = queue.shift();
    if (!id) return;
    try { await loadAssets(id); } catch { /* Navigation retries failed preloads. */ }
    warm();
  });
  if (!navigator.connection?.saveData) warm();
}
boot().catch(error => toastError(bus, error, '启动界面失败'));
