import { createChat } from "../chat-stream/chat-stream.js";
import { toastError } from "../../core/toast.js";
import { el, selectMarkup } from "../../core/dom.js";
import { createSelect } from "../../core/select.js";


export async function openStrategyChat(ctx, { onYaml, profileName = "" }) {
  let models = [];
  try {
    const data = await ctx.api.aiModels();
    models = (data.items || [])
      .filter((m) => m.enabled && m.kind === "api_model")
      .map((m) => m.name);
  } catch {
    models = [];
  }

  const backdrop = el("div", "dialog-backdrop");
  const dialog = el("div", "dialog dialog--wide");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  backdrop.append(dialog);
  dialog.append(
    el("div", "dialog__title", profileName ? `编辑策略配置：${profileName}` : "新建策略配置"),
  );

  const body = el("div", "dialog__body");
  const toolbar = el("div", "toolbar");
  const selectHost = el("div", "select");
  selectHost.innerHTML = selectMarkup("讨论模型");
  toolbar.append(el("span", "item-row__meta", "讨论模型"), selectHost);
  body.append(toolbar);

  const modelSelect = createSelect(selectHost, {});
  modelSelect.setOptions(
    [{ value: "", label: "默认对话模型" }, ...models.map((m) => ({ value: m, label: m }))],
    "",
  );

  const chatHost = el("div");
  body.append(chatHost);
  dialog.append(body);

  const chat = createChat(chatHost, {
    greeting: profileName
      ? "说明要修改的配置，讨论后点击“生成草案”。未提及的设置保持不变。"
      : "说明想研究的 A 股策略，例如选股风格和持有时间。讨论后可生成配置草案。",
    send: async (history) => {
      const res = await ctx.api.strategyChat({
        model: modelSelect.value || "",
        profile_name: profileName,
        messages: history,
      });
      return { text: res.reply, model: res.model || "" };
    },
  });

  const actions = el("div", "dialog__actions");
  const closeBtn = el("button", "btn btn--ghost", "关闭");
  closeBtn.type = "button";
  const finalizeBtn = el("button", "btn btn--primary", "生成草案");
  finalizeBtn.type = "button";
  actions.append(closeBtn, finalizeBtn);
  dialog.append(actions);

  function close() {
    backdrop.remove();
  }
  closeBtn.addEventListener("click", close);
  backdrop.addEventListener("click", (ev) => {
    if (ev.target === backdrop) close();
  });

  finalizeBtn.addEventListener("click", async () => {
    if (!chat.history.length) {
      ctx.bus.emit("toast", { kind: "info", title: "请先填写并讨论策略想法。" });
      return;
    }
    finalizeBtn.disabled = true;
    finalizeBtn.textContent = "生成中…";
    try {
      const res = await ctx.api.strategyChatFinalize({
        model: modelSelect.value || "",
        profile_name: profileName,
        messages: chat.history,
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
      finalizeBtn.disabled = false;
      finalizeBtn.textContent = "生成草案";
      toastError(ctx.bus, err, "草案生成失败");
    }
  });

  document.body.append(backdrop);
}
