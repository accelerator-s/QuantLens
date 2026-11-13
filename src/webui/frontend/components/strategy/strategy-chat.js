import { createChat } from "../chat-stream/chat-stream.js";
import { createChatDialog } from "../chat-stream/chat-dialog.js";
import { toastError } from "../../core/toast.js";
import { el, selectMarkup } from "../../core/dom.js";
import { createSelect } from "../../core/select.js";


export async function openStrategyChat(ctx, { onYaml, profileName = "" }) {
  let models = [];
  const [modelResult] = await Promise.allSettled([ctx.api.aiModels()]);
  try {
    const data = modelResult.status === 'fulfilled' ? modelResult.value : {};
    models = (data.items || [])
      .filter((m) => m.enabled && m.kind === "api_model")
      .map((m) => m.name);
  } catch {
    models = [];
  }

  let chat, modelSelect;
  const { toolbar, chatHost, historyHost, actions, close } = createChatDialog({
    title: profileName ? `编辑策略配置：${profileName}` : "新建策略配置",
    history: true,
    onClose: () => { chat?.destroy(); modelSelect?.destroy(); },
  });
  const modelField = el("div", "chat-dialog__model");
  const selectHost = el("div", "select");
  selectHost.innerHTML = selectMarkup("讨论模型");
  const modelLabel = el("span", "", "讨论模型");
  modelLabel.id = "strategy-chat-model-label";
  const modelTrigger = selectHost.querySelector("[data-select-trigger]");
  modelTrigger.setAttribute("aria-labelledby", modelLabel.id);
  modelField.append(modelLabel, selectHost);
  toolbar.append(modelField);

  modelSelect = createSelect(selectHost, {});
  const modelOptions = [{ value: "", label: "默认对话模型" }, ...models.map(m => ({ value: m, label: m }))];
  modelSelect.setOptions(modelOptions, "");

  const finalizeBtn = el("button", "btn btn--primary", "生成草案");
  finalizeBtn.type = "button";
  actions.append(finalizeBtn);
  finalizeBtn.disabled = true;
  chat = createChat(chatHost, {
    historyHost,
    historyKey: `strategy:${profileName || "new"}`,
    getMetadata: () => ({ model: modelSelect.value }),
    onRestore: metadata => modelSelect.setOptions(modelOptions, metadata.model || ""),
    title: profileName ? "这次想调整什么？" : "一起构建你的策略",
    onStateChange: (state) => {
      finalizeBtn.disabled = state.busy || state.disabled || state.loading || !state.hasUserMessages;
      modelTrigger.disabled = state.busy || state.disabled || state.loading;
    },
    greeting: profileName
      ? "说说要改哪些地方，其余设置保持不变。"
      : "想用哪些因子、持有多久、怎么控制风险？",
    send: async (history, options) => {
      const res = await ctx.api.strategyChat({
        model: modelSelect.value || "",
        profile_name: profileName,
        messages: history,
      }, options);
      return { text: res.reply, reasoning: res.reasoning || "", model: res.model || "" };
    },
  });


  finalizeBtn.addEventListener("click", async () => {
    if (chat.busy) return;
    if (!chat.history.length) {
      ctx.bus.emit("toast", { kind: "info", title: "请先填写并讨论策略想法。" });
      return;
    }
    finalizeBtn.disabled = true;
    finalizeBtn.textContent = "生成中…";
    chat.setDisabled(true);
    modelTrigger.disabled = true;
    try {
      const res = await ctx.api.strategyChatFinalize({
        model: modelSelect.value || "",
        profile_name: profileName,
        messages: chat.context,
      });
      close();
      if ((res.open_questions || []).length) {
        ctx.bus.emit("toast", {
          kind: "info",
          title: "草案含待确认问题",
          detail: res.open_questions.join("；"),
        });
      }
      onYaml(res.yaml_text, res.profile_name);
    } catch (err) {
      finalizeBtn.textContent = "生成草案";
      chat.setDisabled(false);
      modelTrigger.disabled = false;
      toastError(ctx.bus, err, "草案生成失败");
    }
  });

}
