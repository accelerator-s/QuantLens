import { api } from './core/api.js';
import { icons } from './core/icons.js';
import { createBus } from './core/bus.js';
import { initHealth } from './core/health.js';
import { initTheme } from './core/theme.js';
import { initToast, toastError } from './core/toast.js';
import { mountRail } from './components/rail/rail.js';
import { mountTopbar } from './components/topbar/topbar.js';
import { mount as mountLens } from './components/lens/lens.js';
import { createPageCache, prefetchApi, invalidateRequests } from './core/page-cache.js';

const LENS = new Set(['dashboard','market','stocks','screen','models','watch','assistant','journal','system']);
const PAGES = new Set([...LENS,'factors','experiments','backtest','portfolio','strategy','data','review','ai-config','overview','admin']);
initTheme();
const bus = createBus();
initHealth(bus);
initToast(bus);
const workspace = document.getElementById('workspace');
const views = new Map();
const pending = new Map();
const assets = new Map();
const pageCache = createPageCache();
const shared = {api, icons, navigate, stock:'', backtestRun:''};
let currentRoute = '';
let navigationVersion = 0;
bus.on('backtest:open', id => { shared.backtestRun = id; pageCache.invalidate('backtest'); });
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

const READ_AHEAD = {
  dashboard: ['/api/lens/overview'], market: ['/api/lens/market'],
  models: ['/api/lens/research', '/api/lens/rolling'], watch: ['/api/lens/watch'],
  journal: ['/api/lens/journal'], data: ['/api/providers'],
};
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
  const id = PAGES.has(target) ? target : 'dashboard';
  const version = ++navigationVersion;
  const mounted = views.has(id) && !pending.has(id);
  currentRoute = id;
  if (location.hash !== `#${id}`) history.pushState(null, '', `#${id}`);
  document.body.classList.remove('rail-open');
  views.forEach((view, key) => { view.hidden = key !== id; });
  bus.emit('route', id);
  try {
    const view = await ensureView(id);
    if (version !== navigationVersion) return;
    view.hidden = false;
    if (mounted && (refresh || pageCache.needsRefresh(id))) {
      pageCache.mark(id);
      bus.emit('view:activate', id);
    }
  } catch (error) {
    toastError(bus, error, '页面加载失败');
  }
}

async function pollHealth() {
  try { bus.emit('health', await api.status()); }
  catch { bus.emit('health', null); }
}

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
  setInterval(pollHealth, 30000);
  window.addEventListener('hashchange', () => {
    const id = location.hash.slice(1);
    if (id !== currentRoute) navigate(id);
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
