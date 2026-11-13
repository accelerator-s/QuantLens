import { renderState } from "../state-card/state-card.js";
import { formDialog } from "../confirm-dialog/form-dialog.js";
import { runWithConfirm } from "../confirm-dialog/confirm-dialog.js";
import { hydrateIcons } from "../../core/icons.js";
import { createSelect } from "../../core/select.js";
import { toastError } from "../../core/toast.js";
import { badge, fmtNum, fmtPct, fmtTime, signClass } from "../../core/fmt.js";
import { codeEl, el, kvEl, selectMarkup, tableEl } from "../../core/dom.js";
import { captureLogScroll, updateTaskLog } from "../../core/task-log.js";

const IMPROVED_FILTER = [
  { value: "", label: "全部实验" },
  { value: "true", label: "仅改善" },
  { value: "false", label: "仅未改善" },
];

const STATUS_LABEL = {
  registered: "已登记",
  attached: "结果已保存",
  concluded: "已形成结论",
  aborted: "已中止",
};

const EVENT_LABEL = {
  result: "回测结果",
  comparison: "前次实验对比",
  ai_analysis: "模型复盘",
  conclusion: "结论",
  abort: "中止",
  note: "备注",
};

const EVO_EVENT_LABEL = {
  proposal_restarted: "从已完成的基线继续生成候选",
  candidate_invalidated: "候选无实质改动，已跳过重复计算",
  baseline_started: "基线实验已登记",
  window_completed: "窗口回测完成",
  baseline_completed: "基线回测完成",
  candidate_created: "生成新候选因子",
  version_evaluated: "候选评估完成",
  round_completed: "一轮进化完成",
  round_failed: "本轮进化失败",
  review_generation_failed: "模型复盘格式纠错失败，研究已继续",
  session_failed: "会话失败",
  session_finished: "进化结束",
  locked_test_completed: "锁定测试完成",
  locked_test_failed: "锁定测试失败",
  cancel_requested: "请求中止",
};


const EVO_EVENT_HIDDEN = new Set([
  "runtime_log",
  "session_created",
  "model_context_dispatched",
  "locked_test_reserved",
]);

const METRIC_LABEL = {
  
  annualized_excess_return: "年化超额收益",
  annualized_return: "年化收益",
  "returns.annualized_excess_return": "年化超额收益",
  "returns.annualized_return": "年化收益",
  "risk.sharpe": "夏普比率",
  "risk.max_drawdown": "最大回撤",
  "trading.annualized_turnover_oneside": "年化单边换手",
};


const PCT_METRICS = new Set([
  "returns.annualized_excess_return",
  "returns.annualized_return",
  "risk.max_drawdown",
]);

const CLASSIFICATION_LABEL = {
  validation_generalized: "泛化有效",
  recently_decayed: "近期衰减",
  in_sample_only: "仅样本内有效",
  not_effective: "无效",
};

function friendlyEvolutionError(error) {
  const text = String(error || "");
  if (!text) return null;
  if (text.includes("AI_OUTPUT_SCHEMA_INVALID")) {
    return "旧任务因模型返回格式错误而停止。新任务会尝试纠正格式，复盘格式错误不再中止研究。";
  }
  return text.split("\n")[0];
}


