import {newestFirst} from '../../core/sort.js';
import {mountTabs} from '../../core/tabs.js';
import { renderState } from "../state-card/state-card.js";
import { runWithConfirm } from "../confirm-dialog/confirm-dialog.js";
import { hydrateIcons } from "../../core/icons.js";
import { createSelect } from "../../core/select.js";
import { toastError } from "../../core/toast.js";
import { badge, fmtNum, fmtPct, fmtTime } from "../../core/fmt.js";
import { el, kvEl, selectMarkup, tableEl } from "../../core/dom.js";
import {createModelReviews} from '../../core/model-reviews.js';

const STATUS_LABEL = { draft: "研究中", active: "已启用", disabled: "已停止" };
const STATUS_TONE = { draft: "accent", active: "safe", disabled: "muted" };
const DIRECTION_LABEL = {
  positive: "数值越高越看好",
  negative: "数值越低越看好",
  undecided: "方向待验证",
  long_only_positive: "数值越高越看好",
  long_only_negative: "数值越低越看好",
  long_short: "多空双向",
};
const TIMESCALE_LABEL = {
  daily: "短期（日频）",
  weekly: "短期（周频）",
  monthly: "中短期（月频）",
  quarterly: "中期（季频）",
  event_driven: "事件驱动",
};
const EVAL_STATE_LABEL = { running: "评价中", completed: "已完成", failed: "失败" };
const EVAL_STATE_TONE = { running: "accent", completed: "safe", failed: "danger", interrupted: "warn", queued: "warn" };

