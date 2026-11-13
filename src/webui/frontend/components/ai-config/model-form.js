import { el, selectMarkup } from "../../core/dom.js";
import { createSelect } from "../../core/select.js";
import { loadIcon } from "../../core/icons.js";

export function modelForm({ existing = null, tags, api, onSave, onTestSuccess = () => {}, onTestFailure = () => {} }) {
  const previousFocus = document.activeElement;
  const backdrop = el("div", "dialog-backdrop");
  const dialog = el("form", "dialog dialog--wide ai-model-form");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "ai-model-form-title");
  const heading = el("h3", "dialog__title", existing ? `编辑模型 ${existing.name}` : "添加模型");
  heading.id = "ai-model-form-title";
  const body = el("div", "dialog__body");
  const controls = {};
  const selects = [];
  let generation = 0;
  let closed = false;
  let saving = false;
  let loading = false;
  let testing = false;
  let verified = Boolean(existing?.configured);
  let modelId = existing?.api_model_id || "";
  let preferredModelId = modelId;

  function inputField(key, label, value = "", type = "text") {
    const wrap = el("label", "field");
    const input = el("input", "input");
    input.name = key;
    input.type = type;
    input.value = value;
    input.autocomplete = type === "password" ? "new-password" : "off";
    wrap.append(el("span", "field__label", label), input);
    body.append(wrap);
    controls[key] = input;
    return input;
  }
  if (!existing) inputField("name", "名称（必填）").required = true;

  const protocols = el("fieldset", "ai-model-form__protocols");
  protocols.append(el("legend", "field__label", "接口类型"));
  const cards = el("div", "ai-model-form__providers");
  for (const [value, label] of [["anthropic", "Anthropic"], ["openai", "OpenAI"]]) {
    const card = el("label", "ai-model-form__provider");
    card.dataset.provider = value;
    const radio = el("input", "ai-model-form__radio");
    radio.type = "radio";
    radio.name = "api_type";
    radio.value = value;
    radio.checked = value === (existing?.api_type || "openai");
    radio.addEventListener("change", invalidateConnection);
    const content = el("span", "ai-model-form__provider-content");
    const icon = el("span", "ai-model-form__provider-icon");
    loadIcon(`ai-${value}`).then(svg => { if (icon.isConnected) icon.innerHTML = svg; });
    const check = el("span", "ai-model-form__provider-check checkmark");
    check.setAttribute("aria-hidden", "true");
    content.append(icon, el("span", "", label), check);
    card.append(radio, content);
    cards.append(card);
  }
  protocols.append(cards);
  body.append(protocols);
  const endpoint = inputField("provider_endpoint", "服务地址（必填）", existing?.provider_endpoint || "", "url");
  endpoint.required = true;
  endpoint.placeholder = "https://…/v1";
  const key = inputField("api_key", existing?.configured ? "接口密钥（留空保留）" : "接口密钥", "", "password");
  endpoint.addEventListener("input", invalidateConnection);
  key.addEventListener("input", invalidateConnection);

  const modelsField = el("div", "field");
  const modelsHeader = el("div", "ai-model-form__row");
  const modelsLabel = el("span", "field__label", "服务端模型（必填）");
  modelsLabel.id = "ai-model-select-label";
  const fetchButton = el("button", "btn btn--ghost btn--sm", "获取模型");
  fetchButton.type = "button";
  fetchButton.addEventListener("click", fetchModels);
  modelsHeader.append(modelsLabel, fetchButton);
  const selectHost = el("div", "select");
  selectHost.innerHTML = selectMarkup("请先获取模型");
  selectHost.querySelector("[data-select-trigger]").setAttribute("aria-labelledby", modelsLabel.id);
  const modelSelect = createSelect(selectHost, { onChange(value) { modelId = value; preferredModelId = value; generation++; testing = false; verified = false; error.hidden = true; refreshButtons(); } });
  selects.push(modelSelect);
  if (modelId) modelSelect.setOptions([{value: modelId, label: modelId}], modelId);
  const discoveryStatus = el("span", "ai-model-form__status");
  discoveryStatus.setAttribute("role", "status");
  modelsField.append(modelsHeader, selectHost, discoveryStatus);
  body.append(modelsField);

  const capabilities = el("fieldset", "ai-model-form__capabilities");
  capabilities.append(el("legend", "field__label", "能力标签（必填）"));
  const checkboxes = el("div", "ai-model-form__checks");
  for (const tag of tags) {
    const label = el("label", "ai-model-form__check");
    const checkbox = el("input");
    checkbox.type = "checkbox";
    checkbox.name = "capability_tags";
    checkbox.value = tag.value;
    checkbox.checked = Boolean(existing?.capability_tags?.includes(tag.value));
    label.append(checkbox, el("span", "", tag.label));
    checkboxes.append(label);
  }
  capabilities.append(checkboxes);
  body.append(capabilities);

  const error = el("p", "ai-model-form__error");
  error.setAttribute("role", "alert");
  error.hidden = true;
  body.append(error);
  const actions = el("div", "dialog__actions");
  const cancel = el("button", "btn btn--ghost", "取消");
  cancel.type = "button";
  cancel.addEventListener("click", close);
  const submit = el("button", "btn btn--primary", "测试连通性");
  submit.type = "submit";
  actions.append(cancel, submit);
  dialog.append(heading, body, actions);
  backdrop.append(dialog);
  document.body.append(backdrop);
  refreshButtons();
  (dialog.querySelector("input[name=name]") || dialog.querySelector("input[name=api_type]:checked"))?.focus();
  backdrop.addEventListener("click", event => { if (event.target === backdrop) close(); });
  dialog.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      if (dialog.querySelector(".select.is-open")) return;
      event.preventDefault();
      close();
    }
    if (event.key === "Tab") {
      const focusable = [...dialog.querySelectorAll("button:not(:disabled), input:not(:disabled), [tabindex='0']")].filter(node => !node.closest("[hidden]") && (node.type !== "radio" || node.checked));
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  });
  dialog.addEventListener("submit", async event => {
    event.preventDefault();
    if (closed || saving || loading || testing) return;
    error.hidden = true;
    const selectedTags = [...checkboxes.querySelectorAll("input:checked")].map(input => input.value);
    if (!modelId) return showError("请先获取并选择服务端模型。");
    if (!selectedTags.length) { checkboxes.querySelector("input")?.focus(); return showError("请至少选择一项能力。"); }
    if (!validConnection()) return;
    if (!verified) {
      await testConnection();
      return;
    }
    saving = true;
    refreshButtons();
    try {
      await onSave({name: existing?.name || controls.name.value.trim(), kind: "api_model", ...connection(), capability_tags: selectedTags, enabled: existing?.enabled ?? true, notes: existing?.notes || ""});
      saving = false;
      close();
    } catch (err) {
      if (!closed) showError(err.message || "保存失败，请重试。");
    } finally {
      saving = false;
      if (!closed) refreshButtons();
    }
  });

  function connection() {
    return {api_type: dialog.querySelector("input[name=api_type]:checked").value, provider_endpoint: endpoint.value.trim(), api_key: key.value.trim(), api_model_id: modelId};
  }
  function requestPayload() { return {...connection(), existing_name: existing?.name || ""}; }
  function showError(message) { error.textContent = message; error.hidden = false; }
  function refreshButtons() {
    fetchButton.disabled = saving || loading || testing || !endpoint.value.trim();
    fetchButton.textContent = loading ? "获取中…" : "获取模型";
    submit.textContent = saving ? "保存中…" : testing ? "测试中…" : verified ? (existing ? "保存" : "添加") : "测试连通性";
    submit.disabled = saving || loading || testing || !modelId;
    submit.setAttribute("aria-busy", String(saving || testing));
    cancel.disabled = saving;
    for (const input of dialog.querySelectorAll("input")) input.disabled = saving;
    selectHost.querySelector("[data-select-trigger]").disabled = saving || loading;
  }
  function invalidateConnection() {
    generation++;
    loading = false;
    testing = false;
    verified = false;
    if (modelId) preferredModelId = modelId;
    modelId = "";
    modelSelect.setOptions([], "", "请重新获取模型");
    discoveryStatus.textContent = "";
    error.hidden = true;
    refreshButtons();
  }
  function validConnection() {
    if (!endpoint.reportValidity()) return false;
    if (!key.value.trim() && !existing?.configured) { key.focus(); showError("请填写接口密钥。"); return false; }
    error.hidden = true;
    return true;
  }
  async function fetchModels() {
    if (!validConnection()) return;
    modelSelect.close();
    const version = ++generation;
    loading = true;
    verified = false;
    discoveryStatus.textContent = "";
    refreshButtons();
    try {
      const data = await api.aiConnectionModels(requestPayload());
      if (closed || version !== generation) return;
      const options = (data.items || []).map(item => ({value: item.id, label: item.name && item.name !== item.id ? `${item.name} · ${item.id}` : item.id}));
      modelSelect.setOptions(options, preferredModelId, "服务端未返回模型");
      modelId = modelSelect.value;
      discoveryStatus.textContent = options.length ? `已获取 ${options.length} 个模型` : "服务端未返回可用模型。";
    } catch (err) {
      if (!closed && version === generation) showError(err.message || "获取模型失败，请重试。");
    } finally {
      if (!closed && version === generation) { loading = false; refreshButtons(); }
    }
  }
  async function testConnection() {
    if (!validConnection() || !modelId) return;
    const version = ++generation;
    testing = true;
    verified = false;
    refreshButtons();
    try {
      const data = await api.aiTestConnection(requestPayload());
      if (closed || version !== generation) return;
      if (data.ok === false) throw new Error("连接失败，请重试。");
      verified = true;
      onTestSuccess(data);
    } catch (err) {
      if (!closed && version === generation) {
        showError(err.message || "连接失败，请重试。");
        onTestFailure(err);
      }
    } finally {
      if (!closed && version === generation) { testing = false; refreshButtons(); }
    }
  }
  function close() {
    if (saving) return;
    closed = true;
    generation++;
    key.value = "";
    selects.forEach(select => select.destroy?.());
    backdrop.remove();
    previousFocus?.focus();
  }
  return { close };
}
