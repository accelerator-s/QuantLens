import { hydrateIcons, loadIcon } from "../../core/icons.js";
import { currentTheme, onThemeChange, toggleTheme } from "../../core/theme.js";
import { onHealth } from "../../core/health.js";

const PAGE_META = {dashboard:['研究主线','工作台'],screen:['研究主线','条件选股'],stocks:['研究主线','个股分析'],factors:['研究主线','因子库'],backtest:['研究主线','策略回测'],portfolio:['研究主线','组合跟踪'],settings:['设置','设置']};

export async function mountTopbar(slot, bus, onRefresh) {
  const html = await fetch("/components/topbar/topbar.html").then((r) => r.text());
  slot.innerHTML = html;
  await hydrateIcons(slot);
  slot.querySelector("[data-assistant-open]").onclick = () => bus.emit("assistant:open");

  const groupEl = slot.querySelector("[data-page-group]");
  const titleEl = slot.querySelector("[data-page-title]");
  const healthEl = slot.querySelector("[data-health]");
  const healthLabel = slot.querySelector("[data-health-label]");
  const themeBtn = slot.querySelector("[data-theme-toggle]");
  const themeIcon = slot.querySelector("[data-theme-icon]");
  const refreshBtn = slot.querySelector("[data-refresh]");
  const navToggleBtn = slot.querySelector("[data-nav-toggle]");
  healthEl.addEventListener('click', () => { window.location.hash = 'settings/cluster'; });

  if (navToggleBtn) {
    navToggleBtn.addEventListener("click", () => {
      document.body.classList.toggle("rail-open");
    });
  }

  bus.on("route", (id) => {
    const [group, title] = PAGE_META[id] || ["量化工作台", "工作台"];
    if (groupEl) groupEl.textContent = group;
    if (titleEl) titleEl.textContent = title;
  });

  onHealth((h) => {
    if (!h) {
      healthEl.dataset.state = "err";
      healthLabel.textContent = "连接中断";
      return;
    }
    const startup = h.service_startup;
    if (startup?.status === 'running') {
      healthEl.dataset.state = 'starting';
      healthLabel.textContent = '服务启动中';
      healthEl.title = `${startup.phase || '正在启动后台服务'}，点击查看进度`;
      return;
    }
    if (['failed', 'interrupted'].includes(startup?.status)) {
      healthEl.dataset.state = 'err';
      healthLabel.textContent = startup.status === 'failed' ? '服务启动失败' : '服务启动中断';
      healthEl.title = `${startup.error || '点击查看详情并重试'}`;
      return;
    }
    healthEl.title = '查看后台服务状态';
    const missing = h.workspace?.missing ?? 0;
    if (missing === 0 && h.workspace?.matrix_file_exists !== false && Object.values(h.packages || {}).every(Boolean)) {
      healthEl.dataset.state = "ok";
      healthLabel.textContent = "运行正常";
    } else {
      healthEl.dataset.state = "warn";
      healthLabel.textContent = missing > 0 ? `缺目录 ${missing} 项` : "状态受限";
    }
  });

  async function syncIcon(theme) {
    const name = theme === "dark" ? "theme-sun" : "theme-moon";
    themeIcon.innerHTML = await loadIcon(name);
  }
  await syncIcon(currentTheme());
  onThemeChange(syncIcon);

  themeBtn.addEventListener("click", () => toggleTheme());
  refreshBtn.addEventListener("click", () => onRefresh());
}
