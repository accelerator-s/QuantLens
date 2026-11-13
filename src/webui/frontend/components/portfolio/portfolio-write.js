import { labelText } from "../../core/labels.js";
import { runWithConfirm } from "../confirm-dialog/confirm-dialog.js";
import { formDialog } from "../confirm-dialog/form-dialog.js";
import { toastError } from "../../core/toast.js";

const PLAN_STATES = ["active", "executed", "stale_for_execution", "archived", "cancelled"];


export function initPortfolioWrite(root, ctx, helpers) {
  const { account, reloadAccounts, reload } = helpers;

  root.querySelector("[data-account-create]").addEventListener("click", () => createAccount());
  root.querySelector("[data-trade-add]").addEventListener("click", () => tradeAdd());
  root.querySelector("[data-trades-append]").addEventListener("click", () => tradesAppend());
  root.querySelector("[data-trades-fix]").addEventListener("click", () => tradesFix());
  root.querySelector("[data-snapshot-commit]").addEventListener("click", () => snapshotCommit());
  root.querySelector("[data-snapshot-submit]").addEventListener("click", () => snapshotSubmit());
  root.querySelector("[data-plan-submit]").addEventListener("click", () => planSubmit());
  root
    .querySelector("[data-recommend-generate]")
    .addEventListener("click", () => recommendGenerate());

  async function createAccount() {
    try {
      const values = await formDialog({
        title: "创建账户",
        submitLabel: "查看并确认",
        fields: [
          { key: "alias", label: "账户名称", required: true },
          { key: "broker", label: "券商" },
          { key: "notes", label: "备注" },
          { key: "star_market_enabled", label: "科创板权限", kind: "checkbox", value: false },
        ],
      });
      if (!values) return;
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.accountCreate({ ...values, ...extra }),
        title: "创建账户",
        confirmLabel: "确认创建",
      });
      if (!result) return;
      ctx.bus.emit("toast", { kind: "success", title: `已创建账户 ${values.alias}` });
      await reloadAccounts();
    } catch (err) {
      toastError(ctx.bus, err, "账户创建失败");
    }
  }

  async function editAccount(current) {
    try {
      const values = await formDialog({
        title: "修改账户",
        submitLabel: "查看并确认",
        fields: [
          { key: "alias", label: "账户名称", required: true, value: current.alias },
          { key: "broker", label: "券商", value: current.broker },
          { key: "notes", label: "备注", value: current.notes },
          { key: "star_market_enabled", label: "科创板权限", kind: "checkbox", value: current.star_market_enabled },
        ],
      });
      if (!values) return;
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.accountSettings(current.account_id, { ...values, ...extra }),
        title: "修改账户", confirmLabel: "保存",
      });
      if (result) { await reloadAccounts(); await reload("plans"); }
    } catch (err) { toastError(ctx.bus, err, "账户修改失败"); }
  }

  async function archiveAccount(accountId) {
    try {
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.accountArchive(accountId, extra),
        title: `归档账户 ${accountId}`,
        confirmLabel: "确认归档",
        danger: true,
      });
      if (!result) return;
      ctx.bus.emit("toast", { kind: "success", title: `已归档账户 ${accountId}` });
      await reloadAccounts();
    } catch (err) {
      toastError(ctx.bus, err, "账户归档失败");
    }
  }

  async function reinitAccount(accountId) {
    try {
      const values = await formDialog({
        title: `重新初始化 ${accountId}`,
        message: "清空当前持仓，对账从重置时间重新开始。",
        submitLabel: "查看并确认",
        fields: [{ key: "reason", label: "重置原因", kind: "textarea", rows: 3, required: true }],
      });
      if (!values) return;
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.portfolioReinit(accountId, { reason: values.reason, ...extra }),
        title: `重新初始化 ${accountId}`,
        confirmLabel: "确认重置",
        danger: true,
      });
      if (!result) return;
      ctx.bus.emit("toast", { kind: "success", title: `账户 ${accountId} 已重置` });
      await reloadAccounts();
    } catch (err) {
      toastError(ctx.bus, err, "账户重置失败");
    }
  }

  async function cashSet(accountId, currency) {
    try {
      const values = await formDialog({
        title: `设置现金 ${accountId}`,
        message: "覆盖该币种的现金记录。可用金额默认等于总额，可取金额默认等于可用金额。",
        submitLabel: "查看并确认",
        fields: [
          { key: "currency", label: "币种", value: currency || "" },
          { key: "total", label: "总额", required: true },
          { key: "available", label: "可用（留空 = 总额）" },
          { key: "withdrawable", label: "可取（留空 = 可用）" },
          { key: "frozen", label: "冻结", value: "0" },
        ],
      });
      if (!values) return;
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.accountCashSet(accountId, { ...values, ...extra }),
        title: `设置现金 ${accountId}`,
        confirmLabel: "确认设置",
      });
      if (!result) return;
      ctx.bus.emit("toast", { kind: "success", title: `账户 ${accountId} 现金已更新` });
      await reloadAccounts();
    } catch (err) {
      toastError(ctx.bus, err, "现金设置失败");
    }
  }

  async function tradeAdd() {
    try {
      const values = await formDialog({
        title: "录入成交",
        message: "按成交时间顺序录入，同步更新持仓与现金。费用和税费以实际交割单为准。",
        submitLabel: "查看并确认",
        fields: [
          { key: "instrument_id", label: "证券代码", required: true },
          { key: "side", label: "方向", kind: "select", value:"long", options: [{value:"long",label:"多头"}] },
          {
            key: "action",
            label: "动作",
            kind: "select",
            value:"open", options: [{value:"open",label:"买入建仓"},{value:"increase",label:"加仓"},{value:"decrease",label:"减仓"},{value:"close",label:"卖出清仓"}],
          },
          { key: "qty", label: "数量", required: true },
          { key: "price", label: "价格", required: true },
          { key: "fee", label: "费用", value: "0" },
          { key: "tax", label: "税", value: "0" },
          { key: "other_cost", label: "其他成本", value: "0" },
          { key: "currency", label: "币种（留空取报告币种）" },
          {
            key: "executed_at",
            label: "成交时间（留空为当前）",
            placeholder: "2026-08-04T10:30:00+08:00",
          },
          { key: "related_plan_id", label: "关联计划编号（可空）" },
          { key: "note", label: "备注" },
        ],
      });
      if (!values) return;
      values.request_id=crypto.randomUUID();
      values.executed_at=values.executed_at||new Date().toISOString();
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.tradesAdd({ account: account(), ...values, ...extra }),
        title: "录入成交",
        confirmLabel: "确认录入",
      });
      if (!result) return;
      ctx.bus.emit("toast", { kind: "success", title: `成交已录入：${result.trade_id}` });
      await reloadAccounts();
      await reload("trades");
    } catch (err) {
      toastError(ctx.bus, err, "成交录入失败");
    }
  }

  async function tradesAppend() {
    try {
      const values = await formDialog({
        title: "批量追加成交",
        message: "仅追加成交记录（导入 / 修复用途），持仓表不会同步更新。",
        submitLabel: "查看并确认",
        fields: [
          { key: "yaml_text", label: "成交列表 YAML", kind: "textarea", rows: 10, required: true },
        ],
      });
      if (!values) return;
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.tradesAppend({ yaml_text: values.yaml_text, ...extra }),
        title: "批量追加成交",
        confirmLabel: "确认追加",
      });
      if (!result) return;
      ctx.bus.emit("toast", { kind: "success", title: `已追加 ${result.total} 条成交` });
      await reloadAccounts();
      await reload("trades");
    } catch (err) {
      toastError(ctx.bus, err, "成交追加失败");
    }
  }

  async function tradesFix() {
    try {
      const values = await formDialog({
        title: "成交审计补丁",
        message: "保留原始成交，另存更正记录。勾选“确认执行更正”后提交。",
        submitLabel: "查看并确认",
        fields: [
          { key: "trade_id", label: "成交编号", required: true },
          {
            key: "yaml_text",
            label: "补丁 YAML（须含 account_id）",
            kind: "textarea",
            rows: 6,
            required: true,
          },
          { key: "force", label: "确认执行更正", kind: "checkbox" },
        ],
      });
      if (!values) return;
      const result = await runWithConfirm({
        execute: (extra) =>
          ctx.api.tradesFix({
            trade_id: values.trade_id,
            yaml_text: values.yaml_text,
            force: values.force,
            ...extra,
          }),
        title: "写入审计补丁",
        confirmLabel: "确认写入",
        danger: true,
      });
      if (!result) return;
      ctx.bus.emit("toast", { kind: "success", title: `已写入审计补丁 ${values.trade_id}` });
      await reloadAccounts();
      await reload("trades");
    } catch (err) {
      toastError(ctx.bus, err, "审计补丁写入失败");
    }
  }

  async function snapshotCommit() {
    try {
      const first = await formDialog({
        title: "生成快照",
        message: "选择“当前持仓”生成新快照，或沿用上次快照并更新市值。",
        submitLabel: "下一步",
        fields: [
          { key: "mode", label: "模式", kind: "select", options: ["detailed", "unchanged"] },
          { key: "note", label: "备注" },
        ],
      });
      if (!first) return;
      const base = { account: account(), mode: first.mode, note: first.note };
      
      const preview = await ctx.api.snapshotCommit({ ...base, confirm: false });
      let prices = {};
      const baseRows = preview.base_rows || [];
      if (baseRows.length) {
        const priceForm = await formDialog({
          title: "填写现价",
          message: "为每个持仓填写当前价格（正数）。",
          submitLabel: "查看并确认",
          fields: baseRows.map((row) => ({
            key: row.instrument_id,
            label: `${row.instrument_id} 现价（${labelText(row.currency)}）`,
            value: row.price || "",
            required: true,
          })),
        });
        if (!priceForm) return;
        prices = priceForm;
      }
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.snapshotCommit({ ...base, prices, ...extra }),
        title: "提交快照",
        confirmLabel: "确认提交",
      });
      if (!result) return;
      ctx.bus.emit("toast", {
        kind: "success",
        title: `快照已提交：${result.snapshot_id}`,
        detail: `总资产 ${result.total_assets}，浮动盈亏 ${result.floating_pnl}`,
      });
      await reload("snapshots");
    } catch (err) {
      toastError(ctx.bus, err, "快照生成失败");
    }
  }

  async function snapshotSubmit() {
    try {
      const values = await formDialog({
        title: "提交快照 YAML",
        submitLabel: "查看并确认",
        fields: [
          { key: "yaml_text", label: "快照 YAML", kind: "textarea", rows: 12, required: true },
          { key: "mode", label: "模式", kind: "select", options: ["detailed", "unchanged"] },
        ],
      });
      if (!values) return;
      const result = await runWithConfirm({
        execute: (extra) =>
          ctx.api.snapshotSubmit({ yaml_text: values.yaml_text, mode: values.mode, ...extra }),
        title: "提交快照",
        confirmLabel: "确认提交",
      });
      if (!result) return;
      ctx.bus.emit("toast", { kind: "success", title: `快照已提交：${result.snapshot_id}` });
      await reload("snapshots");
    } catch (err) {
      toastError(ctx.bus, err, "快照提交失败");
    }
  }

  async function planSubmit() {
    try {
      const values = await formDialog({
        title: "提交调仓计划",
        submitLabel: "查看并确认",
        fields: [
          { key: "yaml_text", label: "计划 YAML", kind: "textarea", rows: 12, required: true },
          { key: "primary_account", label: "主账户", value: account(), required: true },
        ],
      });
      if (!values) return;
      const result = await runWithConfirm({
        execute: (extra) =>
          ctx.api.planSubmit({
            yaml_text: values.yaml_text,
            primary_account: values.primary_account,
            ...extra,
          }),
        title: "提交调仓计划",
        confirmLabel: "确认提交",
      });
      if (!result) return;
      ctx.bus.emit("toast", { kind: "success", title: `计划已提交：${result.plan_id}` });
      await reload("plans");
    } catch (err) {
      toastError(ctx.bus, err, "计划提交失败");
    }
  }

  async function recommendGenerate() {
    try {
      const accountId = account();
      if (!accountId) return;
      const [accData, stratData] = await Promise.all([ctx.api.accounts(), ctx.api.strategies()]);
      const acc = (accData.items || []).find((a) => a.account_id === accountId);
      const candidates = (stratData.items || []).filter(
        (s) => s.status === "active" && (!acc || !s.market || s.market === acc.market),
      );
      if (!candidates.length) {
        toastError(ctx.bus, new Error("没有适用于该账户的已启用策略"), "无法生成建议");
        return;
      }
      const values = await formDialog({
        title: `生成调仓建议（${acc?.alias || accountId}）`,
        message: `按该账户的持仓、可用资金和权限生成计划。科创板权限：${acc?.star_market_enabled ? "已开通" : "未开通，排除科创板买入"}。同策略旧计划将失效，并生成模型解读。`,
        submitLabel: "生成",
        fields: [
          {
            key: "profile",
            label: "策略",
            kind: "select",
            options: candidates.map((s) => s.profile_name),
          },
          { key: "as_of", label: "信号日（留空 = 面板末日）", placeholder: "2026-11-05" },
        ],
      });
      if (!values) return;
      ctx.bus.emit("toast", { kind: "info", title: "正在生成建议（计算因子信号）…" });
      const result = await ctx.api.recommendGenerate({
        account: accountId,
        profile: values.profile,
        as_of: values.as_of || "",
      });
      ctx.bus.emit("toast", { kind: "success", title: `计划已生成：${result.plan_id}` });
      if (!result.ai_ok) {
        ctx.bus.emit("toast", {
          kind: "error",
          title: "模型解读失败（计划已保存）",
          detail: `${result.ai_error} 可在计划卡点击「重新解读」重试。`,
          timeout: 8000,
        });
      }
      await reload("plans");
    } catch (err) {
      toastError(ctx.bus, err, "建议生成失败");
    }
  }

  async function planReview(planId) {
    try {
      ctx.bus.emit("toast", { kind: "info", title: `正在重新解读：${planId}` });
      await ctx.api.planReview(planId, { account: account() });
      ctx.bus.emit("toast", { kind: "success", title: `模型解读完成：${planId}` });
      await reload("plans");
    } catch (err) {
      toastError(ctx.bus, err, "模型解读失败");
    }
  }

  
  async function planCompare(planId) {
    try {
      const values = await formDialog({
        title: `资金规模对比 ${planId}`,
        message: "用参考资金和账户资金分别回测，比较执行差异，不预测收益。",
        submitLabel: "开始对比",
        fields: [
          { key: "reference_capital", label: "参考资金（元）", value: "1000000", required: true },
        ],
      });
      if (!values) return null;
      ctx.bus.emit("toast", { kind: "info", title: "资金对比运行中（两次回测，耗时较长）…" });
      const payload = await ctx.api.planCompareRun(planId, {
        account: account(),
        reference_capital: values.reference_capital,
      });
      ctx.bus.emit("toast", { kind: "success", title: `资金对比完成：${planId}` });
      return payload;
    } catch (err) {
      toastError(ctx.bus, err, "资金对比失败");
      return null;
    }
  }

  async function planTransition(planId) {
    try {
      const values = await formDialog({
        title: `修改计划状态 ${planId}`,
        submitLabel: "查看并确认",
        fields: [
          { key: "state", label: "目标状态", kind: "select", options: PLAN_STATES },
          { key: "primary_account", label: "主账户", value: account(), required: true },
        ],
      });
      if (!values) return;
      const result = await runWithConfirm({
        execute: (extra) =>
          ctx.api.planTransition(planId, {
            primary_account: values.primary_account,
            state: values.state,
            ...extra,
          }),
        title: `修改计划状态 ${planId}`,
        confirmLabel: "确认修改",
      });
      if (!result) return;
      ctx.bus.emit("toast", { kind: "success", title: `计划 ${planId} → ${labelText(values.state)}` });
      await reload("plans");
    } catch (err) {
      toastError(ctx.bus, err, "修改计划状态失败");
    }
  }

  return { editAccount, archiveAccount, reinitAccount, planTransition, cashSet, planReview, planCompare };
}
