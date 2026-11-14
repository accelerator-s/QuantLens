import { renderState } from "../state-card/state-card.js";
import { runWithConfirm } from "../confirm-dialog/confirm-dialog.js";
import { hydrateIcons } from "../../core/icons.js";
import { toastError } from "../../core/toast.js";
import { badge } from "../../core/fmt.js";
import { codeEl, el, kvEl, selectMarkup, tableEl } from "../../core/dom.js";
import { createSelect } from "../../core/select.js";
import { captureLogScroll, updateTaskLog } from "../../core/task-log.js";

const PROVIDER_STATUS_LABEL = { draft: "草稿", active: "启用中", disabled: "已停用" };
const CACHE_STATE_LABEL = { fresh: "已最新", stale: "已过期", missing: "未构建" };
const TASK_STATUS_LABEL = { running: "执行中", completed: "已完成", failed: "失败" };
const BATCH_FREQ_LABEL = {
  day: "按日",
  daily: "按日",
  week: "按周",
  month: "按月",
  quarter: "按季度",
  year: "按年度",
  snapshot: "全量快照",
};
function isDataSyncTask(task) {
  return task?.category === "data_sync" || String(task?.name || "").startsWith("data-sync:");
}

export async function mount(root, ctx) {
  await hydrateIcons(root);

  const tablesHost = root.querySelector("[data-tables]");
  const checkHost = root.querySelector("[data-update-check]");
  const taskHost = root.querySelector("[data-ops-task]");
  const syncBtn = root.querySelector("[data-sync]");
  const tableProviderHost = root.querySelector("[data-table-provider]");
  tableProviderHost.innerHTML = selectMarkup("选择数据源");
  const tableProviderSelect = createSelect(tableProviderHost, {
    emptyLabel: "（无数据源）",
    onChange: (name) => loadTables(name),
  });
  let pollTimer = null;

  syncBtn.addEventListener("click", () => syncNow());

  ctx.bus.on("route", (id) => {
    if (id === "data") load();
  });
  await load();

  async function load() {
    await resumeActiveTask();
    await Promise.all([
      runUpdateCheck(),
      loadProviderTables(),
    ]);
  }

  async function resumeActiveTask() {
    try {
      const data = await ctx.api.activeDataSync();
      if (isDataSyncTask(data.task)) {
        renderTask(taskHost, data.task);
        pollTask(data.task.task_id, { resumed: true });
      }
    } catch (err) {
      toastError(ctx.bus, err, "同步状态恢复失败");
    }
  }

  async function runUpdateCheck() {
    await renderState(checkHost, { kind: "loading", title: "正在检测数据更新…" });
    try {
      const data = await ctx.api.dataUpdateCheck();
      renderUpdateCheck(data);
    } catch (err) {
      await renderState(checkHost, {
        kind: "error",
        title: "更新检测失败",
        detail: err.message,
        retry: () => runUpdateCheck(),
      });
    }
  }

  function renderUpdateCheck(data) {
    checkHost.innerHTML = "";
    if (data.last_check?.reason) {
      const previous = data.last_check;
      const reason = previous.state === "blocked" && /打包.*0%/.test(previous.reason)
        ? "打包进度停在 0%，检查已取消。"
        : previous.reason;
      checkHost.append(el("p", "item-row__meta data__last-check", `上次检查：${reason}`));
    }
    const indexSchedules = (data.schedules || []).filter(item => ['index_basic', 'index_daily'].includes(item.table));
    if (indexSchedules.length) {
      const schedule = el('article', 'card detail-card');
      schedule.append(el('h3', '', '指数更新计划'));
      for (const item of indexSchedules) schedule.append(el('p', 'item-row__meta',
        `${item.table === 'index_basic' ? '指数目录' : '指数日行情'}：${item.label}${item.last_checked ? ` · 上次检查 ${item.last_checked}` : ''}`));
      checkHost.append(schedule);
    }
    if (data.active_sync && !isDataSyncTask(data.active_sync)) return;
    if (data.active_sync) {
      const p = data.active_sync.progress || {};
      checkHost.append(
        el(
          "p",
          "item-row__meta",
          `后台任务「${data.active_sync.name}」正在执行（${Number(p.percent || 0).toFixed(1)}%），结束后可更新数据。`,
        ),
      );
      return;
    }
    if (data.note) {
      checkHost.append(el("p", "item-row__meta", data.note));
      return;
    }
    const staleCache = (data.cache || []).filter((c) => c.state !== "fresh");
    if (!data.needs_update) {
      if (!staleCache.length) {
        checkHost.append(el("p", "data__fresh", `${data.provider} 暂无待更新数据。`));
      } else {
        const names = staleCache
          .map((c) => `${c.variant}（${CACHE_STATE_LABEL[c.state] || c.state}）`)
          .join("、");
        checkHost.append(
          el(
            "p",
            "item-row__meta",
            `${data.provider} 当前无需下载，计算缓存待重建：${names}。点击「检查更新并同步」补齐。`,
          ),
        );
      }
      return;
    }

    const card = el("article", "card detail-card");
    const head = el("div", "detail-card__head");
    head.append(el("h3", "", "检测到数据更新"));
    head.append(badge("待同步", "warn"));
    card.append(head);
    if (data.publication_pending) {
      card.append(kvEl([["已下载至", data.downloaded_date || "—"], ["已发布至", data.published_date || "尚未发布"]]));
      card.append(el("p", "item-row__meta", "数据待发布，点击「检查更新并同步」。"));
    }
    if (data.first_download) {
      card.append(el("p", "item-row__meta", "首次同步将下载全量历史数据。"));
    } else if (data.market_needs_update) {
      card.append(
        kvEl([
          ["数据源", data.provider],
          ["最后更新", data.last_date],
          ["待更新", `${data.pending_days} 天（${data.start_date} → ${data.end_date}）`],
          ["预估新增", `约 ${Number(data.estimated_rows || 0).toLocaleString()} 条`],
          ["下载批次", data.batch_count ? `${data.batch_count} 个` : ""],
        ]),
      );
    }
    const pendingTables = data.pending_tables || [];
    if (pendingTables.length) {
      const modeCounts = new Map();
      for (const item of pendingTables) {
        modeCounts.set(item.mode_label, (modeCounts.get(item.mode_label) || 0) + 1);
      }
      card.append(
        kvEl([
          ["待处理表", `${pendingTables.length} 张`],
          [
            "更新层级",
            [...modeCounts.entries()].map(([name, count]) => `${name} ${count}`).join(" · "),
          ],
          ["计划批次", `${pendingTables.reduce((sum, item) => sum + Number(item.batches || 0), 0)} 个`],
        ]),
      );
    }
    if (staleCache.length) {
      card.append(
        el(
          "p",
          "item-row__meta",
          "计算缓存：" +
            (data.cache || [])
              .map((c) => `${c.variant} ${CACHE_STATE_LABEL[c.state] || c.state}`)
              .join(" · "),
        ),
      );
    }
    if ((data.audit_due || []).length) {
      const warn = el(
        "p",
        "item-row__meta",
        `需重新检查历史数据：${data.audit_due.join("、")}（数据量大、耗时较长）。`,
      );
      warn.style.color = "var(--danger, #c0392b)";
      card.append(warn);
    }
    checkHost.append(card);
  }

  async function syncNow() {
    try {
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.dataSync({ ...extra }),
        title: "检查更新并同步",
        confirmLabel: "确认同步",
      });
      if (!result || !result.task_id) return;
      ctx.bus.emit("toast", { kind: "info", title: "同步任务已启动" });
      pollTask(result.task_id);
    } catch (err) {
      toastError(ctx.bus, err, "同步启动失败");
    }
  }

  function pollTask(taskId, { resumed = false } = {}) {
    if (pollTimer) clearTimeout(pollTimer);
    syncBtn.disabled = true;
    let consecutiveFailures = 0;

    async function tick() {
      try {
        const task = await ctx.api.dataSyncProgress(taskId);
        consecutiveFailures = 0;
        if (!isDataSyncTask(task)) { taskHost.replaceChildren(); syncBtn.disabled = false; return; }
        renderTask(taskHost, task);
        if (task.status === "running") {
          pollTimer = setTimeout(tick, 2000);
          return;
        }
        syncBtn.disabled = false;
        if (task.status === "completed") {
          ctx.bus.emit("toast", {
            kind: "success",
            title: `${task.name} 已完成`,
          });
          await Promise.all([runUpdateCheck(), loadTables(tableProviderSelect.value)]);
        } else {
          ctx.bus.emit("toast", { kind: "error", title: `${task.name} 失败`, detail: task.error });
        }
      } catch (err) {
        consecutiveFailures += 1;
        if (consecutiveFailures < 3) {
          pollTimer = setTimeout(tick, 3000);
          return;
        }
        syncBtn.disabled = false;
        toastError(ctx.bus, err, "任务状态连续查询失败");
      }
    }
    tick();
  }

  function renderTask(host, task) {
    const previousLog = host.querySelector("[data-task-log]");
    const restoreScroll = captureLogScroll(previousLog);
    const card = el("article", "card detail-card");
    const head = el("div", "detail-card__head");
    head.append(el("h3", "", "同步进度"));
    head.append(
      badge(
        TASK_STATUS_LABEL[task.status] || task.status,
        task.status === "failed" ? "danger" : task.status === "completed" ? "safe" : "accent",
      ),
    );
    card.append(head);
    const progress = task.progress || {};
    const percent = Math.max(0, Math.min(100, Number(progress.percent || 0)));
    const current = progress.phase || progress.table || progress.step || "准备同步";
    const subtitle = [];
    if (progress.table && progress.mode) subtitle.push(progress.mode);
    if (progress.table_index) subtitle.push(`表 ${progress.table_index}/${progress.table_total}`);
    if (progress.batch_total) subtitle.push(`批次 ${progress.batch_index || 0}/${progress.batch_total}`);
    if (progress.field_total) subtitle.push(`字段 ${progress.field_index || 0}/${progress.field_total}`);
    if (progress.field) subtitle.push(progress.field);
    if (progress.window) subtitle.push(progress.window);

    const progressBox = el("div", "sync-progress");
    const progressHead = el("div", "sync-progress__head");
    const labels = el("div", "sync-progress__labels");
    labels.append(
      el("strong", "sync-progress__current", current),
      el(
        "span",
        "sync-progress__detail",
        subtitle.join(" · ") || `步骤 ${progress.step_index || 0}/${progress.step_total || "—"}`,
      ),
    );
    progressHead.append(labels, el("strong", "sync-progress__percent", `${percent.toFixed(1)}%`));
    const track = el("div", "sync-progress__track");
    track.setAttribute("role", "progressbar");
    track.setAttribute("aria-valuemin", "0");
    track.setAttribute("aria-valuemax", "100");
    track.setAttribute("aria-valuenow", String(percent));
    const fill = el("span", "sync-progress__fill");
    fill.style.width = `${percent}%`;
    track.append(fill);
    progressBox.append(progressHead, track);
    if (progress.pack_stage && task.status === 'running') {
      const pack = el('div', 'sync-progress__pack');
      const phase = {waiting:'等待打包进度', queued:'CSMAR 排队中', packing:'CSMAR 正在生成数据压缩包',
        ready:'CSMAR 压缩包已生成', transferring:'正在下载压缩包', parsing:'正在解析数据', reused:'复用本次已下载的日行情数据'}[progress.pack_stage] || 'CSMAR 打包';
      const known = progress.pack_percent !== null && progress.pack_percent !== undefined;
      const value = known ? Math.max(0, Math.min(100, Number(progress.pack_percent))) : null;
      const head = el('div', 'sync-progress__head');
      head.append(el('strong', '', phase), el('span', 'sync-progress__percent', known ? `${value}%` : '等待返回'));
      const meter = el('progress', 'ql-progress');
      meter.max = 100;
      if (known) meter.value = value;
      meter.setAttribute('aria-label', 'CSMAR 当前批次打包进度');
      const details = ['waiting', 'queued', 'packing', 'ready'].includes(progress.pack_stage)
        ? [`已等待 ${Number(progress.pack_elapsed_seconds || 0)} 秒`] : [];
      if (progress.pack_estimate_minutes) details.push(`数据源预计 ${progress.pack_estimate_minutes} 分钟`);
      pack.append(head, meter, el('p', 'item-row__meta', details.join(' · ')));
      progressBox.append(pack);
    }
    if (task.status === "running" && progress.state === "downloading" && !progress.pack_stage) {
      progressBox.append(
        el("p", "sync-progress__waiting", "数据源正在准备并传输本批数据，请稍候。"),
      );
    }
    card.append(progressBox);
    if (task.error) card.append(codeEl(task.error));
    const taskLog = updateTaskLog(previousLog, task.log, {
      subject: task.task_id || task.name,
      className: "sync-progress__log",
    });
    if (taskLog) card.append(taskLog);
    host.replaceChildren(card);
    restoreScroll();
  }

  async function loadProviderTables() {
    try {
      const data = await ctx.api.providers();
      const actives = data.items.filter((p) => p.status === "active");
      tableProviderSelect.setOptions(
        data.items.map((p) => ({
          value: p.name,
          label: `${p.name}（${PROVIDER_STATUS_LABEL[p.status] || p.status}）`,
        })),
        actives[0]?.name || data.items[0]?.name || "",
      );
      await loadTables(tableProviderSelect.value);
    } catch (err) {
      await renderState(tablesHost, {
        kind: "error",
        title: "数据表加载失败",
        detail: err.message,
        retry: () => loadProviderTables(),
      });
    }
  }

  async function loadTables(provider = "") {
    await renderState(tablesHost, { kind: "loading", title: "加载表清单…" });
    try {
      const data = await ctx.api.dataTables(provider ? { provider } : undefined);
      tablesHost.innerHTML = "";
      if (!data.items.length) {
        renderState(tablesHost, {
          kind: "empty",
          title: "暂无数据表",
          detail: data.note || "该数据源尚未添加任何数据表。",
        });
        return;
      }
      tablesHost.append(
        tableEl(
          [
            { key: "table_name", label: "数据内容" },
            { key: "staged_name", label: "本地表名" },
            { key: "table_name_phy", label: "来源物理表" },
            { key: "batch_freq", label: "下载批次", format: (v) => BATCH_FREQ_LABEL[v] || v },
            {
              key: "fields",
              label: "字段数",
              num: true,
              format: (v) => (Array.isArray(v) ? String(v.length) : (v ?? "—")),
            },
            {
              key: "verified",
              label: "验收",
              format: (v) => badge(v ? "已验收" : "未验收", v ? "safe" : "warn"),
            },
          ],
          data.items,
        ),
      );
    } catch (err) {
      await renderState(tablesHost, {
        kind: "error",
        title: "表清单加载失败",
        detail: err.message,
        retry: () => loadTables(),
      });
    }
  }
}