export async function mount(root, ctx) {
  await hydrateIcons(root);

  const listHost = root.querySelector("[data-list]");
  const detailHost = root.querySelector("[data-detail]");
  const filterHost = root.querySelector("[data-improved-filter]");
  const evolutionList = root.querySelector("[data-evolution-list]");
  const evolutionDetail = root.querySelector("[data-evolution-detail]");
  filterHost.innerHTML = selectMarkup("全部实验");
  const filterSelect = createSelect(filterHost, { onChange: () => load() });
  filterSelect.setOptions(IMPROVED_FILTER, "");

  root.querySelector("[data-evolution-start]").addEventListener("click", () => startEvolution());

  let currentId = "";
  let detailPoll = null;
  const taskPolls = new Map();
  const polledResearchIds = new Set();
  let chainItems = null;
  let lastItems = null;
  let evolutionItems = [];
  
  let sessionFilter = null;

  function applyExperimentFilter(items) {
    if (!sessionFilter) return items;
    
    return items.filter(
      (item) => sessionFilter.expIds.has(item.exp_id),
    );
  }

  function renderFiltered() {
    if (lastItems) renderList(applyExperimentFilter(lastItems));
  }

  async function selectEvolution(researchId) {
    if (detailPoll) clearTimeout(detailPoll);
    detailPoll = null;
    sessionFilter = { researchId, expIds: new Set() };
    evolutionList.querySelectorAll("tbody tr").forEach(row => {
      row.classList.toggle("is-active", row.dataset.researchId === researchId);
    });
    await showEvolution(researchId);
    renderFiltered();
  }

  ctx.bus.on("route", (id) => {
    if (id === "experiments" || id === "factors/experiments") {
      load();
      loadEvolutions();
    }
  });
  await load();
  await loadEvolutions();

  async function loadEvolutions() {
    try {
      const data = await ctx.api.factorEvolutions();
      renderEvolutions(data.items || []);
    } catch (err) {
      await renderState(evolutionList, {
        kind: "error",
        title: "自动进化状态加载失败",
        detail: err.message,
        retry: () => loadEvolutions(),
      });
    }
  }

  function renderEvolutions(items) {
    evolutionItems = items;
    evolutionList.innerHTML = "";
    if (!items.length) {
      evolutionList.append(el("p", "item-row__meta", "暂无实验"));
      evolutionDetail.innerHTML = "";
      return;
    }
    
    evolutionList.append(el("div", "item-list__group", "单因子迭代研究"));
    const table = tableEl(
      [
        { key: "base_factor_display_name", label: "基础因子" },
        { key: "model_name", label: "模型" },
        { key: "started_at", label: "启动时间", format: (value) => fmtTime(value) },
        { key: "state", label: "状态", format: (value, row) => badge(...stateBadgeArgs(row)) },
        { key: "phase", label: "研究进展", format: (value, row) => `${value || "等待执行"} · 第 ${row.iteration || 0} 轮 · ${fmtPct(row.progress, 1)}` },
      ],
      items,
      { onRowClick: (item) => selectEvolution(item.research_id) },
    );
    table.classList.add("evolution-sessions");
    table.querySelectorAll("tbody tr").forEach((row, index) => {
      row.dataset.researchId = items[index].research_id;
      row.title = items[index].research_id;
    });
    evolutionList.append(table);
    const target = items.find((i) => i.research_id === sessionFilter?.researchId) || items[0];
    selectEvolution(target.research_id);
  }

  async function showEvolution(researchId, schedule = true) {
    try {
      const previousLog = evolutionDetail.querySelector("[data-task-log]");
      const retainedLog = previousLog?.dataset.subject === researchId ? previousLog : null;
      const data = await ctx.api.factorEvolution(researchId);
      // Also restore tasks served by a process started before task linkage was added.
      if (data.task === undefined) {
        const jobs = await ctx.api.jobs();
        data.task = (jobs.items || []).find(task => task.name === `factor-evolution:${researchId}`);
      }
      if (data.experiment_ids === undefined) {
        const start = (data.events || []).find(event => event.event_type === "session_created")?.created_at || data.status?.started_at;
        const boundaries = evolutionItems.filter(item => item.research_id !== researchId &&
          item.base_factor === data.config?.base_factor && item.started_at > start).map(item => item.started_at);
        if (data.status?.finished_at) boundaries.push(data.status.finished_at);
        const end = boundaries.sort()[0];
        const roots = (lastItems || []).filter(item => item.subject_type === "factor" &&
          item.subject_name === data.config?.base_factor && item.profile_name === data.config?.profile_name &&
          !item.parent_exp_id && start && item.created_at >= start && (!end || item.created_at < end));
        data.experiment_ids = roots.length === 1 ? [roots[0].exp_id] : [];
      }
      if (sessionFilter?.researchId !== researchId) return;
      if (sessionFilter?.researchId === researchId) {
        const ids = new Set(
          [...(data.experiment_ids || []), ...(data.events || []).flatMap((event) =>
            [event.payload?.experiment_id, event.payload?.result?.experiment_id])].filter(Boolean),
        );
        const changed = ids.size !== sessionFilter.expIds.size || [...ids].some(id => !sessionFilter.expIds.has(id));
        const revision = (data.events || []).filter(event =>
          ["window_completed", "version_evaluated", "round_completed", "round_failed", "session_finished", "session_failed"].includes(event.event_type)).length;
        const resultsChanged = sessionFilter.revision !== revision;
        sessionFilter.revision = revision;
        sessionFilter.expIds = ids;
        sessionFilter.state = data.lifecycle_state === "paused" ? "paused" : data.status?.state;
        sessionFilter.phase = data.status?.phase;
        sessionFilter.error = data.task?.error || data.status?.error;
        if ((changed || resultsChanged) && ids.size) await load(true);
        else if (!ids.size) renderFiltered();
      }
      const status = data.status || {};
      const lifecycleState = data.lifecycle_state || "";
      const taskState = data.task?.status === "interrupted" ? "paused"
        : data.task?.status === "failed" ? "failed"
        : data.task?.progress?.state === "queued" ? "queued" : status.state;
      const displayStatus = { ...status, state: lifecycleState === "paused" ? "paused" : taskState };
      const sessionRow = evolutionList.querySelector(`tr[data-research-id="${CSS.escape(researchId)}"]`);
      if (sessionRow) {
        sessionRow.cells[3].replaceChildren(badge(...stateBadgeArgs(displayStatus)));
        sessionRow.cells[4].textContent = `${status.phase || "等待执行"} · 第 ${status.iteration || 0} 轮 · ${fmtPct(status.progress, 1)}`;
      }
      const exposure = (data.locked_test_exposures || [])[0] || {};
      const box = el("article", "card evolution-progress");
      const head = el("div", "detail-card__head");
      head.append(el("strong", "", researchId), badge(...stateBadgeArgs(displayStatus)));
      if (lifecycleState === "paused") {
        const resume = el("button", "btn btn--ghost", "继续研究");
        resume.type = "button";
        resume.addEventListener("click", async () => {
          resume.disabled = true;
          try {
            const started = await ctx.api.factorEvolutionResume(researchId);
            ctx.bus.emit("toast", { kind: "info", title: `已恢复因子研究：${researchId}` });
            pollEvolution(researchId, started.task_id);
          } catch (err) {
            toastError(ctx.bus, err, "因子研究恢复失败");
            resume.disabled = false;
          }
        });
        head.append(resume);
      }
      box.append(head);
      if (status.state === "running") {
        const progress = el("div", "evolution-progress__track");
        const fill = el("span", "evolution-progress__fill");
        fill.style.width = `${Math.max(0, Math.min(100, Number(status.progress || 0) * 100))}%`;
        progress.append(fill);
        box.append(progress);
      }
      const evaluatedEvents = [...(data.events || [])]
        .reverse()
        .filter((event) => event.event_type === "version_evaluated");
      const evaluated =
        evaluatedEvents.find(
          (event) => event.payload?.version_id === status.best_version_id,
        ) || evaluatedEvents[0];
      const generalization = evaluated?.payload?.generalization || {};
      const lockedCompleted = [...(data.events || [])]
        .reverse()
        .find((event) => event.event_type === "locked_test_completed");
      const finalClassification =
        lockedCompleted?.payload?.generalization_classification ||
        generalization.classification;
      const aiModels = data.ai_models || {};
      const candidateAI = aiModels.candidate
        ? `${aiModels.candidate}${aiModels.candidate_source === "current_route" && !data.config?.model_name ? "（自动选择）" : ""}`
        : null;
      const reviewAI = aiModels.review
        ? `${aiModels.review}${aiModels.review_source === "current_route" && !data.config?.model_name ? "（自动选择）" : ""}`
        : null;
      box.append(
        kvEl(
          [
            ["研究规则", data.profile_display_name || evolutionItems.find(item => item.research_id === researchId)?.profile_display_name || data.config?.profile_name],
            ["候选生成模型", candidateAI],
            ["复盘模型", reviewAI],
            ["当前阶段", displayStatus.state === "running" ? status.phase : null, { wide: true }],
            ["轮次", `${status.iteration}（成功 ${status.successful_rounds} / 失败 ${status.failed_rounds}）`],
            ["连续无改善", status.no_improvement_count > 0 ? `${status.no_improvement_count} 轮` : null],
            ["运行时间", `${fmtNum(displayStatus.state === "running" && status.started_at
              ? Math.max(status.elapsed_seconds || 0, (new Date(data.task?.finished_at || Date.now()) - new Date(status.started_at)) / 1000)
              : status.elapsed_seconds)} 秒`],
            ["最佳方案", status.best_version_id || null],
            ["泛化结论", finalClassification ? CLASSIFICATION_LABEL[finalClassification] || finalClassification : null],
            ["训练收益中位数", pctNode(generalization.train_median_excess_return)],
            ["验证收益中位数", pctNode(generalization.validation_median_excess_return)],
            ["泛化落差", pctNode(generalization.generalization_gap)],
            ["锁定测试回测", runLink(exposure.run_id)],
            ["结束原因", status.terminal_reason || null],
            ["错误", friendlyEvolutionError(data.task?.error || status.error)],
          ].filter(([, value]) => value !== null && value !== undefined && value !== ""),
        ),
      );
      const folds = evaluated?.payload?.folds || [];
      const windows = (data.events || []).filter(event => event.event_type === "window_completed");
      if (!folds.length && windows.length) {
        box.append(el("h4", "evolution-progress__title", "已完成的窗口回测"), tableEl([
          { key: "fold_id", label: "窗口" },
          { key: "window_name", label: "区间", format: value => value === "train" ? "训练" : "验证" },
          { key: "run_id", label: "回测", format: runLink },
        ], windows.map(event => event.payload)));
      }
      if (folds.length) {
        box.append(
          el("h4", "evolution-progress__title", "滚动验证"),
          tableEl(
            [
              { key: "fold_id", label: "窗口" },
              {
                key: "train_window",
                label: "训练区间",
                format: (value) => `${value.start}..${value.end}`,
              },
              {
                key: "validation_window",
                label: "验证区间",
                format: (value) => `${value.start}..${value.end}`,
              },
              {
                key: "train",
                label: "训练回测",
                format: (value) => runLink(value?.run_id),
              },
              {
                key: "validation",
                label: "验证回测",
                format: (value) => runLink(value?.run_id),
              },
              {
                key: "validation",
                label: "验证超额",
                num: true,
                format: (value) => pctNode(value?.metrics?.returns?.annualized_excess_return),
              },
            ],
            folds,
          ),
        );
      }
      const recent = (data.events || [])
        .filter((event) => !EVO_EVENT_HIDDEN.has(event.event_type))
        .slice(-6);
      if (recent.length) {
        box.append(el("h4", "evolution-progress__title", "近期动态"));
        const list = el("div", "evolution-events");
        for (const event of recent) {
          const row = el("div", "evolution-events__row");
          row.append(
            el("span", "evolution-events__time", fmtTime(event.created_at)),
            el("span", "", EVO_EVENT_LABEL[event.event_type] || event.event_type),
          );
          list.append(row);
        }
        box.append(list);
      }
      if (data.research_report) {
        const report = data.research_report;
        const content = await ctx.api.researchReportContent(report.object_id);
        box.append(
          el("h4", "evolution-progress__title", "研究报告（数值已核验）"),
          codeEl(content.content),
        );
        if (report.state === "validated") {
          const approve = el("button", "btn btn--ghost", "人工批准报告");
          approve.type = "button";
          approve.addEventListener("click", async () => {
            approve.disabled = true;
            try {
              await runWithConfirm({
                execute: (p) => ctx.api.researchReportApprove(report.object_id, {
                  confirm: p.confirm,
                  decided_by: p.decided_by || "",
                  reason: p.note || "",
                }),
                title: "批准因子研究报告",
                requireDecidedBy: true,
                notePlaceholder: "填写对指标依据与研究局限的审阅意见",
                confirmLabel: "批准报告",
              });
              await showEvolution(researchId, false);
            } catch (err) {
              toastError(ctx.bus, err, "研究报告批准失败");
              approve.disabled = false;
            }
          });
          box.append(approve);
        }
      }
      const lifecycleVersions = data.factor_versions || [];
      if (lifecycleVersions.length) {
        box.append(
          el("h4", "evolution-progress__title", "因子方案与使用权限"),
          tableEl(
            [
              { key: "object_id", label: "方案" },
              { key: "state", label: "状态", format: (value) => badge(value) },
              { key: "change", label: "变更" },
              { key: "action", label: "人工审批", format: (value) => value },
            ],
            lifecycleVersions.map((item) => ({
              ...item,
              change: item.factor_version.change_summary,
              action: factorProductionAction(item, data.research_report, researchId),
            })),
          ),
        );
      }
      if (sessionFilter?.researchId !== researchId) return;
      const restoreScroll = captureLogScroll(retainedLog);
      const eventLines = (data.events || []).map(event => {
        const payload = event.payload || {};
        return `${fmtTime(event.created_at)}  ${payload.message || EVO_EVENT_LABEL[event.event_type] || event.event_type}${payload.run_id ? ` · ${payload.run_id}` : ""}`;
      });
      const lines = data.task?.log?.length ? data.task.log : eventLines;
      const log = updateTaskLog(retainedLog, lines.length ? lines : [status.phase || "等待研究任务执行"], {
        limit: 200, subject: researchId,
      });
      if (log && !retainedLog) log.open = true;
      evolutionDetail.replaceChildren(...[box, log].filter(Boolean));
      restoreScroll();
      if (schedule && status.state === "running" && !polledResearchIds.has(researchId)) {
        if (detailPoll) clearTimeout(detailPoll);
        detailPoll = setTimeout(() => showEvolution(researchId), 2000);
      }
    } catch (err) {
      toastError(ctx.bus, err, "自动进化详情加载失败");
    }
  }

  function factorProductionAction(item, report, researchId) {
    if (item.state !== "shadow") return "—";
    if (report?.state !== "approved") return "先批准研究报告";
    const button = el("button", "btn btn--ghost", "批准正式使用");
    button.type = "button";
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await runWithConfirm({
          execute: (p) => ctx.api.factorVersionPromote(item.object_id, {
            confirm: p.confirm,
            decided_by: p.decided_by || "",
            reason: p.note || "",
          }),
          title: "批准因子使用权限",
          message: `因子方案 ${item.object_id} 将获准正式使用，不会自动切换策略。`,
          requireDecidedBy: true,
          notePlaceholder: "记录锁定测试、稳定性和风险审阅结论",
          confirmLabel: "批准因子",
        });
        await showEvolution(researchId, false);
      } catch (err) {
        toastError(ctx.bus, err, "因子使用权限批准失败");
        button.disabled = false;
      }
    });
    return button;
  }

  async function startEvolution() {
    try {
      const [factorData, strategyData, recommendationData] = await Promise.all([
        ctx.api.factors(),
        ctx.api.strategies({ include_internal: true }),
        ctx.api.factorEvolutionModelRecommendations(),
      ]);
      const strategies = (strategyData.items || strategyData || []).filter(
        (item) => item.status !== "disabled",
      );
      const details = await Promise.all(
        strategies.map((item) => ctx.api.strategy(item.profile_name)),
      );
      const eligible = new Set(
        details
          .map((item) => item.resolved?.factors?.items || [])
          .filter((items) => items.length === 1)
          .map((items) => items[0].name),
      );
      const factors = (factorData.items || []).filter((item) => eligible.has(item.name));
      if (!factors.length) {
        throw new Error("请先添加因子和单因子策略配置。");
      }
      const models = (recommendationData.items || []).filter((item) => item.selectable);
      if (!models.length) {
        throw new Error("没有可执行因子研究的模型，请检查模型连接和任务配置。");
      }
      const values = await formDialog({
        title: "新建实验",
        message:
          "模型按回测结果修改因子，逐版保存实验记录。回测区间与策略由研究规则确定。",
        submitLabel: "启动",
        fields: [
          {
            key: "experiment_type",
            label: "实验类型",
            kind: "select",
            options: [
              { value: "factor_evolution", label: "单因子自动进化（因子实验）" },
              { value: "factor_batch", label: "批量研究全部可用因子（最多 16 个）" },
            ],
          },
          {
            key: "base_factor",
            label: "基础因子",
            kind: "select",
            options: factors.map((item) => ({
              value: item.name,
              label: item.display_name || item.name,
            })),
          },
          {
            key: "model_name",
            label: "研究模型（优先使用所选模型）",
            kind: "select",
            value: recommendationData.recommended,
            hint: recommendationData.policy,
            options: models.map((item) => ({
              value: item.name,
              label: `${item.name}${item.recommended ? "（自动匹配）" : ""}`,
            })),
          },
        ],
      });
      if (!values) return;
      if (values.experiment_type === "factor_batch") {
        const started = await ctx.api.factorEvolutionBatchStart({
          base_factors: factors.slice(0, 16).map((item) => item.name),
          model_name: values.model_name,
        });
        ctx.bus.emit("toast", {
          kind: started.errors?.length ? "warn" : "info",
          title: `已调度 ${started.items?.length || 0} 个因子研究任务`,
          detail: started.errors?.map((item) => item.message).join("\n") || "",
        });
        await loadEvolutions();
        for (const item of started.items || []) pollEvolution(item.research_id, item.task_id);
      } else {
        const started = await ctx.api.factorEvolutionStart({
          base_factor: values.base_factor,
          model_name: values.model_name,
        });
        ctx.bus.emit("toast", { kind: "info", title: `自动进化已启动：${started.research_id}` });
        await loadEvolutions();
        pollEvolution(started.research_id, started.task_id);
      }
    } catch (err) {
      toastError(ctx.bus, err, "自动进化启动失败");
    }
  }

  function pollEvolution(researchId, taskId) {
    const previous = taskPolls.get(taskId);
    if (previous) clearTimeout(previous);
    polledResearchIds.add(researchId);
    async function tick() {
      try {
        const task = await ctx.api.task(taskId);
        if (sessionFilter?.researchId === researchId) {
          await showEvolution(researchId, false);
        }
        if (task.log?.length && sessionFilter?.researchId === researchId) {
          const taskLog = updateTaskLog(
            evolutionDetail.querySelector("[data-task-log]"),
            task.log,
            { limit: 12, subject: researchId },
          );
          if (taskLog && taskLog.parentNode !== evolutionDetail) evolutionDetail.append(taskLog);
        }
        if (task.status === "running") {
          taskPolls.set(taskId, setTimeout(tick, 2000));
          return;
        }
        taskPolls.delete(taskId);
        polledResearchIds.delete(researchId);
        await Promise.all([loadEvolutions(), load()]);
        const kind = task.status === "completed" ? "success" : "error";
        ctx.bus.emit("toast", {
          kind,
          title: task.status === "completed" ? "自动进化已结束" : "自动进化任务失败",
          detail: task.error || "",
        });
      } catch (err) {
        taskPolls.delete(taskId);
        polledResearchIds.delete(researchId);
        toastError(ctx.bus, err, "自动进化状态查询失败");
      }
    }
    tick();
  }

  async function load(quiet = false) {
    chainItems = null;
    if (!quiet) await renderState(listHost, { kind: "loading", title: "加载实验…" });
    try {
      const data = await ctx.api.experiments({ improved: filterSelect.value });
      lastItems = data.items;
      renderList(applyExperimentFilter(lastItems));
    } catch (err) {
      await renderState(listHost, {
        kind: "error",
        title: "实验列表加载失败",
        detail: err.message,
        retry: () => load(),
      });
    }
  }

  function improvedBadge(improved) {
    if (improved === true) return badge("改善", "safe");
    if (improved === false) return badge("未改善", "danger");
    return badge("基线", "accent");
  }

  function renderList(items) {
    listHost.innerHTML = "";
    if (!items.length) {
      const running = ["created", "running", "queued"].includes(sessionFilter?.state);
      const filtered = Boolean(filterSelect.value);
      renderState(listHost, {
        kind: running && !filtered ? "loading" : "empty",
        title: filtered ? "没有符合筛选条件的实验" : running ? "研究进行中，等待基线实验登记" : sessionFilter ? "该会话尚无实验记录" : "暂无实验",
        detail: filtered ? "可切换为「全部实验」查看基线与待评价实验。" : sessionFilter
          ? sessionFilter.error || (running ? sessionFilter.phase || "任务正在执行，运行日志见上方。" : sessionFilter.state === "paused" ? "研究已暂停，可在上方查看日志并继续研究。" : "运行阶段和结束原因见上方会话详情。")
          : "点击右上角「新建实验」启动后，系统会自动写入完整记录。",
      });
      detailHost.innerHTML = "";
      return;
    }
    
    const groups = new Map();
    for (const item of items) {
      const key = `${item.subject_type}:${item.subject_name}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    }
    for (const [key, rows] of groups) {
      const type = key.slice(0, key.indexOf(":"));
      const name = rows[0].subject_display_name || key.slice(key.indexOf(":") + 1);
      const typeLabel = {
        factor: "因子",
        strategy: "策略",
        portfolio_policy: "组合政策",
      }[type] || type;
      listHost.append(el("div", "item-list__group", `${typeLabel} ${name}`));
      for (const item of rows) {
        const row = el("button", "item-row");
        row.type = "button";
        row.dataset.expId = item.exp_id;
        if (item.exp_id === currentId) row.classList.add("is-active");
        row.append(el("span", "item-row__title", item.display_name || "研究实验"));
        const meta = el("span", "item-row__meta");
        meta.append(badge(STATUS_LABEL[item.status] || item.status), improvedBadge(item.improved));
        if (item.annualized_excess_return != null) {
          const val = el("span", signClass(item.annualized_excess_return));
          val.textContent = `超额 ${fmtPct(item.annualized_excess_return)}`;
          meta.append(val);
        }
        if (item.profile_display_name) meta.append(el("span", "", item.profile_display_name));
        row.append(meta);
        row.addEventListener("click", () => {
          currentId = item.exp_id;
          listHost.querySelectorAll(".item-row").forEach((n) => {
            n.classList.toggle("is-active", n.dataset.expId === currentId);
          });
          loadDetail(item.exp_id);
        });
        listHost.append(row);
      }
    }
    if (!currentId || !items.some((i) => i.exp_id === currentId)) {
      currentId = items[0].exp_id;
      listHost.querySelector(".item-row")?.classList.add("is-active");
    }
    loadDetail(currentId);
  }

  async function loadDetail(expId) {
    await renderState(detailHost, { kind: "loading", title: "加载实验详情…" });
    try {
      const [data, compare] = await Promise.all([
        ctx.api.experiment(expId),
        ctx.api.experimentCompare(expId).catch(() => null),
      ]);
      if (!chainItems) {
        
        chainItems = await ctx.api
          .experiments()
          .then((d) => d.items || [])
          .catch(() => []);
      }
      renderDetail(data, compare);
    } catch (err) {
      await renderState(detailHost, {
        kind: "error",
        title: "实验详情加载失败",
        detail: err.message,
        retry: () => loadDetail(expId),
      });
    }
  }

  function renderDetail(data, compare) {
    detailHost.innerHTML = "";
    const record = data.experiment || {};
    const events = data.events || [];
    const expId = record.exp_id;

    // ---- 基本信息卡
    const card = el("article", "card detail-card experiments__record");
    const head = el("div", "detail-card__head");
    head.append(el("h3", "", record.display_name || "研究实验"));
    const status = deriveStatus(events);
    head.append(badge(STATUS_LABEL[status] || status));
    const comparison = latestPayload(events, "comparison");
    if (comparison) head.append(improvedBadge(comparison.improved));
    else if (!record.parent_exp_id) head.append(improvedBadge(null));
    card.append(head);
    card.append(
      kvEl([
        [{
          factor: "基础因子",
          strategy: "策略",
          portfolio_policy: "组合政策",
        }[record.subject_type] || "实验主体", record.subject_display_name || record.subject_name],
        ["研究编号", expId],
        ["前次实验", record.parent_exp_id || "基线实验"],
        ["采用策略", record.profile_display_name || record.profile_name],
        ["训练窗口", windowNode(record.train_window)],
        ["验证窗口", windowNode(record.valid_window)],
        ["测试窗口", windowNode(record.test_window)],
        ["登记时间", fmtTime(record.created_at)],
        ["变更摘要", record.change_summary, { wide: true }],
        ["变更原因", record.change_reason, { wide: true }],
      ]),
    );

    detailHost.append(card);

    
    detailHost.append(chainCard(record));

    // ---- 前次实验对比卡
    detailHost.append(compareCard(compare?.comparison, record.parent_exp_id));

    // ---- 事件流时间线
    detailHost.append(eventsCard(events));
  }

  function deriveStatus(events) {
    if (events.some((e) => e.event_type === "abort")) return "aborted";
    if (events.some((e) => e.event_type === "conclusion")) return "concluded";
    if (events.some((e) => e.event_type === "result")) return "attached";
    return "registered";
  }

  function windowNode(value) {
    if (!value) return "未设置";
    const dates = String(value).split("..");
    if (dates.length !== 2) return value;
    const range = el("span", "experiments__date-range");
    range.append(el("span", "", dates[0]), el("span", "experiments__date-separator", "至"), el("span", "", dates[1]));
    return range;
  }

  function latestPayload(events, type) {
    for (let i = events.length - 1; i >= 0; i -= 1) {
      if (events[i].event_type === type) return events[i].payload || {};
    }
    return null;
  }

  function chainCard(record) {
    const card = el("article", "card detail-card");
    const head = el("div", "detail-card__head");
    head.append(el("h4", "", "实验关系"));
    card.append(head);

    const byId = new Map((chainItems || []).map((i) => [i.exp_id, i]));
    if (!byId.has(record.exp_id)) byId.set(record.exp_id, record);

    
    const chain = [];
    const seen = new Set();
    let node = byId.get(record.exp_id);
    while (node && !seen.has(node.exp_id)) {
      seen.add(node.exp_id);
      chain.unshift(node);
      node = node.parent_exp_id ? byId.get(node.parent_exp_id) : null;
    }
    const children = (chainItems || []).filter(
      (i) => i.parent_exp_id === record.exp_id && !seen.has(i.exp_id),
    );

    if (chain.length <= 1 && !children.length) {
      card.append(
        el("p", "item-row__meta", "暂无后续实验。生成新候选后会自动关联。"),
      );
      return card;
    }

    const wrap = el("div", "experiments__chain");
    chain.forEach((n, idx) => wrap.append(chainRow(n, idx, n.exp_id === record.exp_id, false)));
    for (const child of children) wrap.append(chainRow(child, chain.length, false, true));
    card.append(wrap);
    return card;
  }

  function chainRow(node, idx, isCurrent, isChild) {
    const row = el("div", "chain-row");
    if (isCurrent) row.classList.add("is-current");
    const step = isChild ? "后续实验" : idx === 0 ? "基线" : `第 ${idx} 轮`;
    row.append(el("span", "chain-row__step", step));
    const idBtn = el("button", "chain-row__id", node.display_name || node.exp_id);
    idBtn.type = "button";
    idBtn.addEventListener("click", () => {
      if (node.exp_id === currentId) return;
      currentId = node.exp_id;
      listHost.querySelectorAll(".item-row").forEach((n) => {
        n.classList.toggle("is-active", n.dataset.expId === currentId);
      });
      loadDetail(currentId);
    });
    row.append(idBtn, chainBadge(node));
    if (node.annualized_excess_return != null) {
      row.append(
        el(
          "span",
          signClass(node.annualized_excess_return),
          `超额 ${fmtPct(node.annualized_excess_return)}`,
        ),
      );
    }
    if (node.run_id) {
      const runBtn = el("button", "btn btn--ghost chain-row__run", "查看回测");
      runBtn.type = "button";
      runBtn.addEventListener("click", () => {
        ctx.bus.emit("backtest:open", node.run_id);
        ctx.bus.emit("route", "backtest");
      });
      row.append(runBtn);
    }
    return row;
  }

  function chainBadge(node) {
    if (node.improved === true) return badge("改善", "safe");
    if (node.improved === false) return badge("未改善", "danger");
    return node.parent_exp_id ? badge("未判定", "muted") : badge("基线", "accent");
  }

  function stateText(state, decision) {
    if (state === "queued") return "排队中";
    if (state === "running") return "运行中";
    if (state === "created") return "待启动";
    if (state === "failed") return "失败";
    if (state === "cancelled") return "已中止";
    if (state === "paused") return "已暂停（可恢复）";
    if (state === "completed") return decision === "frozen" ? "已完成（达标）" : "已完成（未达标）";
    return state || "—";
  }

  function stateBadgeArgs(status) {
    if (status.decision === "cancelled") return ["已中止", "muted"];
    const state = status.state || "";
    if (state === "queued" || state === "created") return [stateText(state), "warn"];
    if (state === "running") return ["运行中", "accent"];
    if (state === "failed") return ["失败", "danger"];
    if (state === "cancelled") return ["已中止", "muted"];
    if (state === "paused") return ["已暂停（可恢复）", "accent"];
    if (state === "completed") {
      return status.decision === "frozen" ? ["已完成（达标）", "safe"] : ["已完成（未达标）", "muted"];
    }
    return [stateText(state, status.decision)];
  }

  function runLink(runId) {
    if (!runId) return null;
    const btn = el("button", "experiments__run-link", runId);
    btn.type = "button";
    btn.addEventListener("click", () => {
      ctx.bus.emit("backtest:open", runId);
      ctx.bus.emit("route", "backtest");
    });
    return btn;
  }

  function compareCard(comparison, parentExpId) {
    const card = el("article", "card detail-card");
    const head = el("div", "detail-card__head");
    head.append(el("h4", "", "前次实验对比"));
    if (comparison) head.append(improvedBadge(comparison.improved));
    card.append(head);
    if (!parentExpId) {
      card.append(el("p", "item-row__meta", "基线实验，无前次实验可对比。"));
      return card;
    }
    if (!comparison) {
      card.append(el("p", "item-row__meta", "对比数据加载失败或本实验尚无回测结果。"));
      return card;
    }
    if (comparison.note) {
      card.append(el("p", "item-row__meta", comparison.note));
      return card;
    }
    const metricName = METRIC_LABEL[comparison.primary_metric] || comparison.primary_metric;
    card.append(
      kvEl([
        ["前次实验", comparison.parent_exp_id],
        ["前次回测", runLink(comparison.parent_run_id)],
        [`本次${metricName}`, pctNode(comparison.primary_current)],
        [`前次实验${metricName}`, pctNode(comparison.primary_parent)],
      ]),
    );
    const rows = Object.entries(comparison.deltas || {}).map(([key, value]) => ({ key, value }));
    if (rows.length) {
      card.append(
        tableEl(
          [
            { key: "key", label: "指标", format: (v) => METRIC_LABEL[v] || v },
            {
              key: "value",
              label: "与前次实验相比",
              num: true,
              format: (v, row) => {
                if (v == null) return "—";
                const text = PCT_METRICS.has(row.key) ? fmtPct(v) : fmtNum(v, 3);
                return el("span", signClass(v), v > 0 ? `+${text}` : text);
              },
            },
          ],
          rows,
        ),
      );
    }
    card.append(
      el("p", "item-row__meta", `本次${metricName}高于前次实验即视为改善，反之为未改善。`),
    );
    return card;
  }

  function eventsCard(events) {
    const card = el("article", "card detail-card experiments__operations");
    const head = el("div", "detail-card__head");
    head.append(el("h4", "", `操作记录（${events.length}）`));
    const filterWrap = el("div", "select experiments__event-filter");
    filterWrap.innerHTML = selectMarkup("全部事件");
    head.append(filterWrap);
    card.append(head);
    const timeline = el("div", "experiments__timeline");
    card.append(timeline);

    const eventTypes = [...new Set(events.map((e) => e.event_type))];
    const options = [{ value: "", label: "全部事件" }].concat(
      eventTypes.map((t) => ({ value: t, label: EVENT_LABEL[t] || t })),
    );
    const select = createSelect(filterWrap, { onChange: (value) => renderEvents(value) });
    select.setOptions(options, "");
    renderEvents("");

    function renderEvents(type) {
      timeline.innerHTML = "";
      const rows = events.filter((e) => !type || e.event_type === type);
      if (!rows.length) {
        timeline.append(el("p", "item-row__meta", "暂无该类型事件。"));
        return;
      }
      for (const event of [...rows].reverse()) timeline.append(eventEl(event));
    }
    return card;
  }

  function eventEl(event) {
    const item = el("div", "event");
    const head = el("div", "event__head");
    head.append(badge(EVENT_LABEL[event.event_type] || event.event_type));
    head.append(el("span", "event__time", fmtTime(event.created_at)));
    item.append(head);
    const payload = event.payload || {};
    switch (event.event_type) {
      case "result":
        item.append(resultBody(payload));
        break;
      case "comparison":
        item.append(comparisonBody(payload));
        break;
      case "ai_analysis":
        item.append(analysisBody(payload));
        break;
      case "note":
        item.append(el("p", "event__text", payload.text || "—"));
        if (payload.run_id) item.append(resultBody(payload));
        break;
      case "abort":
        item.append(el("p", "event__text", payload.reason || "—"));
        break;
      default:
        item.append(codeEl(JSON.stringify(payload, null, 2)));
    }
    return item;
  }

  function resultBody(payload) {
    const metrics = payload.metrics || {};
    const returns = metrics.returns || {};
    const risk = metrics.risk || {};
    const body = el("div", "event__result");
    if (payload.run_id) {
      const reference = el("div", "event__run-reference");
      reference.append(el("span", "item-row__meta", "回测运行"), runLink(payload.run_id));
      body.append(reference);
    }
    const pairs = [
      ["年化收益", pctNode(returns.annualized_return)],
      ["年化超额", pctNode(returns.annualized_excess_return)],
      ["夏普", risk.sharpe != null ? fmtNum(risk.sharpe, 3) : null],
      ["最大回撤", risk.max_drawdown != null ? fmtPct(risk.max_drawdown) : null],
    ].filter(([, value]) => value !== null && value !== undefined);
    if (pairs.length) {
      const metricsRow = el("div", "event__metrics");
      for (const [label, value] of pairs) {
        const metric = el("span", "event__metric");
        metric.append(el("span", "item-row__meta", label));
        metric.append(value instanceof Node ? value : el("span", "", value));
        metricsRow.append(metric);
      }
      body.append(metricsRow);
    }
    return body;
  }

  function pctNode(value) {
    if (value == null) return null;
    return el("span", signClass(value), fmtPct(value));
  }

  function comparisonBody(payload) {
    const wrap = el("div");
    const metricName = METRIC_LABEL[payload.primary_metric] || payload.primary_metric || "主指标";
    wrap.append(
      kvEl([
        ["前次实验", payload.parent_exp_id],
        [`本次${metricName}`, pctNode(payload.primary_current ?? payload.current)],
        [`前次实验${metricName}`, pctNode(payload.primary_parent)],
        ["判定", payload.improved == null ? "无法判定" : payload.improved ? "改善" : "未改善"],
      ]),
    );
    if (payload.note) wrap.append(el("p", "item-row__meta", payload.note));
    return wrap;
  }

  function analysisBody(payload) {
    const wrap = el("div");
    if (payload.assessment) {
      wrap.append(el("h5", "event__subtitle", "整体评价"));
      wrap.append(el("p", "event__text", payload.assessment));
    }
    if (payload.failure_reason) {
      wrap.append(el("h5", "event__subtitle", "失败原因"));
      wrap.append(el("p", "event__text", payload.failure_reason));
    }
    if (Array.isArray(payload.next_steps) && payload.next_steps.length) {
      wrap.append(el("h5", "event__subtitle", "下一步建议"));
      const list = el("ol", "event__list");
      for (const step of payload.next_steps) list.append(el("li", "", step));
      wrap.append(list);
    }
    if (Array.isArray(payload.open_questions) && payload.open_questions.length) {
      wrap.append(el("h5", "event__subtitle", "待确认问题"));
      const list = el("ul", "event__list");
      for (const q of payload.open_questions) list.append(el("li", "", q));
      wrap.append(list);
    }
    if (payload.model) wrap.append(el("p", "item-row__meta", payload.model));
    return wrap;
  }
}
