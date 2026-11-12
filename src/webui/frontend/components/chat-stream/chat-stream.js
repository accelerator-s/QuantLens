

export function createChat(host, { send, greeting = "", placeholder = "输入内容，回车发送" } = {}) {
  const history = [];
  host.innerHTML = "";
  host.classList.add("chat");

  const list = document.createElement("div");
  list.className = "chat__list";
  const form = document.createElement("form");
  form.className = "chat__form";
  const input = document.createElement("textarea");
  input.className = "textarea chat__input";
  input.rows = 2;
  input.placeholder = placeholder;
  const btn = document.createElement("button");
  btn.type = "submit";
  btn.className = "btn btn--primary chat__send";
  btn.textContent = "发送";
  form.append(input, btn);
  host.append(list, form);

  if (greeting) appendBubble("assistant", greeting, { transient: true });

  function appendBubble(role, text, { transient = false, model = "" } = {}) {
    const item = document.createElement("div");
    item.className = `chat__item chat__item--${role}`;
    const bubble = document.createElement("div");
    bubble.className = "chat__bubble";
    bubble.textContent = text;
    item.append(bubble);
    if (model) {
      const meta = document.createElement("span");
      meta.className = "item-row__meta";
      meta.textContent = model;
      item.append(meta);
    }
    list.append(item);
    list.scrollTop = list.scrollHeight;
    if (!transient) history.push({ role, content: text });
    return bubble;
  }

  let busy = false;
  async function submit() {
    const text = input.value.trim();
    if (!text || busy) return;
    input.value = "";
    appendBubble("user", text);

    busy = true;
    btn.disabled = true;
    const pendingItem = document.createElement("div");
    pendingItem.className = "chat__item chat__item--assistant";
    const pending = document.createElement("div");
    pending.className = "chat__bubble chat__bubble--pending";
    pending.textContent = "思考中…";
    pendingItem.append(pending);
    list.append(pendingItem);
    list.scrollTop = list.scrollHeight;

    try {
      const res = await send(history.slice());
      const reply = typeof res === "string" ? res : res?.text || "";
      const model = typeof res === "string" ? "" : res?.model || "";
      pendingItem.remove();
      appendBubble("assistant", reply, { model });
    } catch (err) {
      pendingItem.remove();
      appendBubble("assistant", `调用失败：${err.message}`, { transient: true });
    } finally {
      busy = false;
      btn.disabled = false;
      input.focus();
    }
  }

  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    submit();
  });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      submit();
    }
  });

  return {
    history,
    clear() {
      history.length = 0;
      list.innerHTML = "";
      if (greeting) appendBubble("assistant", greeting, { transient: true });
    },
  };
}
