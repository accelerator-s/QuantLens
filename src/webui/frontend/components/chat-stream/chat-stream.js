import { createChatHistory } from "./chat-history.js";
import { renderAssistant, splitThinking, createAssistantStream } from "./chat-markdown.js";

function node(tag, className, text = "") {
  const result = document.createElement(tag);
  result.className = className;
  result.textContent = text;
  return result;
}

export function createChat(host, {
  send, greeting = "", title = "研究想法", placeholder = "输入内容…",
  suggestions = [], onBusyChange, onStateChange, historyHost, historyKey, getMetadata = () => ({}), onRestore,
} = {}) {
  const history = [];
  let busy = false, disabled = false, disposed = false, savedHistory;
  let historyLoading = Boolean(historyHost && historyKey), saveTimer, activeStream;
  host.replaceChildren();
  host.classList.add("chat");

  const list = node("div", "chat__list");
  list.setAttribute("role", "log");
  list.setAttribute("aria-label", "对话记录");
  list.setAttribute("aria-live", "polite");
  list.setAttribute("aria-relevant", "additions text");
  list.tabIndex = 0;
  const welcome = node("div", "chat__welcome");
  const mark = node("span", "chat__welcome-mark", "✦");
  mark.setAttribute("aria-hidden", "true");
  welcome.append(mark, node("h3", "chat__welcome-title", title), node("p", "chat__welcome-desc", greeting));
  const prompts = node("div", "chat__suggestions");
  for (const text of suggestions) {
    const suggestion = node("button", "chat__suggestion", text);
    suggestion.type = "button";
    suggestion.addEventListener("click", () => {
      input.value = text;
      resizeInput();
      input.focus();
      sync();
      savedHistory?.save();
    });
    prompts.append(suggestion);
  }
  if (suggestions.length) welcome.append(prompts);
  list.append(welcome);

  const form = node("form", "chat__form");
  const composer = node("div", "chat__composer");
  const input = node("textarea", "chat__input");
  input.rows = 2;
  input.placeholder = placeholder;
  input.setAttribute("aria-label", placeholder);
  const tools = node("div", "chat__tools");
  const status = node("span", "chat__hint", "Enter 发送 · Shift + Enter 换行");
  status.setAttribute("role", "status");
  const btn = node("button", "btn btn--primary chat__send");
  btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5m-6 6 6-6 6 6"/></svg>';
  btn.type = "submit";
  btn.title = "发送消息";
  btn.setAttribute("aria-label", "发送消息");
  tools.append(status, btn);
  composer.append(input, tools);
  form.append(composer);
  host.append(list, form);

  function sync() {
    btn.disabled = busy || disabled || historyLoading || !input.value.trim();
    input.disabled = disabled || historyLoading;
    for (const button of list.querySelectorAll("[data-chat-action]")) button.disabled = busy || disabled || historyLoading;
    for (const field of list.querySelectorAll(".chat__edit-input")) field.disabled = busy || disabled || historyLoading;
    for (const button of prompts.children) button.disabled = busy || disabled;
    savedHistory?.setLocked(busy || disabled || historyLoading);
    host.setAttribute("aria-busy", String(busy));
    status.textContent = busy ? "正在回复…" : "Enter 发送 · Shift + Enter 换行";
    onStateChange?.({ busy, disabled, loading: historyLoading,
      hasUserMessages: history.some(message => message.role === "user" && message.content.trim()),
    });
  }
  function resizeInput() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  }
  function context() {
    return history.map(message => ({ role: message.role,
      content: message.role === "assistant" ? splitThinking(message.content).answer : message.content,
    })).filter(message => message.content.trim());
  }
  function scrollToLatest() { list.scrollTop = list.scrollHeight; }
  function appendMessage(role, text, model = "", index = history.length, reasoning = "", pending = false) {
    const item = node("article", `chat__item chat__item--${role}`);
    item.dataset.messageIndex = String(index);
    if (role === "assistant") {
      const avatar = node("span", "chat__avatar", "✦");
      avatar.setAttribute("aria-hidden", "true");
      item.append(avatar);
    }
    const content = node("div", "chat__content");
    content.append(node("div", "chat__role", role === "user" ? "你" : model || "研究助手"));
    const bubble = node("div", "chat__bubble");
    if (role === "assistant" && !pending) renderAssistant(bubble, text, reasoning);
    else bubble.textContent = text;
    content.append(bubble);
    if (!pending) appendActions(role, item, index, content);
    item.append(content);
    list.append(item);
    scrollToLatest();
    return item;
  }
  function appendActions(role, item, index, content) {
    const actions = node("div", "chat__message-actions");
    const action = node("button", "btn btn--ghost chat__message-action");
    action.type = "button";
    action.dataset.chatAction = role === "user" ? "edit" : "regenerate";
    action.title = role === "user" ? "编辑并重新发送" : "重新生成";
    action.setAttribute("aria-label", action.title);
    action.innerHTML = role === "user"
      ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m16 3 5 5L8 21H3v-5Z"/><path d="m14 5 5 5"/></svg>'
      : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 7a7 7 0 0 1 12-1l2 6M4 12l2 6a7 7 0 0 0 12-1"/></svg>';
    action.addEventListener("click", () => {
      if (busy || disabled || historyLoading || disposed) return;
      if (role === "user") editMessage(item, index);
      else {
        const userIndex = history.findLastIndex((message, position) => position < index && message.role === "user");
        if (userIndex < 0) return;
        truncate(userIndex + 1);
        savedHistory?.save();
        submit({ retry: true });
      }
    });
    actions.append(action);
    content.append(actions);
  }

  async function submit({ retry = false, editIndex, text: editedText } = {}) {
    if (busy || disabled || historyLoading || disposed) return;
    const text = (editedText ?? input.value).trim();
    if (!retry && !text) return;
    list.querySelector(".chat__edit-cancel")?.click();
    clearTimeout(saveTimer);
    list.querySelector(".chat__error")?.remove();
    welcome.remove();
    if (!retry) {
      if (editIndex !== undefined) truncate(editIndex);
      else input.value = "";
      resizeInput();
      appendMessage("user", text);
      history.push({ role: "user", content: text });
      savedHistory?.save();
    }
    busy = true;
    sync();
    onBusyChange?.(true);
    const pending = appendMessage("assistant", "正在思考…", "", history.length, "", true);
    pending.classList.add("chat__item--pending");
    const bubble = pending.querySelector(".chat__bubble");
    let received = false;
    const live = createAssistantStream(bubble, paint => {
      if (disposed) return;
      // Respect a user reading earlier messages; no forced jumps on each token.
      const follow = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
      paint();
      if (follow) scrollToLatest();
    });
    activeStream = live;
    try {
      const result = await send(context(), { onDelta: delta => {
        if (disposed) return;
        if (!received) { bubble.replaceChildren(); pending.classList.remove("chat__item--pending"); received = true; }
        live.push(delta);
      } });
      const reply = typeof result === "string" ? result : result?.text || "";
      const reasoning = typeof result === "object" ? result?.reasoning || "" : "";
      if (!reply.trim() && !reasoning.trim()) throw new Error("模型未返回内容，请重试。");
      const expectedMessages = disposed ? history.map(message => ({ ...message })) : undefined;
      history.push({ role: "assistant", content: reply, ...(reasoning ? { reasoning } : {}) });
      savedHistory?.save({ expectedMessages });
      if (history.filter(message => message.role === "assistant").length === 1) savedHistory?.suggestTitle();
      if (disposed) return;
      if (!received) bubble.replaceChildren();
      live.finish(reply, reasoning);
      pending.classList.remove("chat__item--pending");
      pending.dataset.messageIndex = String(history.length - 1);
      pending.querySelector(".chat__role").textContent = result?.model || "研究助手";
      appendActions("assistant", pending, history.length - 1, pending.querySelector(".chat__content"));
    } catch (err) {
      if (disposed) return;
      pending.remove();
      const error = node("div", "chat__error");
      error.setAttribute("role", "alert");
      error.append(node("span", "", `回复失败：${err.message}`));
      const retryButton = node("button", "btn btn--ghost btn--sm", "重试");
      retryButton.type = "button";
      retryButton.addEventListener("click", () => submit({ retry: true }));
      error.append(retryButton);
      list.append(error);
      scrollToLatest();
    } finally {
      live.destroy();
      if (activeStream === live) activeStream = undefined;
      busy = false;
      if (!disposed) {
        sync();
        onBusyChange?.(false);
      }
    }
  }
  function truncate(index) {
    clearTimeout(saveTimer);
    history.splice(index);
    for (const item of list.querySelectorAll(".chat__item")) {
      if (Number(item.dataset.messageIndex) >= index) item.remove();
    }
    list.querySelector(".chat__error")?.remove();
  }
  function editMessage(item, index) {
    list.querySelector(".chat__edit-cancel")?.click();
    const content = item.querySelector(".chat__content");
    const editor = node("form", "chat__edit-form");
    const field = node("textarea", "chat__edit-input");
    field.value = history[index].content;
    field.rows = Math.min(8, Math.max(3, field.value.split("\n").length));
    field.setAttribute("aria-label", "修改这条消息");
    const controls = node("div", "chat__edit-actions");
    const cancel = node("button", "btn btn--ghost btn--sm chat__edit-cancel", "取消");
    cancel.type = "button";
    cancel.dataset.chatAction = "cancel-edit";
    const resend = node("button", "btn btn--primary btn--sm", "发送");
    resend.type = "submit";
    resend.dataset.chatAction = "send-edit";
    controls.append(cancel, resend);
    editor.append(field, controls);
    content.hidden = true;
    item.append(editor);
    function dismiss() { editor.remove(); content.hidden = false; }
    cancel.addEventListener("click", dismiss);
    editor.addEventListener("submit", event => {
      event.preventDefault();
      if (busy || disabled || historyLoading || disposed || !field.value.trim()) return;
      const text = field.value;
      dismiss();
      submit({ editIndex: index, text });
    });
    field.addEventListener("keydown", event => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); dismiss(); }
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
        event.preventDefault(); editor.requestSubmit();
      }
    });
    field.focus({ preventScroll: true });
    field.setSelectionRange(field.value.length, field.value.length);
  }
  form.addEventListener("submit", event => { event.preventDefault(); submit(); });
  input.addEventListener("input", () => {
    resizeInput(); sync();
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => savedHistory?.save(), 300);
  });
  input.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
      event.preventDefault();
      submit();
    }
  });
  if (historyHost && historyKey) {
    savedHistory = createChatHistory(historyHost, historyKey, {
      snapshot: () => ({ messages: history.map(message => ({ ...message })), draft: input.value, metadata: getMetadata() }),
      onReady: () => {
        historyLoading = false;
        if (!disposed) { sync(); onBusyChange?.(busy || disabled); }
      },
      onPendingChange: value => {
        historyLoading = value;
        if (!disposed) { sync(); onBusyChange?.(busy || disabled || value); }
      },
      restore: session => {
        if (disposed) return;
        clearTimeout(saveTimer);
        history.splice(0, history.length, ...session.messages.map(message => ({ ...message })));
        list.replaceChildren();
        if (!history.length) list.append(welcome);
        history.forEach((message, index) => appendMessage(message.role, message.content, session.metadata?.model || "", index, message.reasoning || ""));
        input.value = session.draft || "";
        onRestore?.(session.metadata || {});
        const dialog = host.closest(".chat-dialog");
        dialog?.classList.remove("is-history-open");
        dialog?.querySelector(".chat-dialog__history-toggle")?.setAttribute("aria-expanded", "false");
        resizeInput();
        sync();
        input.focus({ preventScroll: true });
      },
    });
  }
  sync();
  return {
    history,
    get context() { return context(); },
    get busy() { return busy || historyLoading; },
    get ready() { return savedHistory?.ready || Promise.resolve(); },
    setDisabled(value) { disabled = value; sync(); },
    focus() { input.focus({ preventScroll: true }); },
    destroy() { clearTimeout(saveTimer); activeStream?.destroy(); savedHistory?.save(); disposed = true; },
    clear() {
      if (busy) return;
      history.length = 0;
      list.replaceChildren(welcome);
      input.value = "";
      resizeInput();
      savedHistory?.save();
      sync();
    },
  };
}
