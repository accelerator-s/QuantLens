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
  const researchHeading = root.querySelector("[data-research-heading]");
  const researchButtons = [aiResearchBtn, root.querySelector("[data-research-again]")];
  const setResearchDisabled = value => researchButtons.forEach(button => { button.disabled = value; });
  let pollTimer = null;
  let currentTaskId = "";
  let polling = false;
  await hydrateIcons(root);
  researchButtons.forEach(button => button.addEventListener("click", openAIResearchDialog));
  ctx.bus.on("route", id => {
    if (id === "strategy-research" && !polling) restoreTask();
  });
  root.closest('.view')?.querySelector('#strategy-research-tab-research')?.addEventListener('click', () => {
    if (!polling) restoreTask();
  });
  await restoreTask();

  async function restoreTask() {
    setResearchDisabled(true);
    try {
      const data = await ctx.api.jobs({resource: "strategy-ai-research", limit: 1});
      const task = (data.items || []).find(item => item.resource === "strategy-ai-research");
      if (task) {
        pollTask(task.task_id, {
          host: candidateTaskHost,
          buttons: researchButtons,
          onResult: renderAIResearchResult,
          notify: false,
        });
      } else {
        setResearchDisabled(false);
      }
    } catch (err) {
      setResearchDisabled(false);
      await renderState(candidateTaskHost, {
        kind: "error", title: "研究任务加载失败", detail: err.message, retry: restoreTask,
      });
    }
  }

  async function openAIResearchDialog() {
    let recommendationData, accountData;
    try {
      setResearchDisabled(true);
      [recommendationData, accountData] = await Promise.all([ctx.api.strategyResearchModels(), ctx.api.accounts()]);
    } catch (err) {
      toastError(ctx.bus, err, "模型匹配加载失败");
      return;
    } finally {
      setResearchDisabled(false);
    }
    const modelItems = (recommendationData.items || []).filter((item) => item.selectable);
    if (!modelItems.length) {
      ctx.bus.emit("toast", {
        kind: "error",
        title: "没有可执行策略研究的模型",
        detail: "请在“模型连接”配置密钥和模型能力。",
      });
      return;
    }
    const accountItems = (accountData.items || []).filter(a => a.market === 'CN-A' && a.instrument_class === 'stock');
    if (!accountItems.length) {
      ctx.bus.emit('toast', {kind: 'info', title: '请先在账户持仓中创建账户'});
      return;
    }
    let chat, modelSelect, accountSelect;
    const { toolbar, chatHost, historyHost, actions, close } = createChatDialog({
      title: "研究新策略",
      history: true,
      onClose: () => { chat?.destroy(); modelSelect?.destroy(); accountSelect?.destroy(); },
    });
    const modelField = el("div", "chat-dialog__model");
    const modelLabel = el("span", "", "研究模型");
    modelLabel.id = "strategy-research-model-label";
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
    const accountField = el('div', 'chat-dialog__model');
    const accountRoot = el('div', 'select');
    accountRoot.innerHTML = selectMarkup('请选择关联账户');
    const accountOptions = [{value: '', label: '请选择关联账户'}, ...accountItems.map(a => ({value: a.account_id, label: a.alias || a.account_id}))];
    let chatState = {};
    accountSelect = createSelect(accountRoot, {onChange: () => {
      start.disabled = !accountSelect.value || chatState.busy || chatState.disabled || chatState.loading || !chatState.hasUserMessages;
    }});
    accountSelect.setOptions(accountOptions, '');
    accountField.append(el('span', '', '关联账户'), accountRoot);
    toolbar.prepend(accountField);
    const start = el("button", "btn btn--primary", "生成策略方案");
    start.type = "button";
    actions.append(start);
    start.disabled = true;
    chat = createChat(chatHost, {
      historyHost,
      historyKey: "strategy-research",
      getMetadata: () => ({ model: modelSelect.value, account_id: accountSelect.value }),
      onRestore: metadata => {
        modelSelect.setOptions(modelOptions, metadata.model || recommendationData.recommended);
        accountSelect.setOptions(accountOptions, accountItems.some(a => a.account_id === metadata.account_id) ? metadata.account_id : '');
      },
      title: "从一个策略想法开始研究",
      onStateChange: (state) => {
        chatState = state;
        start.disabled = !accountSelect.value || state.busy || state.disabled || state.loading || !state.hasUserMessages;
        modelTrigger.disabled = state.busy || state.disabled || state.loading;
        accountRoot.querySelector('[data-select-trigger]').disabled = state.busy || state.disabled || state.loading;
      },
      greeting:
        "先选择用于生成策略的账户，再聊你希望选择哪类公司。资金和交易权限取自账户；策略创建后，关联账户不可更换。",
      placeholder: "填写策略研究想法…",
      send: async (history, options) => {
        const res = await ctx.api.strategyResearchChat({
          model: modelSelect.value,
          account_id: accountSelect.value,
          messages: history,
        }, options);
        return { text: res.reply, reasoning: res.reasoning || "", model: res.model || modelSelect.value };
      },
    });

    start.addEventListener("click", async () => {
      if (chat.busy) return;
      if (!accountSelect.value) return;
      if (!chat.history.some((item) => item.role === "user")) {
        ctx.bus.emit("toast", { kind: "info", title: "请先填写研究想法" });
        return;
      }
      start.disabled = true;
      start.textContent = "正在启动…";
      chat.setDisabled(true);
      modelTrigger.disabled = true;
      try {
        const result = await ctx.api.strategyResearchStart({
          model: modelSelect.value,
          account_id: accountSelect.value,
          messages: chat.context,
        });
        close();
        ctx.bus.emit("toast", { kind: "info", title: "生成策略方案任务已启动" });
        pollTask(result.task_id, {
          host: candidateTaskHost,
          buttons: researchButtons,
          onResult: renderAIResearchResult,
        });
      } catch (err) {
        start.textContent = "生成策略方案";
        chat.setDisabled(false);
        modelTrigger.disabled = false;
        toastError(ctx.bus, err, "生成策略方案失败");
      }
    });
  }

  function renderAIResearchResult(result, taskId) {
    const pending = result.approval_status === "pending";
    const summary = el("article", "card detail-card factors__research-result");
    const head = el("div", "detail-card__head");
    head.append(el("h3", "", result.display_name || "新策略"), badge(pending ? "等待启动实验" : "已进入策略实验", pending ? "accent" : "safe"));
    summary.append(head);
    summary.append(kvEl([["内部标识", result.profile_name], ...(result.experiment_id ? [["研究会话", result.experiment_id]] : [])]));
    summary.append(el("p", "", result.hypothesis || "策略假设已登记。"));
    if (result.account_snapshot) summary.append(kvEl([
      ['关联账户（不可更换）', result.account_snapshot.alias],
      ['创建时可投资资金', `${Number(result.account_snapshot.capital).toLocaleString()} 元`],
    ]));
    summary.append(
      el(
        "p",
        "item-row__meta",
        pending ? "策略方案已通过检查。" : "本地量化模型将完成配权、选股、回测和报告。",
      ),
    );
    if (pending) {
      const start = el("button", "btn btn--primary", "启动实验");
      start.type = "button";
      start.addEventListener("click", async () => {
        start.disabled = true;
        start.textContent = "正在启动…";
        setResearchDisabled(true);
        try {
          await ctx.api.strategyResearchApprove(taskId);
          if (currentTaskId !== taskId) return;
          ctx.bus.emit("toast", { kind: "success", title: "策略实验已启动" });
          pollTask(taskId, { host: candidateTaskHost, buttons: researchButtons,
            onResult: renderAIResearchResult, notify: false });
        } catch (err) {
          start.disabled = false;
          start.textContent = "启动实验";
          setResearchDisabled(false);
          toastError(ctx.bus, err, "策略实验启动失败");
        }
      });
      summary.append(start);
      resultHost.replaceChildren(summary);
      return;
    }
    const open = el("button", "btn btn--ghost", "查看自动研究进展");
    open.type = "button";
    open.addEventListener("click", () => {
      ctx.strategyExperimentId = result.experiment_id;
      ctx.strategyExperimentFilter = result.profile_name;
      ctx.strategyExperimentFilterLabel = result.display_name;
      ctx.bus.emit("strategy:experiment", result.experiment_id);
      ctx.bus.emit("route", "strategy-research/experiments");
    });
    summary.append(open);
    resultHost.replaceChildren(summary);
  }

  function pollTask(taskId, { host, buttons = [], onResult, notify = true }) {
    if (pollTimer) clearTimeout(pollTimer);
    currentTaskId = taskId;
    polling = true;
    emptyHost.hidden = true;
    researchHeading.hidden = false;
    resultHost.replaceChildren();
    for (const b of buttons) b.disabled = true;

    async function tick() {
      try {
        const task = await ctx.api.strategyResearchProgress(taskId);
        if (currentTaskId !== taskId) return;
        renderTaskCard(host, task);
        if (task.status === "running") {
          pollTimer = setTimeout(tick, 2000);
          return;
        }
        polling = false;
        for (const b of buttons) b.disabled = false;
        if (task.status === "completed") {
          if (notify) ctx.bus.emit("toast", { kind: "success", title: "新策略已生成" });
          if (onResult) onResult(task.result || {}, taskId);
        } else {
          if (notify) ctx.bus.emit("toast", { kind: "error", title: "新策略生成失败", detail: task.error });
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
    head.append(el("h3", "", "方案生成任务"));
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
          task.status === "completed" ? (task.result?.approval_status === "pending" ? "策略方案已就绪，等待启动实验" : "策略方案已登记，策略实验已启动")
            : `${progress.step}${task.status === "running" && Number.isFinite(pct) ? ` · ${Math.round(pct)}%` : ""}`,
        ),
      );
    }

    if (task.error) card.append(el("p", "factors__task-error", task.error));
    const taskLog = updateTaskLog(previousLog, task.log, {
      subject: task.task_id || task.name,
    });
    if (taskLog) card.append(taskLog);
    host.replaceChildren(card);
    restoreScroll();
  }
}
