import {mountTabs} from '../../core/tabs.js';
import { renderState } from "../state-card/state-card.js";
import { runWithConfirm } from "../confirm-dialog/confirm-dialog.js";
import { createChat } from "../chat-stream/chat-stream.js";
import { createChatDialog } from "../chat-stream/chat-dialog.js";
import { hydrateIcons } from "../../core/icons.js";
import { createSelect } from "../../core/select.js";
import { toastError } from "../../core/toast.js";
import { captureLogScroll, updateTaskLog } from "../../core/task-log.js";
import { badge, fmtNum, fmtPct, fmtTime } from "../../core/fmt.js";
import { el, kvEl, selectMarkup, tableEl } from "../../core/dom.js";

const STATUS_LABEL = { draft: "研究中", active: "已启用", disabled: "已停用", retired: "已淘汰" };
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
const TASK_STATUS_LABEL = {
  running: "进行中",
  completed: "已完成",
  failed: "失败",
  interrupted: "已中断",
};
const EVAL_STATE_LABEL = { running: "评价中", completed: "已完成", failed: "失败" };
const EVAL_STATE_TONE = { running: "accent", completed: "safe", failed: "danger", interrupted: "warn", queued: "warn" };

// 因子研究入口
async function mountContent(root, ctx) {
  const listHost = root.querySelector("[data-list]");
  const detailHost = root.querySelector("[data-detail]");
  const statusRoot = root.querySelector("[data-status-select]");
  statusRoot.innerHTML = selectMarkup("全部状态");
  await hydrateIcons(root);

  const statusSelect = createSelect(statusRoot, { onChange: () => load() });
  const candidateTaskHost = root.querySelector("[data-candidate-task]");
  const aiResearchBtn = root.querySelector("[data-ai-research]");
  aiResearchBtn.addEventListener("click", () => openAIResearchDialog());

  let optionsReady = false;
  let currentName = "";
  let statuses = [];
  let pollTimer = null;
  let evalRefreshTimer = null;

  ctx.bus.on("route", (id) => {
    if (id === "factors") load();
    else if (evalRefreshTimer) {
      clearTimeout(evalRefreshTimer);
      evalRefreshTimer = null;
    }
  });
  await load();

  async function load() {
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
            ...data.statuses.map((value) => ({ value, label: STATUS_LABEL[value] || value })),
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
    if (!items.length) {
      renderState(listHost, {
        kind: "empty",
        title: "暂无注册因子",
        detail: "点击“研究新因子”，填写研究想法。",
      });
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
      meta.append(badge(STATUS_LABEL[item.status] || item.status));
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
    await renderState(detailHost, { kind: "loading", title: "加载因子详情…" });
    try {
      const data = await ctx.api.factor(name);
      renderDetail(data);
    } catch (err) {
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
    const card = el("article", "card detail-card");

    const head = el("div", "detail-card__head");
    head.append(el("h3", "", factor.display_name || factor.name));
    head.append(badge(STATUS_LABEL[factor.status] || factor.status));
    if (factor.ai_generated) head.append(badge("模型生成", "accent"));
    if (factor.production_permission) head.append(badge("已获准正式使用", "safe"));
    card.append(head);

    card.append(
      kvEl([
        ["核心假设", factor.assumption, { wide: true, scroll: true }],
        ["适用市场", (factor.applicable_markets || []).join("、")],
        ["适用标的", (factor.applicable_instruments || []).join("、")],
        ["时间尺度", TIMESCALE_LABEL[factor.timescale] || factor.timescale],
        ["方向", DIRECTION_LABEL[factor.direction] || factor.direction],
        ["最近评估", fmtTime(factor.last_evaluated_at)],
        ["更新时间", fmtTime(factor.updated_at)],
      ]),
    );

    const actions = el("div", "detail-card__head detail-card__head--actions");
    actions.append(el("h4", "", "人工权限与归档"));
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
    card.append(actions);

    if (factor.risk_notes) {
      card.append(el("h4", "", "风险提示"));
      card.append(el("p", "item-row__meta", factor.risk_notes));
    }

    card.append(el("h4", "", "评价记录"));
    const evalRunsHost = el("div");
    card.append(evalRunsHost);
    if (evalRefreshTimer) clearTimeout(evalRefreshTimer);
    const refreshRuns = async () => {
      await loadEvalRuns(factor.name, evalRunsHost);
      if (currentName === factor.name) {
        evalRefreshTimer = setTimeout(refreshRuns, 15000);
      }
    };
    refreshRuns();

    detailHost.append(card);
  }

  async function transition(name, target) {
    try {
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.factorTransition(name, { target, ...extra }),
        title: `修改因子状态：${target}`,
        message:
          target === "active"
            ? "启用后可用于正式策略。"
            : "停用或淘汰后，将收回正式使用权限。",
        confirmLabel: "确认修改",
      });
      if (!result) return;
      ctx.bus.emit("toast", { kind: "success", title: `${name} 状态已改为 ${target}` });
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
        message: "源码移入回收站可恢复，数据表元数据不可恢复。",
        requireDecidedBy: true,
        notePlaceholder: "删除原因",
        confirmLabel: "确认删除",
        danger: true,
      });
      if (!result) return;
      currentName = "";
      ctx.bus.emit("toast", { kind: "success", title: `已删除因子 ${name}` });
      await load();
    } catch (err) {
      toastError(ctx.bus, err, "因子删除失败");
    }
  }

  
  async function openAIResearchDialog() {
    let recommendationData;
    try {
      aiResearchBtn.disabled = true;
      recommendationData = await ctx.api.factorEvolutionModelRecommendations();
    } catch (err) {
      toastError(ctx.bus, err, "模型匹配加载失败");
      return;
    } finally {
      aiResearchBtn.disabled = false;
    }
    const modelItems = (recommendationData.items || []).filter((item) => item.selectable);
    if (!modelItems.length) {
      ctx.bus.emit("toast", {
        kind: "error",
        title: "没有可执行因子研究的模型",
        detail: "请在“模型连接”配置密钥和模型能力。",
      });
      return;
    }
    let chat, modelSelect;
    const { toolbar, chatHost, historyHost, actions, close } = createChatDialog({
      title: "研究新因子",
      history: true,
      onClose: () => { chat?.destroy(); modelSelect?.destroy(); },
    });
    const modelField = el("div", "chat-dialog__model");
    const modelLabel = el("span", "", "研究模型");
    modelLabel.id = "factor-research-model-label";
    const modelSelectRoot = el("div", "select");
    modelSelectRoot.innerHTML = selectMarkup("研究模型");
    modelSelectRoot
      .querySelector("[data-select-trigger]")
      ?.setAttribute("aria-labelledby", modelLabel.id);
    const modelTrigger = modelSelectRoot.querySelector("[data-select-trigger]");
    modelSelect = createSelect(modelSelectRoot, {});
    const modelOptions = modelItems.map(item => ({
      value: item.name,
      label: `${item.name}${item.recommended ? "（自动匹配）" : ""}`,
    }));
    modelSelect.setOptions(
      modelOptions,
      recommendationData.recommended,
      "研究模型",
    );
    modelField.append(modelLabel, modelSelectRoot);
    toolbar.append(modelField);
    const start = el("button", "btn btn--primary", "开始自动研究");
    start.type = "button";
    actions.append(start);
    start.disabled = true;
    chat = createChat(chatHost, {
      historyHost,
      historyKey: "factor-research",
      getMetadata: () => ({ model: modelSelect.value }),
      onRestore: metadata => modelSelect.setOptions(modelOptions, metadata.model || recommendationData.recommended),
      title: "研究假设",
      onStateChange: (state) => {
        start.disabled = state.busy || state.disabled || state.loading || !state.hasUserMessages;
        modelTrigger.disabled = state.busy || state.disabled || state.loading;
      },
      greeting:
        "填写研究假设、作用周期和失效条件。",
      placeholder: "输入研究假设…",
      send: async (history) => {
        const res = await ctx.api.factorResearchChat({
          model: modelSelect.value,
          messages: history,
        });
        return { text: res.reply, reasoning: res.reasoning || "", model: res.model || modelSelect.value };
      },
    });

    start.addEventListener("click", async () => {
      if (chat.busy) return;
      if (!chat.history.some((item) => item.role === "user")) {
        ctx.bus.emit("toast", { kind: "info", title: "请先填写研究想法" });
        return;
      }
      start.disabled = true;
      start.textContent = "正在启动…";
      chat.setDisabled(true);
      modelTrigger.disabled = true;
      try {
        const result = await ctx.api.factorResearchStart({
          model: modelSelect.value,
          messages: chat.context,
        });
        close();
        ctx.bus.emit("toast", { kind: "info", title: "自动研究已启动" });
        pollTask(result.task_id, {
          host: candidateTaskHost,
          buttons: [aiResearchBtn],
          onResult: renderAIResearchResult,
          onComplete: load,
        });
      } catch (err) {
        start.textContent = "开始自动研究";
        chat.setDisabled(false);
        modelTrigger.disabled = false;
        toastError(ctx.bus, err, "自动研究启动失败");
      }
    });
  }

  function renderAIResearchResult(result) {
    const target = candidateTaskHost.querySelector(".card") || candidateTaskHost;
    const summary = el("section", "factors__research-result");
    const head = el("div", "detail-card__head");
    head.append(el("h4", "", result.display_name || "新因子"), badge("已进入自动迭代", "safe"));
    summary.append(head);
    summary.append(el("p", "", result.assumption || "金融假设已登记。"));
    summary.append(
      el(
        "p",
        "item-row__meta",
        "语法检查和数据试运行已通过，正在评价并比较研究结果。正式使用仍需人工批准。",
      ),
    );
    const open = el("button", "btn btn--ghost", "查看自动研究进展");
    open.type = "button";
    open.addEventListener("click", () => ctx.bus.emit("route", "factors/experiments"));
    summary.append(open);
    target.append(summary);
  }

  async function loadEvalRuns(name, host) {
    try {
      const data = await ctx.api.factorEvalRuns(name);
      host.innerHTML = "";
      if (!data.items.length) {
        host.append(el("p", "item-row__meta", "暂无评价记录。"));
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
      const table = tableEl(columns, data.items, {onRowClick: row => showEvalRun(name, row.run_id, host)});
      table.classList.add("factors__eval-runs");
      host.append(table);
    } catch (err) {
      host.append(el("p", "item-row__meta", `评价记录加载失败：${err.message}`));
    }
  }

  async function showEvalRun(name, runId, host) {
    let detail = host.querySelector("[data-eval-run-detail]");
    if (!detail) {
      detail = el("div");
      detail.dataset.evalRunDetail = "";
      host.append(detail);
    }
    detail.innerHTML = "";
    try {
      const data = await ctx.api.factorEvalRun(name, runId);
      detail.append(el("h4", "", "评价结论"));
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
      detail.append(
        kvEl([
          ["平均相关系数", fmtNum(ic.ic_mean, 4)],
          ["稳定性", fmtNum(ic.icir_annualized, 3)],
          ["有效交易日", ic.n_valid_days ?? "—"],
          ["符合方向年份", yearly.length ? `${positiveYears}/${yearly.length}` : "—"],
          ["最近一年", latest ? `${latest.year}，有效性 ${fmtNum(latest.ic_mean, 4)}` : "—"],
          ["分组收益差", fmtPct(spread.annualized_return)],
          ["分组最大回撤", fmtPct(spread.max_drawdown)],
          ["日均换手估算", fmtPct(spread.avg_daily_turnover_oneside)],
        ]),
      );
      const weak = Math.abs(Number(ic.ic_mean) || 0) < 0.01;
      const unstable = yearly.length >= 2 && positiveYears / yearly.length < 0.67;
      const latestWeak = latest && Number(latest.ic_mean) * direction <= 0;
      let conclusion = "该区间存在正向信号，仍需检验滚动表现和交易成本。";
      if (weak) conclusion = "平均信号较弱，单个分组的高收益不足以支持假设。";
      else if (latestWeak) conclusion = "历史整体存在信号，但最近一年转弱或反向，需要继续观察。";
      else if (unstable) conclusion = "因子在不同年份方向不一致，暂不适合直接用于组合。";
      detail.append(el("p", "factors__eval-conclusion", conclusion));
      if ((data.charts || []).length) {
        detail.append(el("h4", "", "结果数据"));
        const links = el("p", "item-row__meta");
        data.charts.forEach((filename, index) => {
          const link = el("a", "", `下载数据 ${index + 1}`);
          link.href = ctx.api.factorEvalChartUrl(name, runId, filename);
          link.download = filename;
          links.append(link, " ");
        });
        detail.append(links);
      }
    } catch (err) {
      toastError(ctx.bus, err, "评价详情加载失败");
    }
  }

  
  function pollTask(taskId, { host, buttons = [], onComplete, onResult }) {
    if (pollTimer) clearTimeout(pollTimer);
    for (const b of buttons) b.disabled = true;

    async function tick() {
      try {
        const task = await ctx.api.task(taskId);
        renderTaskCard(host, task);
        if (task.status === "running") {
          pollTimer = setTimeout(tick, 2000);
          return;
        }
        for (const b of buttons) b.disabled = false;
        if (task.status === "completed") {
          ctx.bus.emit("toast", { kind: "success", title: `${task.name} 完成` });
          if (onResult) onResult(task.result || {});
          if (onComplete) await onComplete();
        } else {
          ctx.bus.emit("toast", { kind: "error", title: `${task.name} 失败`, detail: task.error });
        }
      } catch (err) {
        for (const b of buttons) b.disabled = false;
        toastError(ctx.bus, err, "任务状态查询失败");
      }
    }
    tick();
  }

  function renderTaskCard(host, task) {
    const previousLog = host.querySelector("[data-task-log]");
    const restoreScroll = captureLogScroll(previousLog);
    const card = el("article", "card detail-card");
    const head = el("div", "detail-card__head");
    head.append(el("h4", "", task.name));
    head.append(
      badge(
        TASK_STATUS_LABEL[task.status] || task.status,
        task.status === "completed" ? "safe" : task.status === "failed" ? "danger" : "accent",
      ),
    );
    card.append(head);
    const progress = task.progress || {};
    if (progress.step) {
      const pct = Number(progress.percent);
      card.append(
        el(
          "p",
          "factors__task-step",
          `${progress.step}${Number.isFinite(pct) ? ` · ${Math.round(pct)}%` : ""}`,
        ),
      );
    }
    if (task.error) card.append(el("p", "factors__task-error", task.error));
    const taskLog = updateTaskLog(previousLog, task.log, {
      limit: 8,
      subject: task.task_id || task.name,
    });
    if (taskLog) card.append(taskLog);
    host.replaceChildren(card);
    restoreScroll();
  }
}

export async function mount(root,ctx) { const html=root.innerHTML; await mountTabs(root,ctx,'factors',[{id:'list',label:'因子列表',html,mount:mountContent},{id:'experiments',label:'迭代实验',component:'experiments'}]); }
