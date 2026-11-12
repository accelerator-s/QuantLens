import { hydrateIcons, loadIcon } from "../../core/icons.js";
import { currentTheme, onThemeChange, toggleTheme } from "../../core/theme.js";
import { onHealth } from "../../core/health.js";

const PAGE_META = {
  dashboard: ["行情与选股", "工作台"],
  market: ["行情与选股", "市场全景"],
  stocks: ["行情与选股", "个股分析"],
  screen: ["行情与选股", "条件选股"],
  watch: ["行情与选股", "自选跟踪"],
  models: ["量化研究", "预测模型"],
  factors: ["量化研究", "因子库"],
  experiments: ["量化研究", "因子迭代"],
  backtest: ["量化研究", "策略回测"],
  assistant: ["量化研究", "研究问答"],
  journal: ["量化研究", "研究记录"],
  portfolio: ["组合与运维", "组合跟踪"],
  strategy: ["组合与运维", "策略配置"],
  data: ["组合与运维", "数据中心"],
  review: ["组合与运维", "审计日志"],
  "ai-config": ["组合与运维", "接口配置"],
  system: ["组合与运维", "系统维护"],
  overview: ["行情与选股", "工作台总览"],
  admin: ["组合与运维", "系统设置"],
};

export async function mountTopbar(slot, bus, onRefresh) {
  const html = await fetch("/components/topbar/topbar.html").then((r) => r.text());
  slot.innerHTML = html;
  await hydrateIcons(slot);

  const groupEl = slot.querySelector("[data-page-group]");
  const titleEl = slot.querySelector("[data-page-title]");
  const healthEl = slot.querySelector("[data-health]");
  const healthLabel = slot.querySelector("[data-health-label]");
  const themeBtn = slot.querySelector("[data-theme-toggle]");
  const themeIcon = slot.querySelector("[data-theme-icon]");
  const refreshBtn = slot.querySelector("[data-refresh]");
  const navToggleBtn = slot.querySelector("[data-nav-toggle]");

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
