import { modelForm } from "./model-form.js";
import { labelText } from "../../core/labels.js";
import { renderState } from "../state-card/state-card.js";
import { runWithConfirm } from "../confirm-dialog/confirm-dialog.js";
import { formDialog } from "../confirm-dialog/form-dialog.js";
import { hydrateIcons } from "../../core/icons.js";
import { toastError } from "../../core/toast.js";
import { badge } from "../../core/fmt.js";
import { el, kvEl, selectMarkup, tableEl as createTable } from "../../core/dom.js";
import { createSelect } from "../../core/select.js";

function tableEl(columns, rows, options = {}) {
  return createTable(columns, rows, {...options, pagination: false});
}

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
      modelsHost.innerHTML = "";
      if (!data.items.length) {
        renderState(modelsHost, {
          kind: "empty",
          title: "暂无添加模型",
        });
        return;
      }
      function clipped(value, className = '') {
        const text = value || '—';
        const span = el('span', `ai-config__cell-text ${className}`, text);
        span.title = text;
        return span;
      }
      const columns = [
        {key: 'name', label: '名称', format: value => clipped(value, 'ai-config__model-name')},
        {key: 'api_type', label: '接口', format: value => badge(value === 'anthropic' ? 'Anthropic' : 'OpenAI', 'muted')},
        {key: 'api_model_id', label: '模型', format: (value, row) => {
          const span = clipped(value);
          span.title = [value, row.provider_endpoint].filter(Boolean).join('\n');
          return span;
        }},
        {key: 'capability_tags', label: '能力', format: value => tagBadges(value, 1)},
        {key: 'enabled', label: '状态', format: (value, row) => {
          const toggle = el('button', 'btn btn--ghost ai-config__model-status');
          toggle.type = 'button';
          toggle.setAttribute('role', 'switch');
          toggle.setAttribute('aria-checked', String(value));
          toggle.setAttribute('aria-label', `${value ? '停用' : '启用'} ${row.name}`);
          toggle.title = `${value ? '点击停用' : '点击启用'}${row.configured ? '' : ' · 未配置密钥'}`;
          toggle.append(badge(value ? '已启用' : '已停用', value ? 'safe' : 'muted'));
          toggle.addEventListener('click', async () => {
            if (toggle.disabled) return;
            toggle.disabled = true;
            try { await setEnabled(row.name, !value); }
            finally { toggle.disabled = false; }
          });
          const state = el('span', 'ai-config__model-status-cell');
          state.append(toggle);
          if (!row.configured) state.append(badge('缺密钥', 'warn'));
          return state;
        }},
        {key: 'name', label: '操作', format: (name, row) => {
          const actions = el('span', 'ai-config__model-actions');
          const edit = el('button', 'btn btn--ghost', '编辑');
          edit.type = 'button';
          edit.addEventListener('click', () => openModelForm(row));
          actions.append(edit);
          if (row.kind === 'api_model') {
            const test = el('button', 'btn btn--ghost ai-config__model-test', '测试');
            test.type = 'button';
            test.title = '测试连通性';
            test.setAttribute('aria-label', `测试 ${name} 的连通性`);
            test.addEventListener('click', () => testModel(name, test));
            actions.append(test);
          }
          const remove = el('button', 'btn btn--ghost ai-config__model-remove', '删除');
          remove.type = 'button';
          remove.addEventListener('click', () => removeModel(name, row.configured));
          actions.append(remove);
          return actions;
        }},
      ];
      const modelTable = tableEl(columns, data.items, {onRowRender:row=>{
        [...row.children].forEach((cell,index)=>{cell.dataset.label=columns[index].label;});
      }});
      modelTable.classList.add('ai-config__models-table');
      modelsHost.append(modelTable);
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
      matrixTags = data.tags || [];
      const tags = el("span", "tag-badges");
      for (const t of data.tags || []) tags.append(badge(t, "muted"));
      if (matrixTags.length) matrixHost.append(tags);
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
    if (btn.disabled) return;
    btn.disabled = true;
    btn.textContent = "测试中…";
    try {
      const data = await ctx.api.aiTestModel(name);
      ctx.bus.emit("toast", { kind: "success", title: `连接成功 · ${data.latency_ms} ms`, detail: name });
    } catch (err) {
      toastError(ctx.bus, err, `${name} 连通性测试失败`);
    } finally {
      btn.disabled = false;
      btn.textContent = "测试";
    }
  }

  async function openModelForm(existing = null) {
    if (!matrixTags.length) {
      try {
        matrixTags = (await ctx.api.aiMatrix()).tags || [];
      } catch (err) {
        toastError(ctx.bus, err, "能力标签加载失败");
        return;
      }
    }
    modelForm({
      existing,
      tags: matrixTags.map(value => ({value, label: CAPABILITY_LABELS[value] || labelText(value)})),
      api: ctx.api,
      onTestSuccess(data) {
        ctx.bus.emit("toast", {kind: "success", title: `连接成功 · ${data.latency_ms} ms`});
      },
      onTestFailure(error) {
        toastError(ctx.bus, error, "连接失败");
      },
      async onSave(payload) {
        const data = existing
          ? await ctx.api.aiUpdateModel(existing.name, payload)
          : await ctx.api.aiAddModel(payload);
        ctx.bus.emit("toast", {kind: "success", title: `${existing ? "已更新" : "已添加"} ${data.model.name}`});
        await loadModels();
      },
    });
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
          {key:'executors',label:'执行方式',kind:'multiselect',required:true,value:task.permitted_executors||[],options:['api_model','human','mixed'].map(value=>({value,label:labelText(value)}))},
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
