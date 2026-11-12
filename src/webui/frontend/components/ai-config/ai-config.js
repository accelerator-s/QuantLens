import { labelText } from "../../core/labels.js";
import { renderState } from "../state-card/state-card.js";
import { runWithConfirm } from "../confirm-dialog/confirm-dialog.js";
import { formDialog } from "../confirm-dialog/form-dialog.js";
import { hydrateIcons } from "../../core/icons.js";
import { toastError } from "../../core/toast.js";
import { badge, fmtTime } from "../../core/fmt.js";
import { el, kvEl, selectMarkup, tableEl } from "../../core/dom.js";
import { createSelect } from "../../core/select.js";

const MODEL_KINDS = ["api_model", "external_ide_ai"];
const CAPABILITY_LABELS = {
  reasoning_high: "深度推理",
  coding_high: "代码生成",
  cheap_batch: "低成本批处理",
  long_context: "长上下文",
  review_strict: "严格复核",
  structured_output: "结构化输出",
  debugging: "调试诊断",
  risk_aware: "风险识别",
  web_browsing: "联网检索",
};


export async function mount(root, ctx) {
  await hydrateIcons(root);
  let matrixTags = [];

  root.querySelector("[data-model-add]").addEventListener("click", () => openModelForm());

  const discussHost = root.querySelector("[data-discuss]");
  const modelsHost = root.querySelector("[data-models]");
  const matrixHost = root.querySelector("[data-matrix]");
  const routeHost = root.querySelector("[data-route]");

  const taskSelectHost = root.querySelector("[data-task-select]");
  taskSelectHost.innerHTML = selectMarkup("选择任务");
  const taskSelect = createSelect(taskSelectHost, {
    emptyLabel: "（矩阵为空）",
    onChange: (task) => loadRoutePreview(task),
  });

  ctx.bus.on("route", (id) => {
    if (id === "ai-config") load();
  });
  await load();

  async function load() {
    await Promise.all([loadModels(), loadMatrix()]);
  }

  function tagBadges(tags, max = 3) {
    const span = el("span", "tag-badges");
    const list = tags || [];
    if (!list.length) {
      span.textContent = "—";
      return span;
    }
    for (const t of list.slice(0, max)) span.append(badge(t, "muted"));
    if (list.length > max) {
      const more = badge(`+${list.length - max}`, "accent");
      more.title = list.slice(max).map(labelText).join("、");
      span.append(more);
    }
    span.title = list.map(labelText).join("、");
    return span;
  }

  async function loadModels() {
    await renderState(modelsHost, { kind: "loading", title: "加载模型列表…" });
    try {
      const data = await ctx.api.aiModels();
      discussHost.innerHTML = "";
      discussHost.append(
        kvEl([["默认对话模型", data.discuss_model || "（未设置）"]]),
      );
      modelsHost.innerHTML = "";
      if (!data.items.length) {
        renderState(modelsHost, {
          kind: "empty",
          title: "暂无添加模型",
          detail: "点击“添加模型”，连接兼容 OpenAI 接口的模型服务。",
        });
        return;
      }
      modelsHost.append(
        tableEl(
          [
            { key: "name", label: "名称" },
            { key: "kind", label: "类型", format: (v) => badge(v, "muted") },
            { key: "provider_endpoint", label: "服务地址" },
            { key: "api_model_id", label: "接口模型" },
            { key: "capability_tags", label: "能力标签", format: (v) => tagBadges(v) },
            {
              key: "enabled",
              label: "启用",
              format: (v) => badge(v ? "已启用" : "已停用", v ? "safe" : "muted"),
            },
            {
              key: "configured",
              label: "密钥",
              format: (v, row) => {
                const span = el("span", "tag-badges");
                span.append(badge(v ? "已配置" : "未配置", v ? "safe" : "warn"));
                span.append(el("code", "ai-config__env", row.env_var));
                return span;
              },
            },
            { key: "updated_at", label: "更新时间", format: (v) => fmtTime(v) },
            {
              key: "name",
              label: "操作",
              format: (name, row) => {
                const wrap = el("span", "tag-badges");
                const toggle = el("button", "btn btn--ghost", row.enabled ? "停用" : "启用");
                toggle.type = "button";
                toggle.addEventListener("click", () => setEnabled(name, !row.enabled));
                wrap.append(toggle);
                const edit = el("button", "btn btn--ghost", "编辑");
                edit.type = "button";
                edit.addEventListener("click", () => openModelForm(row));
                wrap.append(edit);
                if (row.kind === "api_model") {
                  const test = el("button", "btn btn--ghost", "测试");
                  test.type = "button";
                  test.addEventListener("click", () => testModel(name, test));
                  wrap.append(test);
                }
                if (row.configured) {
                  const delKey = el("button", "btn btn--ghost", "删除密钥");
                  delKey.type = "button";
                  delKey.addEventListener("click", () => deleteKey(name));
                  wrap.append(delKey);
                }
                if (name !== data.discuss_model) {
                  const discuss = el("button", "btn btn--ghost", "设为对话模型");
                  discuss.type = "button";
                  discuss.addEventListener("click", () => setDiscuss(name));
                  wrap.append(discuss);
                }
                const remove = el("button", "btn btn--danger", "删除");
                remove.type = "button";
                remove.addEventListener("click", () => removeModel(name, row.configured));
                wrap.append(remove);
                return wrap;
              },
            },
          ],
          data.items,
        ),
      );
    } catch (err) {
      await renderState(modelsHost, {
        kind: "error",
        title: "模型列表加载失败",
        detail: err.message,
        retry: () => loadModels(),
      });
    }
  }

  async function loadMatrix() {
    await renderState(matrixHost, { kind: "loading", title: "加载任务配置…" });
    try {
      const data = await ctx.api.aiMatrix();
      matrixHost.innerHTML = "";
      const head = el("div", "toolbar");
      head.append(badge(`版本 ${data.version}`, "accent"));
      matrixTags = data.tags || [];
      const tags = el("span", "tag-badges");
      for (const t of data.tags || []) tags.append(badge(t, "muted"));
      head.append(tags);
      matrixHost.append(head);
      matrixHost.append(
        tableEl(
          [
            { key: "task", label: "任务" },
            { key: "description", label: "说明" },
            { key: "required_tags", label: "必需标签", format: (v) => tagBadges(v) },
            { key: "optional_tags", label: "可选标签", format: (v) => tagBadges(v) },
            { key: "permitted_executors", label: "执行方式", format: (v) => tagBadges(v) },
            {
              key: "task",
              label: "操作",
              format: (_, row) => {
                const btn = el("button", "btn btn--ghost", "调整");
                btn.type = "button";
                btn.addEventListener("click", () => editMatrixTask(row));
                return btn;
              },
            },
          ],
          data.tasks || [],
        ),
      );
      const names = (data.tasks || []).map((t) => ({value:t.task,label:labelText(t.task)}));
      taskSelect.setOptions(names);
      if (names.length) await loadRoutePreview(taskSelect.value);
    } catch (err) {
      await renderState(matrixHost, {
        kind: "error",
        title: "任务配置加载失败",
        detail: err.message,
        retry: () => loadMatrix(),
      });
    }
  }

  async function loadRoutePreview(task) {
    if (!task) return;
    await renderState(routeHost, { kind: "loading", title: "评估候选模型…" });
    try {
      const data = await ctx.api.aiRoutePreview(task);
      routeHost.innerHTML = "";
      const card = el("article", "card detail-card");
      const head = el("div", "detail-card__head");
      head.append(el("h3", "", labelText(data.task)));
      card.append(head);
      if (data.description) card.append(el("p", "item-row__meta", data.description));
      card.append(
        kvEl([
          ["必需标签", (data.required_tags || []).map(labelText).join("、") || "（无）"],
          ["可选标签", (data.optional_tags || []).map(labelText).join("、") || "（无）"],
          ["执行方式", (data.permitted_executors || []).map(labelText).join("、") || "（无）"],
        ]),
      );
      if (!(data.candidates || []).length) {
        card.append(el("p", "item-row__meta", "没有满足条件的已启用模型。"));
      } else {
        card.append(
          tableEl(
            [
              { key: "model", label: "候选模型" },
              { key: "score", label: "得分", num: true },
              { key: "capability_tags", label: "能力标签", format: (v) => tagBadges(v) },
            ],
            data.candidates,
          ),
        );
      }
      routeHost.append(card);
    } catch (err) {
      await renderState(routeHost, {
        kind: "error",
        title: "模型匹配失败",
        detail: err.message,
        retry: () => loadRoutePreview(task),
      });
    }
  }

  async function setEnabled(name, enabled) {
    try {
      await ctx.api.aiSetEnabled(name, enabled);
      ctx.bus.emit("toast", { kind: "success", title: `${name} 已${enabled ? "启用" : "停用"}` });
      await loadModels();
    } catch (err) {
      toastError(ctx.bus, err, "模型状态修改失败");
    }
  }

  async function setDiscuss(name) {
    try {
      await ctx.api.aiSetDiscussModel(name);
      ctx.bus.emit("toast", { kind: "success", title: `默认对话模型已设为 ${name}` });
      await loadModels();
    } catch (err) {
      toastError(ctx.bus, err, "对话模型设置失败");
    }
  }

  async function removeModel(name, hasKey) {
    try {
      let keepKey = false;
      if (hasKey) {
        const values = await formDialog({
          title: `删除模型 ${name}`,
          submitLabel: "下一步",
          fields: [{ key: "keep_key", label: "保留本地 接口密钥", kind: "checkbox" }],
        });
        if (!values) return;
        keepKey = values.keep_key;
      }
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.aiRemoveModel(name, { keep_key: keepKey, ...extra }),
        title: `删除模型 ${name}`,
        confirmLabel: "确认删除",
        danger: true,
      });
      if (!result) return;
      ctx.bus.emit("toast", { kind: "success", title: `已删除模型 ${name}` });
      await loadModels();
    } catch (err) {
      toastError(ctx.bus, err, "模型删除失败");
    }
  }

  async function testModel(name, btn) {
    btn.disabled = true;
    try {
      const data = await ctx.api.aiTestModel(name);
      ctx.bus.emit("toast", { kind: "success", title: `${name} 连通性正常`, detail: data.reply });
    } catch (err) {
      toastError(ctx.bus, err, `${name} 连通性测试失败`);
    } finally {
      btn.disabled = false;
    }
  }

  async function deleteKey(name) {
    try {
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.aiDeleteModelKey(name, extra),
        title: `删除 ${name} 的本地 接口密钥`,
        confirmLabel: "确认删除",
        danger: true,
      });
      if (!result) return;
      ctx.bus.emit("toast", { kind: "success", title: `已删除 ${name} 的本地密钥` });
      await loadModels();
    } catch (err) {
      toastError(ctx.bus, err, "密钥删除失败");
    }
  }

  function splitTags(text) {
    return text
      .split(/[,，]/)
      .map((s) => s.trim())
      .filter(Boolean);
  }

  async function openModelForm(existing = null) {
    if (!matrixTags.length) {
      try {
        const matrix = await ctx.api.aiMatrix();
        matrixTags = matrix.tags || [];
      } catch (err) {
        toastError(ctx.bus, err, "能力标签加载失败");
        return;
      }
    }
    const isEdit = Boolean(existing);
    const fields = [];
    if (!isEdit) {
      fields.push({ key: "name", label: "名称", required: true, placeholder: "用于区分不同模型连接" });
    }
    fields.push(
      {
        key: "kind",
        label: "类型",
        kind: "select",
        options: MODEL_KINDS.map(value=>({value,label:labelText(value)})),
        value: existing?.kind || "api_model",
      },
      {
        key: "provider_endpoint",
        label: "服务地址",
        value: existing?.provider_endpoint || "",
        placeholder: "兼容接口的服务地址，如 https://…/v1",
      },
      { key: "api_model_id", label: "服务端模型名称", value: existing?.api_model_id || "" },
      {
        key: "capability_tags",
        label: "能力标签",
        kind: "multiselect",
        value: existing?.capability_tags || [],
        requiredWhen: (values) => values.kind === "api_model",
        placeholder: "选择模型具备的能力（可多选）",
        hint: "选项来自任务配置；接口模型至少选择一项。",
        options: matrixTags.map((tag) => ({
          value: tag,
          label: CAPABILITY_LABELS[tag] || "其他能力",
        })),
      },
      {
        key: "api_key",
        label: isEdit ? "接口密钥（留空则不修改）" : "接口密钥（可留空）",
        kind: "password",
      },
      { key: "notes", label: "备注", value: existing?.notes || "" },
    );
    const values = await formDialog({
      title: isEdit ? `编辑模型 ${existing.name}` : "添加模型",
      submitLabel: isEdit ? "保存" : "添加",
      fields,
    });
    if (!values) return;
    const payload = {
      name: isEdit ? existing.name : values.name,
      kind: values.kind,
      provider_endpoint: values.provider_endpoint,
      api_model_id: values.api_model_id,
      capability_tags: values.capability_tags,
      notes: values.notes,
      enabled: isEdit ? existing.enabled : true,
      api_key: values.api_key || "",
    };
    try {
      const data = isEdit
        ? await ctx.api.aiUpdateModel(existing.name, payload)
        : await ctx.api.aiAddModel(payload);
      ctx.bus.emit("toast", {
        kind: "success",
        title: `${isEdit ? "已更新" : "已添加"} ${data.model.name}`,
        detail: data.key_saved ? "接口密钥 已保存到本地凭据。" : "",
      });
      await loadModels();
    } catch (err) {
      toastError(ctx.bus, err, isEdit ? "模型更新失败" : "模型添加失败");
    }
  }

  async function editMatrixTask(task) {
    try {
      const values = await formDialog({
        title: `调整任务 ${labelText(task.task)}`,
        message: '选择完成此任务所需的能力与执行方式。',
        submitLabel: '查看并确认',
        fields: [
          {key:'required',label:'必需能力',kind:'multiselect',value:task.required_tags||[],options:matrixTags.map(value=>({value,label:labelText(value)}))},
          {key:'optional',label:'可选能力',kind:'multiselect',value:task.optional_tags||[],options:matrixTags.map(value=>({value,label:labelText(value)}))},
          {key:'executors',label:'执行方式',kind:'multiselect',required:true,value:task.permitted_executors||[],options:['api_model','external_ide_ai','human','mixed'].map(value=>({value,label:labelText(value)}))},
        ],
      });
      if (!values) return;
      const result = await runWithConfirm({
        execute: (extra) =>
          ctx.api.aiSetMatrixTask(task.task, {
            required: values.required,
            optional: values.optional,
            executors: values.executors,
            ...extra,
          }),
        title: `更新任务配置 ${labelText(task.task)}`,
        confirmLabel: "确认更新",
      });
      if (!result) return;
      ctx.bus.emit("toast", { kind: "success", title: `已更新任务 ${labelText(task.task)}` });
      await loadMatrix();
    } catch (err) {
      toastError(ctx.bus, err, "矩阵更新失败");
    }
  }
}
