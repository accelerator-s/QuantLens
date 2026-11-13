import { createChat } from "../chat-stream/chat-stream.js";
import { createChatDialog } from "../chat-stream/chat-dialog.js";
import { renderState } from "../state-card/state-card.js";
import { hydrateIcons } from "../../core/icons.js";
import { createSelect } from "../../core/select.js";
import { toastError } from "../../core/toast.js";
import { captureLogScroll, updateTaskLog } from "../../core/task-log.js";
import { badge } from "../../core/fmt.js";
import { el, kvEl, selectMarkup } from "../../core/dom.js";

const TASK_STATUS_LABEL = {
  running: "进行中", completed: "生成完成", failed: "失败", interrupted: "已中断",
};

export async function mount(root, ctx) {
  const candidateTaskHost = root.querySelector("[data-candidate-task]");
  const resultHost = root.querySelector("[data-research-result]");
  const aiResearchBtn = root.querySelector("[data-ai-research]");
  const emptyHost = root.querySelector("[data-research-empty]");
  let pollTimer = null;
  let currentTaskId = "";
  let polling = false;
  await hydrateIcons(root);
  aiResearchBtn.addEventListener("click", () => openAIResearchDialog());
  ctx.bus.on("route", id => {
    if (id === "factors" && !polling) restoreTask();
  });
  await restoreTask();

  async function restoreTask() {
    aiResearchBtn.disabled = true;
    try {
      const data = await ctx.api.jobs();
      const task = (data.items || []).find(item => item.resource === "factor-ai-research");
      if (task) {
        pollTask(task.task_id, {
          host: candidateTaskHost,
          buttons: [aiResearchBtn],
          onResult: renderAIResearchResult,
          notify: false,
        });
      } else {
        aiResearchBtn.disabled = false;
      }
    } catch (err) {
      aiResearchBtn.disabled = false;
      await renderState(candidateTaskHost, {
        kind: "error", title: "研究任务加载失败", detail: err.message, retry: restoreTask,
      });
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
      title: "从一个现象开始研究",
      onStateChange: (state) => {
        start.disabled = state.busy || state.disabled || state.loading || !state.hasUserMessages;
        modelTrigger.disabled = state.busy || state.disabled || state.loading;
      },
      greeting:
        "描述你观察到的现象，一起讨论它的原因、作用时间和失效条件。",
      placeholder: "填写想验证的现象或假设…",
      send: async (history, options) => {
        const res = await ctx.api.factorResearchChat({
          model: modelSelect.value,
          messages: history,
        }, options);
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
    const summary = el("article", "card detail-card factors__research-result");
    const head = el("div", "detail-card__head");
    head.append(el("h3", "", result.display_name || "新因子"), badge("已进入自动迭代", "safe"));
    summary.append(head);
    summary.append(kvEl([["内部标识", result.factor_name], ["研究会话", result.research_id]]));
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
    resultHost.replaceChildren(summary);
  }

  function pollTask(taskId, { host, buttons = [], onResult, notify = true }) {
    if (pollTimer) clearTimeout(pollTimer);
    currentTaskId = taskId;
    polling = true;
    emptyHost.hidden = true;
    resultHost.replaceChildren();
    for (const b of buttons) b.disabled = true;

    async function tick() {
      try {
        const task = await ctx.api.task(taskId);
        if (currentTaskId !== taskId) return;
        renderTaskCard(host, task);
        if (task.status === "running") {
          pollTimer = setTimeout(tick, 2000);
          return;
        }
        polling = false;
        for (const b of buttons) b.disabled = false;
        if (task.status === "completed") {
          if (notify) ctx.bus.emit("toast", { kind: "success", title: "新因子已生成，已启动迭代实验" });
          if (onResult) onResult(task.result || {});
        } else {
          if (notify) ctx.bus.emit("toast", { kind: "error", title: "新因子生成失败", detail: task.error });
        }
      } catch (err) {
        if (currentTaskId !== taskId) return;
        polling = false;
        for (const b of buttons) b.disabled = false;
        toastError(ctx.bus, err, "任务状态查询失败");
        await renderState(host, {
          kind: "error", title: "任务状态查询失败", detail: err.message,
          retry: () => pollTask(taskId, { host, buttons, onResult, notify }),
        });
      }
    }
    tick();
  }

  function renderTaskCard(host, task) {
    const previousLog = host.querySelector("[data-task-log]");
    const restoreScroll = captureLogScroll(previousLog);
    const card = el("article", "card detail-card");
    const head = el("div", "detail-card__head");
    head.append(el("h3", "", "候选生成任务"));
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
          task.status === "completed" ? "候选已登记，迭代实验已启动"
            : `${progress.step}${task.status === "running" && Number.isFinite(pct) ? ` · ${Math.round(pct)}%` : ""}`,
        ),
      );
    }
    card.append(el("p", "item-row__meta", task.status === "completed"
      ? "候选已登记，后续评估与版本比较请到「迭代实验」查看。"
      : "本阶段生成并检查初始候选；通过后会自动启动迭代实验。"));
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