// 因子研究入口
async function mountContent(root, ctx) {
  const listHost = root.querySelector("[data-list]");
  const detailHost = root.querySelector("[data-detail]");
  const emptyHost = root.querySelector("[data-library-empty]");
  const split = root.querySelector(".split");
  const statusRoot = root.querySelector("[data-status-select]");
  statusRoot.innerHTML = selectMarkup("全部状态");
  await hydrateIcons(root);

  const statusSelect = createSelect(statusRoot, { onChange: () => load() });
  const researchOpenBtn = root.querySelector("[data-research-open]");
  researchOpenBtn.addEventListener("click", () => ctx.bus.emit("route", "factors/research"));
  root.querySelector("[data-empty-research]").addEventListener("click", () => ctx.bus.emit("route", "factors/research"));

  let optionsReady = false;
  let currentName = "";
  let statuses = [];
  let evalRefreshTimer = null;
  const selectedEvalRuns = new Map();
  let detailRequest = 0;
  let evalRequest = 0;

  ctx.bus.on("route", (id) => {
    if (id === "factors") load();
    else if (evalRefreshTimer) {
      clearTimeout(evalRefreshTimer);
      evalRefreshTimer = null;
    }
  });
  ctx.bus.on('subtab', data => { if (data.page === 'factors' && data.tab === 'list') load(); });
  root.closest('.view')?.querySelector('#factors-tab-list')?.addEventListener('click', load);
  await load();

  async function load() {
    emptyHost.hidden = true;
    split.hidden = false;
    researchOpenBtn.hidden = false;
    await renderState(listHost, { kind: "loading", title: "加载因子数据表…" });
    try {
      const data = await ctx.api.factors({
        status: statusSelect.value === "__all__" ? "" : statusSelect.value,
      });
      statuses = data.statuses;
      if (!optionsReady) {
        statusSelect.setOptions(
          [
            { value: "__all__", label: "全部状态" },
            ...statuses.map((value) => ({ value, label: STATUS_LABEL[value] || value })),
          ],
          "__all__",
        );
        optionsReady = true;
      }
      renderList(data.items);
    } catch (err) {
      await renderState(listHost, {
        kind: "error",
        title: "因子列表加载失败",
        detail: err.message,
        retry: () => load(),
      });
    }
  }

  function renderList(items) {
    listHost.innerHTML = "";
    emptyHost.hidden = items.length > 0;
    split.hidden = !items.length;
    researchOpenBtn.hidden = !items.length;
    if (!items.length) {
      currentName = "";
      ++detailRequest;
      ++evalRequest;
      clearTimeout(evalRefreshTimer);
      evalRefreshTimer = null;
      detailHost.innerHTML = "";
      return;
    }
    for (const item of items) {
      const row = el("button", "item-row");
      row.type = "button";
      row.dataset.name = item.name;
      if (item.name === currentName) row.classList.add("is-active");
      row.append(el("span", "item-row__title", item.display_name || item.name));
      const meta = el("span", "item-row__meta");
      if (item.version) meta.append(el("span", "", item.version));
      meta.append(badge(STATUS_LABEL[item.status] || item.status, STATUS_TONE[item.status]));
      if (item.ai_generated) meta.append(badge("模型生成", "accent"));
      meta.append(el("span", "", TIMESCALE_LABEL[item.timescale] || item.timescale || "—"));
      row.append(meta);
      row.addEventListener("click", () => {
        currentName = item.name;
        listHost.querySelectorAll(".item-row").forEach((n) => {
          n.classList.toggle("is-active", n.dataset.name === currentName);
        });
        loadDetail(item.name);
      });
      listHost.append(row);
    }
    if (!currentName || !items.some((i) => i.name === currentName)) {
      currentName = items[0].name;
      listHost.querySelector(".item-row")?.classList.add("is-active");
    }
    loadDetail(currentName);
  }

  async function loadDetail(name) {
    const request = ++detailRequest;
    await renderState(detailHost, { kind: "loading", title: "加载因子详情…" });
    try {
      const data = await ctx.api.factor(name);
      if (request !== detailRequest || name !== currentName) return;
      renderDetail(data);
    } catch (err) {
      if (request !== detailRequest || name !== currentName) return;
      await renderState(detailHost, {
        kind: "error",
        title: "因子详情加载失败",
        detail: err.message,
        retry: () => loadDetail(name),
      });
    }
  }

  function renderDetail(data) {
    const factor = data.factor;
    detailHost.innerHTML = "";
    const card = el("article", "card detail-card factors__detail");

    const head = el("div", "detail-card__head research-detail__head");
    const title = el("div", "research-detail__title");
    title.append(el("h3", "", factor.display_name || factor.name), badge(STATUS_LABEL[factor.status] || factor.status, STATUS_TONE[factor.status]));
    if (factor.ai_generated) title.append(badge("模型生成", "accent"));
    const experimentsBtn = el("button", "btn btn--ghost", "查看实验");
    experimentsBtn.type = "button";
    experimentsBtn.dataset.factorExperiments = factor.name;
    experimentsBtn.addEventListener("click", () => {
      ctx.factorExperimentFilter = factor.name;
      ctx.factorExperimentFilterLabel = factor.display_name || factor.name;
      ctx.bus.emit("factor:experiments");
      ctx.bus.emit("route", "factors/experiments");
    });
    const controls = el("div", "factors__controls");
    head.append(title, controls);
    card.append(head);

    const hypothesis = kvEl([["核心假设", factor.assumption, { wide: true, scroll: true }]]);
    hypothesis.classList.add("factors__hypothesis");
    card.append(hypothesis);

    const facts = kvEl([
        ["时间尺度", TIMESCALE_LABEL[factor.timescale] || factor.timescale],
        ["当前版本", factor.version || "—"],
        ["方向", DIRECTION_LABEL[factor.direction] || factor.direction],
        ["最近评估", fmtTime(factor.last_evaluated_at)],
        ["更新时间", fmtTime(factor.updated_at)],
    ]);
    facts.classList.add("factors__facts");
    facts.setAttribute("aria-label", "因子信息");
    card.append(facts);

    const actions = el("div", "factors__actions");
    actions.setAttribute("role", "group");
    actions.setAttribute("aria-label", "因子操作");
    for (const target of statuses) {
      if (target === factor.status) continue;
      const btn = el("button", "btn btn--ghost", `转为${STATUS_LABEL[target] || target}`);
      btn.type = "button";
      btn.addEventListener("click", () => transition(factor.name, target));
      actions.append(btn);
    }
    const removeBtn = el("button", "btn btn--danger", "删除");
    removeBtn.type = "button";
    removeBtn.addEventListener("click", () => remove(factor.name));
    actions.append(removeBtn);
    controls.append(actions, experimentsBtn);

    if (factor.risk_notes) {
      card.append(el("h4", "", "风险提示"));
      card.append(el("p", "item-row__meta", factor.risk_notes));
    }

    const reviews = el("article", "card detail-card factors__reviews");
    reviews.append(createModelReviews(ctx, 'factor', factor.name));
    const evalRunsHost = el("section", "factors__evaluations");
    evalRunsHost.setAttribute("aria-label", "因子评价记录与结论");
    reviews.append(evalRunsHost);
    if (evalRefreshTimer) clearTimeout(evalRefreshTimer);
    const refreshRuns = async () => {
      await loadEvalRuns(factor.name, evalRunsHost);
      if (currentName === factor.name && evalRunsHost.isConnected) {
        evalRefreshTimer = setTimeout(refreshRuns, 15000);
      }
    };
    refreshRuns();

    detailHost.append(card, reviews);
  }

  async function transition(name, target) {
    try {
      await ctx.api.factorTransition(name, { target });
      ctx.bus.emit("toast", { kind: "success", title: `${name} 状态已改为${STATUS_LABEL[target] || target}` });
      await load();
    } catch (err) {
      toastError(ctx.bus, err, "因子状态修改失败");
    }
  }

  async function remove(name) {
    try {
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.factorRemove(name, extra),
        title: `删除因子 ${name}`,
        showPreview: false,
        confirmLabel: "确认删除",
        danger: true,
      });
      if (!result) return;
      currentName = "";
      ctx.bus.emit("toast", { kind: "success", title: `因子 ${name} 已移入回收站` });
      await load();
    } catch (err) {
      toastError(ctx.bus, err, "因子删除失败");
    }
  }

  
  async function loadEvalRuns(name, host) {
    try {
      const data = await ctx.api.factorEvalRuns(name);
      if (!host.isConnected || name !== currentName) return;
      host.querySelector(':scope > .factors__eval-error')?.remove();
      if (!data.items.length) {
        selectedEvalRuns.delete(name);
        host.replaceChildren();
        return;
      }
      const columns = [
        { key: "run_id", label: "记录", format: (value, row) => {
          const label = el("span", "factors__eval-id", value);
          label.title = `${value}${row.updated_at ? `\n更新于 ${fmtTime(row.updated_at)}` : ""}`;
          return label;
        } },
        {
          key: "state",
          label: "状态",
          format: (value) => badge(EVAL_STATE_LABEL[value] || value || "—", EVAL_STATE_TONE[value] || "muted"),
        },
        { key: "period", label: "日期区间" },
        { key: "primary_horizon", label: "持有期", format: (v) => (v ? `${v}日` : "—") },
        { key: "ic_mean", label: "Rank IC", num: true, format: (v) => fmtNum(v, 4) },
        { key: "icir_annualized", label: "ICIR（年化）", num: true, format: (v) => fmtNum(v, 3) },
      ];
      for (const column of [
        {key: "long_short_annualized", label: "多空年化收益", num: true, format: (v) => fmtPct(v)},
        {key: "max_drawdown", label: "最大回撤", num: true, format: (v) => fmtPct(v)},
      ]) {
        if (data.items.some(row => row[column.key] !== null && row[column.key] !== undefined)) columns.push(column);
      }
      const selectRun = row => {
        selectedEvalRuns.set(name, row.run_id);
        updateEvalSelection(name, host);
        showEvalRun(name, row.run_id, host);
      };
      const previousTable = host.querySelector('.factors__eval-runs');
      const table = tableEl(columns, newestFirst(data.items, ['created_at', 'updated_at']), {
        ...previousTable?.pageState?.(), onRowClick: selectRun,
        onRowRender: (row, item) => {
          row.dataset.evalRun = item.run_id;
          const active = selectedEvalRuns.get(name) === item.run_id;
          row.classList.toggle('is-active', active);
          row.setAttribute('aria-expanded', String(active));
        },
      });
      table.classList.add("factors__eval-runs");
      table.setAttribute('aria-label', '评价记录，选择记录查看结论');
      // Polling replaces only the list. The expanded conclusion keeps its DOM and scroll.
      const previous = host.querySelector('.factors__eval-runs');
      if (previous) {
        const {scrollTop, scrollLeft} = previous;
        const focused = previous.contains(document.activeElement) ? document.activeElement : null;
        const focusedRun = focused?.closest('[data-eval-run]')?.dataset.evalRun;
        previous.replaceWith(table);
        table.scrollTop = scrollTop;
        table.scrollLeft = scrollLeft;
        if (focusedRun) {
          [...table.querySelectorAll('[data-eval-run]')].find(row => row.dataset.evalRun === focusedRun)?.focus({preventScroll: true});
        } else if (focused) table.focus({preventScroll: true});
      } else {
        host.replaceChildren(table);
      }
      updateEvalSelection(name, host);
      const selected = selectedEvalRuns.get(name);
      if (selected && !host.querySelector('[data-eval-run-detail]')) showEvalRun(name, selected, host);
    } catch (err) {
      if (!host.isConnected || name !== currentName) return;
      let error = host.querySelector(':scope > .factors__eval-error');
      if (!error) { error = el('p', 'item-row__meta factors__eval-error'); host.append(error); }
      error.textContent = `评价记录加载失败：${err.message}`;
    }
  }

  function updateEvalSelection(name, host) {
    const selected = selectedEvalRuns.get(name);
    host.querySelectorAll('[data-eval-run]').forEach(row => {
      const active = row.dataset.evalRun === selected;
      row.classList.toggle('is-active', active);
      row.setAttribute('aria-expanded', String(active));
    });
  }

  async function showEvalRun(name, runId, host) {
    const request = ++evalRequest;
    let detail = host.querySelector("[data-eval-run-detail]");
    if (!detail) {
      detail = el("section", "factors__eval-detail");
      detail.dataset.evalRunDetail = "";
      host.append(detail);
    }
    detail.hidden = false;
    const heading = el('div', 'factors__eval-heading');
    const title = el('div', 'factors__eval-title');
    title.append(el('h4', '', '评价结论'), el('p', 'item-row__meta', runId));
    const close = el('button', 'btn btn--ghost btn--sm', '收起结论');
    close.type = 'button';
    close.onclick = () => {
      ++evalRequest;
      selectedEvalRuns.delete(name);
      detail.hidden = true;
      updateEvalSelection(name, host);
    };
    heading.append(title, close);
    detail.replaceChildren(heading, el('p', 'item-row__meta', '正在读取评价结论…'));
    try {
      const data = await ctx.api.factorEvalRun(name, runId);
      if (request !== evalRequest || name !== currentName || !detail.isConnected) return;
      detail.replaceChildren(heading);
      const labels = data.metrics?.labels || {};
      const label = labels.strict || labels.default || Object.values(labels)[0] || {};
      const variants = label.variants || {};
      const variant = variants.industry_size || variants.raw || Object.values(variants)[0] || {};
      const ic = variant.ic || {};
      const yearly = variant.yearly_ic || [];
      const direction = data.metrics?.direction === 'negative' ? -1 : 1;
      const positiveYears = yearly.filter((item) => Number(item.ic_mean) * direction > 0).length;
      const latest = yearly.length ? yearly[yearly.length - 1] : null;
      const g5 = variant.groups?.g5 || Object.values(variant.groups || {})[0] || {};
      const spread = g5.long_short || {};
      const metrics = kvEl([
          ["平均相关系数", fmtNum(ic.ic_mean, 4)],
          ["稳定性", fmtNum(ic.icir_annualized, 3)],
          ["有效交易日", ic.n_valid_days ?? "—"],
          ["符合方向年份", yearly.length ? `${positiveYears}/${yearly.length}` : "—"],
          ["最近一年", latest ? `${latest.year}，有效性 ${fmtNum(latest.ic_mean, 4)}` : "—"],
          ["分组收益差", fmtPct(spread.annualized_return)],
          ["分组最大回撤", fmtPct(spread.max_drawdown)],
          ["日均换手估算", fmtPct(spread.avg_daily_turnover_oneside)],
        ]);
      metrics.classList.add('factors__eval-metrics');
      detail.append(metrics);
    } catch (err) {
      if (request !== evalRequest || name !== currentName || !detail.isConnected) return;
      detail.replaceChildren(heading, el('p', 'factors__eval-error', `评价详情加载失败：${err.message}`));
      toastError(ctx.bus, err, "评价详情加载失败");
    }
  }

  

}

export async function mount(root, ctx) {
  const html = root.innerHTML;
  await mountTabs(root, ctx, 'factors', [
    {id: 'list', label: '因子库', html, mount: mountContent},
    {id: 'research', label: '新因子研究', component: 'factor-research'},
    {id: 'experiments', label: '因子实验', component: 'experiments'},
  ]);
}
