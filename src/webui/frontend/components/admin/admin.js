import { renderState } from "../state-card/state-card.js";
import { runWithConfirm } from "../confirm-dialog/confirm-dialog.js";
import { formDialog } from "../confirm-dialog/form-dialog.js";
import { toastError } from "../../core/toast.js";
import { badge, fmtTime } from "../../core/fmt.js";
import { el, kvEl, tableEl } from "../../core/dom.js";


export async function mount(root, ctx) {
  const sessionHost = root.querySelector("[data-session]");
  const providersHost = root.querySelector("[data-providers]");

  root.querySelector("[data-market-set]").addEventListener("click", () => setMarket());
  root.querySelector("[data-fee-set]").addEventListener("click", () => setFee());
  root.querySelector("[data-doctor-init]").addEventListener("click", () => doctorInit());
  root.querySelector("[data-memory-config]").addEventListener("click", () => memoryConfigure());
  root.querySelector("[data-trash-purge]").addEventListener("click", () => trashPurge());

  ctx.bus.on("route", (id) => {
    if (id === "admin") load();
  });
  await load();

  async function load() {
    await Promise.all([loadSession(), loadProviders()]);
  }

  async function loadSession() {
    sessionHost.innerHTML = "";
    try {
      const s = await ctx.api.session();
      sessionHost.append(
        kvEl([
          [
            "交易市场",
            s.active_market ? `${s.active_market} ${s.active_instrument_class}` : "（未选择）",
          ],
          ["佣金费率", s.commission_rate || "0.0003（预置默认：万三）"],
          ["上次登录", fmtTime(s.last_login)],
        ]),
      );
    } catch (err) {
      sessionHost.append(el("p", "item-row__meta", `会话加载失败：${err.message}`));
    }
  }

  async function loadProviders() {
    await renderState(providersHost, { kind: "loading", title: "加载数据源…" });
    try {
      const data = await ctx.api.providers();
      providersHost.innerHTML = "";
      if (!data.items.length) {
        renderState(providersHost, {
          kind: "empty",
          title: "暂无注册数据源",
          detail: "暂无数据源配置。",
        });
        return;
      }
      providersHost.append(
        tableEl(
          [
            { key: "name", label: "名称" },
            { key: "status", label: "状态", format: (v) => badge(v, v === "active" ? "safe" : "muted") },
            {
              key: "market",
              label: "市场",
              format: (v, r) => `${v || "—"} ${r.instrument_class || ""}`.trim(),
            },
            { key: "source_category", label: "分类" },
            { key: "related_issue_id", label: "关联事项" },
            { key: "updated_at", label: "更新时间", format: (v) => fmtTime(v) },
          ],
          data.items,
        ),
      );
    } catch (err) {
      await renderState(providersHost, {
        kind: "error",
        title: "数据源加载失败",
        detail: err.message,
        retry: () => loadProviders(),
      });
    }
  }

  async function setMarket() {
    let options = [];
    try {
      const preview = await ctx.api.sessionMarket({ confirm: false });
      options = preview.options || [];
    } catch (err) {
      toastError(ctx.bus, err, "读取可选市场失败");
      return;
    }
    const body = { market: "", instrument_class: "" };
    if (options.length) {
      const values = await formDialog({
        title: "切换交易市场",
        submitLabel: "下一步",
        fields: [
          {
            key: "choice",
            label: "目标市场",
            kind: "select",
            required: true,
            options: options.map((o) => ({
              value: `${o.market}|${o.instrument_class}`,
              label: `${o.market} ${o.instrument_class}（规则 ${o.rule}，T+${o.t_plus}）`,
            })),
          },
        ],
      });
      if (!values) return;
      const [market, instrumentClass] = values.choice.split("|");
      body.market = market;
      body.instrument_class = instrumentClass;
    }
    try {
      const done = await runWithConfirm({
        title: "切换交易市场",
        confirmLabel: "确认切换",
        execute: (extra) => ctx.api.sessionMarket({ ...body, ...extra }),
      });
      if (!done) return;
      ctx.bus.emit("toast", {
        kind: "success",
        title: `已切换到 ${done.market} ${done.instrument_class}`,
        detail: `规则 ${done.rule} · 数据源 ${done.providers.length} 个 · 因子 ${done.factor_count} 个`,
      });
      await loadSession();
    } catch (err) {
      toastError(ctx.bus, err, "切换市场失败");
    }
  }

  async function setFee() {
    const values = await formDialog({
      title: "设置券商佣金费率",
      message: "输入小数形式，如万 0.8 = 0.00008。",
      submitLabel: "下一步",
      fields: [{ key: "rate", label: "费率", required: true, placeholder: "0.0003" }],
    });
    if (!values) return;
    try {
      const done = await runWithConfirm({
        title: "设置佣金费率",
        confirmLabel: "确认设置",
        execute: (extra) => ctx.api.sessionFee({ rate: values.rate, ...extra }),
      });
      if (!done) return;
      ctx.bus.emit("toast", { kind: "success", title: `佣金费率已设为 ${done.commission_rate}` });
      await loadSession();
    } catch (err) {
      toastError(ctx.bus, err, "设置费率失败");
    }
  }

  async function doctorInit() {
    try {
      const done = await runWithConfirm({
        title: "补齐工作区目录",
        confirmLabel: "确认补齐",
        execute: (extra) => ctx.api.doctorInit({ ...extra }),
      });
      if (!done) return;
      ctx.bus.emit("toast", {
        kind: "success",
        title: "目录补齐完成",
        detail: `新建目录 ${done.created_dirs.length} 个，补写 .gitkeep ${done.gitkeep_written} 个。`,
      });
    } catch (err) {
      toastError(ctx.bus, err, "目录补齐失败");
    }
  }

  async function memoryConfigure() {
    const values = await formDialog({
      title: "记忆服务配置",
      message: "保存指定版本，不升级依赖包。",
      submitLabel: "下一步",
      fields: [
        { key: "pinned_version", label: "固定版本", required: true, placeholder: "0.1.x" },
        { key: "package_name", label: "包名", value: "mem0ai" },
      ],
    });
    if (!values) return;
    try {
      const done = await runWithConfirm({
        title: "配置记忆服务",
        confirmLabel: "确认写入",
        execute: (extra) =>
          ctx.api.memoryConfigure({
            pinned_version: values.pinned_version,
            package_name: values.package_name || "mem0ai",
            ...extra,
          }),
      });
      if (!done) return;
      ctx.bus.emit("toast", { kind: "success", title: `记忆服务版本已固定为 ${done.pinned_version}` });
    } catch (err) {
      toastError(ctx.bus, err, "记忆服务配置失败");
    }
  }

  async function trashPurge() {
    const values = await formDialog({
      title: "清理回收站",
      message: "永久删除超过指定天数未恢复的条目，不可撤销。",
      submitLabel: "下一步",
      fields: [{ key: "days", label: "保留天数", kind: "number", required: true, value: "30" }],
    });
    if (!values) return;
    try {
      const done = await runWithConfirm({
        title: "永久清理回收站",
        confirmLabel: "确认清理",
        danger: true,
        execute: (extra) => ctx.api.trashPurgeOlderThan({ days: Number(values.days), ...extra }),
      });
      if (!done) return;
      ctx.bus.emit("toast", { kind: "success", title: `已清理 ${done.count} 条回收站条目` });
    } catch (err) {
      toastError(ctx.bus, err, "回收站清理失败");
    }
  }

}
