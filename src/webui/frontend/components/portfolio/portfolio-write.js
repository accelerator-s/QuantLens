import { runWithConfirm } from "../confirm-dialog/confirm-dialog.js";
import { formDialog } from "../confirm-dialog/form-dialog.js";
import { toastError } from "../../core/toast.js";
import { setupShareQuantity } from "./share-quantity.js";

export function initPortfolioWrite(root, ctx, { reloadAccounts, account, latestTradeId, setWriting }) {
  let pending = false;
  const guard = (operation) => async (...args) => {
    if (pending) return;
    pending = true;
    setWriting(true);
    try { await operation(...args); }
    finally { pending = false; setWriting(false); }
  };
  root.querySelector("[data-account-create]").addEventListener("click", guard(createAccount));
  root.querySelector("[data-holding-add]").addEventListener("click", guard(() => holdingSet()));
  root.querySelector("[data-trade-add]").addEventListener("click", guard(tradeAdd));
  root.querySelector("[data-trade-undo]").addEventListener("click", guard(undoTrade));

  async function quantityRule(current) {
    const { items } = await ctx.api.marketRules();
    const rules = items.filter((rule) => rule.market === current.market
      && rule.instrument_class === current.instrument_class && rule.status === "active");
    if (rules.length !== 1) throw new Error("未找到唯一启用的股票交易规则");
    return rules[0];
  }

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
      if (result) await reloadAccounts();
    } catch (err) { toastError(ctx.bus, err, "账户修改失败"); }
  }

  async function deleteAccount(current) {
    try {
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.accountDelete(current.account_id, extra),
        title: `删除账户 ${current.alias || current.account_id}`,
        showPreview: false,
        confirmLabel: "确认删除",
        danger: true,
      });
      if (!result) return;
      if (ctx.accountId === current.account_id) ctx.accountId = "";
      ctx.bus.emit("toast", { kind: "success", title: `已删除账户 ${current.alias || current.account_id}` });
      await reloadAccounts();
    } catch (err) {
      toastError(ctx.bus, err, "账户删除失败");
    }
  }

  async function cashSet(current) {
    try {
      const values = await formDialog({
        title: "调整现金",
        message: "替换账户现金余额，不改变持仓和成交记录。",
        submitLabel: "查看并确认",
        fields: [
          { key: "total", label: "现金余额（元）", kind: "number", min: "0", step: "0.01", required: true,
            value: current.cash?.find((cash) => cash.currency === "CNY")?.total || "0" },
        ],
      });
      if (!values) return;
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.accountCashSet(current.account_id, { ...values, ...extra }),
        title: "调整现金",
        confirmLabel: "确认设置",
      });
      if (!result) return;
      ctx.bus.emit("toast", { kind: "success", title: "现金余额已更新" });
      await reloadAccounts();
    } catch (err) {
      toastError(ctx.bus, err, "现金设置失败");
    }
  }

  async function holdingSet(holding) {
    const current = account();
    if (!current) return;
    try {
      const rule = await quantityRule(current);
      const values = await formDialog({
        title: holding ? "修改持仓" : "录入持仓",
        autoFocus: false,
        submitLabel: "查看并确认",
        setup: (controls) => setupShareQuantity(controls, rule, { holding: true }),
        fields: [
          { key: "instrument_id", label: "股票代码", kind: "stock", value: holding?.instrument_id || "", required: true, placeholder: "输入代码或名称" },
          { key: "qty", label: "持仓数量（股）", value: holding?.qty || "", kind: "number", min: "0", step: "1", required: true },
          { key: "cost_price", label: "成本价（元）", value: holding?.cost_price || "", kind: "number", min: "0", step: "0.0001", required: true },
        ],
      });
      if (!values) return;
      const result = await runWithConfirm({ execute: (extra) => ctx.api.holdingsSet(current.account_id, { ...values, ...extra }),
        title: "保存持仓", confirmLabel: "确认保存" });
      if (!result) return;
      ctx.bus.emit("toast", { kind: "success", title: "持仓已保存" });
      await reloadAccounts();
    } catch (error) { toastError(ctx.bus, error, "持仓录入失败"); }
  }

  async function tradeAdd() {
    const current = account();
    if (!current) return;
    try {
      const rule = await quantityRule(current);
      const values = await formDialog({
        title: "录入成交",
        submitLabel: "查看并确认",
        setup: (controls) => setupShareQuantity(controls, rule),
        fields: [
          { key: "action", label: "买卖", kind: "radio", value: "buy", options: [{ value: "buy", label: "买入" }, { value: "sell", label: "卖出" }] },
          { key: "instrument_id", label: "股票代码", kind: "stock", required: true, placeholder: "输入代码或名称" },
          { key: "qty", label: "成交数量（股）", kind: "number", min: "1", step: "1", required: true },
          { key: "price", label: "成交价（元）", kind: "number", min: "0.0001", step: "0.0001", required: true },
          { key: "executed_at", label: "成交时间（留空为当前时间）", kind: "datetime-local" },
          { key: "note", label: "备注", kind: "textarea", rows: 2 },
        ],
      });
      if (!values) return;
      const payload = { ...values, account: current.account_id,
        executed_at: values.executed_at ? new Date(values.executed_at).toISOString() : new Date().toISOString(), request_id: crypto.randomUUID() };
      const result = await runWithConfirm({ execute: (extra) => ctx.api.tradesAdd({ ...payload, ...extra }),
        title: "录入成交", confirmLabel: "确认录入" });
      if (!result) return;
      ctx.bus.emit("toast", { kind: "success", title: "成交已录入，持仓和余额已更新" });
      await reloadAccounts();
    } catch (error) { toastError(ctx.bus, error, "成交录入失败"); }
  }

  async function undoTrade() {
    const current = account();
    const tradeId = latestTradeId();
    if (!current || !tradeId) return;
    try {
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.tradeUndo(current.account_id, { trade_id: tradeId, ...extra }),
        title: "撤回上一笔交易", confirmLabel: "确认撤回", danger: true,
      });
      if (!result) return;
      ctx.bus.emit("toast", { kind: "success", title: "交易已撤回，持仓和余额已恢复" });
      await reloadAccounts();
    } catch (error) { toastError(ctx.bus, error, "交易撤回失败"); }
  }

  return { editAccount: guard(editAccount), deleteAccount: guard(deleteAccount), cashSet: guard(cashSet), holdingSet: guard(holdingSet) };
}
