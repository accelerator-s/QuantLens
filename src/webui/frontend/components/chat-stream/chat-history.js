import { el } from "../../core/dom.js";
import { api } from "../../core/api.js";
import { splitThinking } from "./chat-markdown.js";

// Serialize file operations, including when a window is closed and reopened.
let operations = Promise.resolve();
const documents = new Map();
function enqueue(operation) {
  const result = operations.then(operation);
  operations = result.catch(() => {});
  return result;
}

export function createChatHistory(host, scope, { restore, snapshot, onReady, onPendingChange }) {
  let sessions = [], currentId = null, locked = false, loaded = false, renamingId = null;
  const titleRequests = new Map();
  const heading = el("div", "chat-history__heading", "对话历史");
  const newButton = el("button", "btn btn--ghost chat-history__new", "+ 新对话");
  newButton.type = "button";
  const list = el("div", "chat-history__list");
  list.setAttribute("aria-label", "历史对话");
  const notice = el("p", "chat-history__notice");
  notice.setAttribute("role", "alert");
  notice.hidden = true;
  const retry = el("button", "btn btn--ghost btn--sm", "重新读取");
  retry.type = "button";
  retry.hidden = true;
  retry.addEventListener("click", load);
  const retryTitle = el("button", "btn btn--ghost btn--sm", "重试生成标题");
  retryTitle.type = "button";
  retryTitle.hidden = true;
  host.replaceChildren(heading, newButton, list, notice, retry, retryTitle);

  function showError(error, action) {
    notice.hidden = false;
    notice.textContent = `${action}失败：${error.message}`;
  }
  function save({ expectedMessages, create = false } = {}) {
    if (!loaded) return Promise.resolve();
    const state = snapshot();
    if (!create && !state.messages.length && !state.draft.trim() && !currentId) return Promise.resolve();
    currentId ||= crypto.randomUUID();
    const id = currentId;
    const prior = sessions.find(session => session.id === id);
    const item = { ...state, id, scope, title: prior?.title || "", title_manual: prior?.title_manual || false, updatedAt: Date.now() };
    sessions = [item, ...sessions.filter(session => session.id !== id)];
    render();
    return enqueue(async () => {
      const previous = documents.get(id);
      // A late reply must never recreate a deleted file or overwrite newer turns.
      if (previous === null) return;
      if (expectedMessages && previous) {
        if (JSON.stringify(previous.messages) !== JSON.stringify(expectedMessages)) return;
        state.draft = previous.draft;
      }
      const result = await api.chatHistorySave(id, {
        ...state, scope, revision: previous?.revision || 0,
        title: previous?.title || "", title_manual: previous?.title_manual || false,
      });
      documents.set(id, result);
      const current = sessions.find(session => session.id === id);
      if (current && !current.title_manual) {
        current.title = result.title;
        current.title_manual = result.title_manual;
        render();
      }
      notice.hidden = true;
    }).catch(error => showError(error, "保存对话"));
  }
  function payload(document) {
    const { scope, messages, draft, metadata, revision, title = "", title_manual = false } = document;
    return { scope, messages, draft, metadata, revision, title, title_manual };
  }
  function suggestTitle(id = currentId) {
    const initial = sessions.find(session => session.id === id);
    const question = initial?.messages.find(message => message.role === "user")?.content;
    if (!id || !question || initial.title || initial.title_manual || titleRequests.get(id) === question) return;
    titleRequests.set(id, question);
    retryTitle.hidden = true;
    render();
    // Only file writes are serialized; the title model must not block chatting.
    return operations.then(async () => {
      const document = documents.get(id);
      if (!document || document.title || document.title_manual) return;
      const messages = document.messages.slice(0, 2).map(message => ({ role: message.role,
        content: message.role === "assistant" ? splitThinking(message.content).answer : message.content,
      }));
      const suggestion = await api.chatHistoryTitle({ model: document.metadata?.model || "", messages });
      await enqueue(async () => {
        const latest = documents.get(id);
        const local = sessions.find(session => session.id === id);
        if (!latest || latest.title || latest.title_manual || local?.title_manual) return;
        if (latest.messages.find(message => message.role === "user")?.content !== question) return;
        const result = await api.chatHistorySave(id, { ...payload(latest), title: suggestion.title });
        documents.set(id, result);
        if (local) local.title = result.title;
        notice.hidden = true;
        render();
      });
    }).catch(error => {
      if (documents.get(id)) {
        showError(error, "生成对话标题");
        retryTitle.hidden = false;
        retryTitle.onclick = () => suggestTitle(id);
      }
    }).finally(() => {
      if (titleRequests.get(id) === question) titleRequests.delete(id);
      render();
    });
  }
  function rename(item, row) {
    if (locked || renamingId) return;
    renamingId = item.id;
    const form = el("form", "chat-history__rename-form");
    const input = el("input", "chat-history__rename-input");
    input.value = item.title || item.messages.find(message => message.role === "user")?.content || "新对话";
    input.maxLength = 80;
    input.setAttribute("aria-label", "对话标题");
    const controls = el("div", "chat-history__rename-actions");
    const cancel = el("button", "btn btn--ghost btn--sm", "取消");
    cancel.type = "button";
    const confirm = el("button", "btn btn--primary btn--sm", "保存");
    confirm.type = "submit";
    controls.append(cancel, confirm);
    form.append(input, controls);
    row.replaceChildren(form);
    function dismiss() { renamingId = null; render(); }
    cancel.addEventListener("click", dismiss);
    input.addEventListener("keydown", event => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); dismiss(); }
      if (event.key === "Enter" && event.isComposing) event.preventDefault();
    });
    form.addEventListener("submit", event => {
      event.preventDefault();
      const title = input.value.trim();
      if (!title || locked) return;
      const local = sessions.find(session => session.id === item.id);
      if (local) { local.title = title; local.title_manual = true; }
      dismiss();
      enqueue(async () => {
        const latest = documents.get(item.id);
        if (!latest) return;
        const result = await api.chatHistorySave(item.id, { ...payload(latest), title, title_manual: true });
        documents.set(item.id, result);
        notice.hidden = true;
        retryTitle.hidden = true;
      }).catch(error => showError(error, "修改对话标题"));
    });
    input.focus();
    input.select();
  }
  function activate(item, { create = false } = {}) {
    currentId = item?.id || null;
    restore(item || { messages: [], draft: "" });
    render();
    if (create) return save({ create: true });
    if (item?.messages.some(message => message.role === "assistant")) suggestTitle();
  }
  async function remove(item) {
    if (locked || !loaded) return;
    locked = true;
    onPendingChange?.(true);
    render();
    try {
      await enqueue(async () => {
        await api.chatHistoryDelete(item.id);
        documents.set(item.id, null);
      });
      sessions = sessions.filter(session => session.id !== item.id);
      notice.hidden = true;
      retryTitle.hidden = true;
      if (currentId === item.id) activate(sessions[0]);
    } catch (error) {
      showError(error, "删除对话");
    } finally {
      locked = false;
      onPendingChange?.(false);
      render();
    }
  }
  function render() {
    if (renamingId) return;
    list.replaceChildren();
    newButton.disabled = locked || !loaded;
    if (!sessions.length) list.append(el("p", "chat-history__empty", loaded ? "还没有历史对话" : "正在读取对话…"));
    for (const item of sessions) {
      const title = item.title || item.messages.find(message => message.role === "user")?.content || item.draft || "新对话";
      const row = el("div", `chat-history__row${item.id === currentId ? " is-active" : ""}`);
      const open = el("button", "chat-history__open");
      open.type = "button";
      open.title = title;
      open.disabled = locked || !loaded;
      if (item.id === currentId) open.setAttribute("aria-current", "true");
      open.append(el("span", "chat-history__title", title), el("span", "chat-history__date",
        new Date(item.updatedAt).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })
        + (titleRequests.has(item.id) ? " · 正在命名…" : "")));
      open.addEventListener("click", () => {
        if (locked || !loaded || item.id === currentId) return;
        save();
        activate(item);
      });
      const removeButton = el("button", "btn btn--ghost chat-history__delete");
      removeButton.type = "button";
      removeButton.disabled = locked || !loaded;
      removeButton.title = "删除对话";
      removeButton.setAttribute("aria-label", `删除对话：${title}`);
      removeButton.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/></svg>';
      removeButton.addEventListener("click", () => remove(item));
      const renameButton = el("button", "btn btn--ghost chat-history__rename");
      renameButton.type = "button";
      renameButton.disabled = locked || !loaded;
      renameButton.title = "修改标题";
      renameButton.setAttribute("aria-label", `修改标题：${title}`);
      renameButton.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m16 3 5 5L8 21H3v-5Z"/><path d="m14 5 5 5"/></svg>';
      renameButton.addEventListener("click", () => rename(item, row));
      row.append(open, renameButton, removeButton);
      list.append(row);
    }
  }
  newButton.addEventListener("click", () => {
    if (locked || !loaded) return;
    save();
    activate(null, { create: true });
  });
  async function load() {
    retry.hidden = true;
    try {
      const result = await enqueue(() => api.chatHistory(scope));
      sessions = result.items;
      for (const item of sessions) documents.set(item.id, item);
      loaded = true;
      notice.hidden = true;
      await activate(sessions[0], { create: !sessions.length });
      onReady?.();
    } catch (error) {
      showError(error, "读取对话历史");
      retry.hidden = false;
    }
  }
  render();
  const ready = load();
  return { ready, save, suggestTitle, setLocked(value) { if (locked !== value) { locked = value; render(); } } };
}
