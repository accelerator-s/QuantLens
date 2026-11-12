import {labelText} from "../../core/labels.js";
import { renderState } from "../state-card/state-card.js";
import { runWithConfirm } from "../confirm-dialog/confirm-dialog.js";
import { formDialog } from "../confirm-dialog/form-dialog.js";
import { initPortfolioWrite } from "./portfolio-write.js";
import { hydrateIcons } from "../../core/icons.js";
import { badge, fmtDate, fmtNum, fmtPct, fmtTime, signClass } from "../../core/fmt.js";
import { codeEl, el, kvEl, selectMarkup, tableEl } from "../../core/dom.js";
import { createSelect } from "../../core/select.js";
import { lineChart } from "../../core/charts.js";
import { toastError } from "../../core/toast.js";

const TRADES_PAGE = 100;

const PLAN_STATE_LABEL = {
  active: ["进行中", "safe"],
  executed: ["已执行", "accent"],
  stale_for_execution: ["已过期待处理", "warn"],
  archived: ["已归档", "muted"],
  cancelled: ["已取消", "muted"],
};

const PLAN_ACTION_LABEL = { open: "建仓", increase: "加仓", decrease: "减仓", close: "清仓" };

const COMPARE_METRIC_LABELS = [
  ["returns.total_return", "累计收益"],
  ["returns.annualized_return", "年化收益"],
  ["returns.annualized_excess_return", "年化超额收益"],
  ["risk.sharpe", "夏普比率"],
  ["risk.max_drawdown", "最大回撤"],
  ["risk.annualized_volatility", "年化波动率"],
  ["trading.annualized_turnover_oneside", "年化单边换手"],
  ["trading.annualized_cost_drag", "年化成本拖累"],
  ["trading.total_cost_paid", "总成本支出"],
  ["trading.avg_holdings", "平均持仓数"],
  ["orders.lot_unaffordable_count", "资金不足一手次数"],
  ["orders.lot_unaffordable_amount_ratio", "不足一手的订单金额占比"],
];


export async function mount(root, ctx) {
  await hydrateIcons(root);

  const accountMeta = root.querySelector("[data-account-meta]");
  const accountCard = root.querySelector("[data-account-card]");
  const emptyHost = root.querySelector("[data-empty]");
  const tabsBar = root.querySelector("[data-tabs]");
  const tabs = root.querySelectorAll("[data-tab]");
  const panels = root.querySelectorAll("[data-panel]");
  const hosts = {
    holdings: root.querySelector("[data-holdings]"),
    trades: root.querySelector("[data-trades]"),
    snapshots: root.querySelector("[data-snapshots]"),
    plans: root.querySelector("[data-plans]"),
    reconcile: root.querySelector("[data-reconcile]"),
  };
  const tradesMoreBtn = root.querySelector("[data-trades-more]");

  const writeOps = initPortfolioWrite(root, ctx, {
    account: () => currentAccount,
    reloadAccounts: () => loadAccounts(true),
    reload: async () => {
      loaded = new Set();
      const active = root.querySelector("[data-tab].is-active");
      if (active) await activate(active.dataset.tab);
    },
  });

  let currentAccount = "";
  let accounts = [];
  let loaded = new Set();
  let tradesOffset = 0;
  let tradesTotal = 0;
  let tradesRows = [];

  const accountSelectHost = root.querySelector("[data-account-select]");
  accountSelectHost.innerHTML = selectMarkup("选择账户");
  const accountSelect = createSelect(accountSelectHost, {
    emptyLabel: "（无账户）",
    onChange: (id) => {
      currentAccount = id;
      resetAndLoad();
    },
  });

  for (const btn of tabs) {
    btn.addEventListener("click", () => {
      tabs.forEach((b) => b.classList.toggle("is-active", b === btn));
      panels.forEach((p) => {
        p.hidden = p.dataset.panel !== btn.dataset.tab;
      });
      activate(btn.dataset.tab);
    });
  }

  tradesMoreBtn.addEventListener("click", () => loadTrades(true));
  root.querySelector("[data-daily-run]").addEventListener("click", () => runDailyOperations());
  root.querySelector("[data-automation-config]").addEventListener("click", () => configureAutomation());
  root.querySelector("[data-shadow-observe]").addEventListener("click", () => observeShadows());
  root.querySelector("[data-attribution-mature]").addEventListener("click", () => matureAttributions());
  root.querySelector("[data-execution-recovery]").addEventListener("click", () => recoverExecutions());
  root.querySelector("[data-decay-scan]").addEventListener("click", () => scanDecay());

  ctx.bus.on("route", (id) => {
    if (id === "portfolio") loadAccounts(true);
  });
  await loadAccounts(false);

  async function loadAccounts(force) {
    try {
      const data = await ctx.api.accounts();
      accounts = data.items || [];
      if (!accounts.length) {
        tabsBar.hidden = true;
        panels.forEach((p) => {
          p.hidden = true;
        });
        accountCard.innerHTML = "";
        accountMeta.textContent = "";
        emptyHost.hidden = false;
        renderState(emptyHost, {
          kind: "empty",
          title: "尚未添加账户",
          detail: "点击上方「创建账户」注册首个账户。",
        });
        return;
      }
      emptyHost.hidden = true;
      tabsBar.hidden = false;
      const options = accounts.map((a) => ({
        value: a.account_id,
        label: a.alias ? `${a.account_id}（${a.alias}）` : a.account_id,
      }));
      accountSelect.setOptions(options, currentAccount);
      if (force || !currentAccount || !accounts.some((a) => a.account_id === currentAccount)) {
        currentAccount = accountSelect.value;
        resetAndLoad();
      }
    } catch (err) {
      emptyHost.hidden = false;
      renderState(emptyHost, {
        kind: "error",
        title: "账户列表加载失败",
        detail: err.message,
        retry: () => loadAccounts(force),
      });
    }
  }

  function resetAndLoad() {
    loaded = new Set();
    tradesOffset = 0;
    tradesRows = [];
    renderAccountCard();
    const active = root.querySelector("[data-tab].is-active");
    panels.forEach((p) => {
      p.hidden = p.dataset.panel !== active.dataset.tab;
    });
    activate(active.dataset.tab);
  }

  function renderAccountCard() {
    const account = accounts.find((a) => a.account_id === currentAccount);
    accountCard.innerHTML = "";
    if (!account) return;
    accountMeta.textContent = `${labelText(account.market)} / ${labelText(account.instrument_class)}`;
    const card = el("article", "card detail-card");
    const head = el("div", "detail-card__head");
    head.append(el("h3", "", account.account_id));
    if (account.alias) head.append(el("span", "item-row__meta", account.alias));
    head.append(badge(account.market, "accent"), badge(account.instrument_class, "muted"));
    card.append(head);
    const cashNode = el("span", "tag-badges");
    for (const c of account.cash || []) {
      cashNode.append(badge(`${labelText(c.currency)} ${fmtNum(c.total)}（可用 ${fmtNum(c.available)}）`, "accent"));
    }
    if (!(account.cash || []).length) cashNode.textContent = "—";
    card.append(
      kvEl([
        ["记账币种", account.reporting_currency],
        ["现金", cashNode],
        ["券商", account.broker],
        ["重置次数", String(account.reinit_count ?? 0)],
        ["创建时间", fmtTime(account.created_at)],
        ["备注", account.notes],
      ]),
    );
    const actions = el("div", "detail-card__head");
    const cashBtn = el("button", "btn btn--ghost", "调整现金");
    cashBtn.type = "button";
    cashBtn.addEventListener("click", () =>
      writeOps.cashSet(account.account_id, account.reporting_currency),
    );
    actions.append(cashBtn);
    const archiveBtn = el("button", "btn btn--ghost", "归档账户");
    archiveBtn.type = "button";
    archiveBtn.addEventListener("click", () => writeOps.archiveAccount(account.account_id));
    const reinitBtn = el("button", "btn btn--danger", "重新初始化");
    reinitBtn.type = "button";
    reinitBtn.addEventListener("click", () => writeOps.reinitAccount(account.account_id));
    actions.append(archiveBtn, reinitBtn);
    card.append(actions);
    accountCard.append(card);
  }

  async function activate(tab) {
    if (!currentAccount || loaded.has(tab)) return;
    loaded.add(tab);
    if (tab === "holdings") await loadHoldings();
    if (tab === "trades") await loadTrades(false);
    if (tab === "snapshots") await loadSnapshots();
    if (tab === "plans") await loadPlans();
    if (tab === "reconcile") await loadReconcile();
  }

  async function loadHoldings() {
    await renderState(hosts.holdings, { kind: "loading", title: "加载持仓…" });
    try {
      const data = await ctx.api.holdings(currentAccount);
      hosts.holdings.innerHTML = "";
      if (!data.items.length) {
        renderState(hosts.holdings, {
          kind: "empty",
          title: "当前无持仓",
          detail: "在成交页签录入成交后自动同步持仓。",
        });
        return;
      }
      hosts.holdings.append(
        tableEl(
          [
            { key: "instrument_id", label: "标的" },
            { key: "side", label: "方向", format: (v) => badge(v, "muted") },
            { key: "qty", label: "数量", num: true, format: (v) => fmtNum(v, 0) },
            { key: "available_qty", label: "可用", num: true, format: (v) => fmtNum(v, 0) },
            { key: "cost_price", label: "成本价", num: true, format: (v) => fmtNum(v, 4) },
            { key: "currency", label: "币种" },
            { key: "as_of", label: "截至" },
          ],
          data.items,
        ),
      );
    } catch (err) {
      await renderState(hosts.holdings, {
        kind: "error",
        title: "持仓加载失败",
        detail: err.message,
        retry: () => loadHoldings(),
      });
    }
  }

  async function loadTrades(more) {
    if (!more) {
      tradesOffset = 0;
      tradesRows = [];
      await renderState(hosts.trades, { kind: "loading", title: "加载成交…" });
    }
    tradesMoreBtn.disabled = true;
    try {
      const data = await ctx.api.trades(currentAccount, {
        limit: TRADES_PAGE,
        offset: tradesOffset,
      });
      tradesTotal = data.total_rows;
      tradesRows = tradesRows.concat(data.items);
      tradesOffset = tradesRows.length;
      hosts.trades.innerHTML = "";
      if (!tradesRows.length) {
        renderState(hosts.trades, {
          kind: "empty",
          title: "暂无成交记录",
          detail: "点击上方「录入成交」添加首笔记录。",
        });
        tradesMoreBtn.hidden = true;
        return;
      }
      hosts.trades.append(
        tableEl(
          [
            { key: "executed_at", label: "成交时间", format: (v) => fmtTime(v) },
            { key: "code", label: "代码" },
            { key: "action", label: "操作", format: (v) => badge(v, "muted") },
            { key: "qty", label: "数量", num: true, format: (v) => fmtNum(v, 0) },
            { key: "price", label: "价格", num: true, format: (v) => fmtNum(v, 4) },
            { key: "fee", label: "费用", num: true, format: (v) => fmtNum(v) },
            { key: "tax", label: "税", num: true, format: (v) => fmtNum(v) },
            { key: "status", label: "状态", format: (v) => badge(v) },
            { key: "reason", label: "原因" },
          ],
          tradesRows,
        ),
      );
      hosts.trades.append(
        el("p", "item-row__meta", `已显示 ${tradesRows.length} / ${tradesTotal} 条`),
      );
      tradesMoreBtn.hidden = tradesRows.length >= tradesTotal;
    } catch (err) {
      await renderState(hosts.trades, {
        kind: "error",
        title: "成交加载失败",
        detail: err.message,
        retry: () => loadTrades(more),
      });
    } finally {
      tradesMoreBtn.disabled = false;
    }
  }

  async function loadSnapshots() {
    await renderState(hosts.snapshots, { kind: "loading", title: "加载快照…" });
    try {
      const data = await ctx.api.portfolioSnapshots(currentAccount);
      hosts.snapshots.innerHTML = "";
      if (!data.items.length) {
        renderState(hosts.snapshots, {
          kind: "empty",
          title: "暂无组合快照",
          detail: "点击上方「生成快照」创建首份快照。",
        });
        return;
      }
      for (const snap of data.items) {
        const card = el("article", "card detail-card");
        const head = el("div", "detail-card__head");
        head.append(el("h3", "", snap.file));
        card.append(head);
        card.append(codeEl(JSON.stringify(snap, null, 2)));
        hosts.snapshots.append(card);
      }
    } catch (err) {
      await renderState(hosts.snapshots, {
        kind: "error",
        title: "快照加载失败",
        detail: err.message,
        retry: () => loadSnapshots(),
      });
    }
  }

  async function loadPlans() {
    await renderState(hosts.plans, { kind: "loading", title: "加载计划…" });
    try {
      const [data, canonical] = await Promise.all([
        ctx.api.plans(currentAccount),
        ctx.api.canonicalPlans({ account_id: currentAccount }),
      ]);
      hosts.plans.innerHTML = "";
      if (!data.items.length && !(canonical.items || []).length) {
        renderState(hosts.plans, {
          kind: "empty",
          title: "暂无交易计划",
          detail: "可按已启用策略生成建议，也可手动提交计划。",
        });
        return;
      }
      for (const item of canonical.items || []) {
        hosts.plans.append(canonicalPlanCard(item));
      }
      for (const plan of data.items) {
        hosts.plans.append(planCard(plan));
      }
    } catch (err) {
      await renderState(hosts.plans, {
        kind: "error",
        title: "计划加载失败",
        detail: err.message,
        retry: () => loadPlans(),
      });
    }
  }

  function canonicalPlanCard(item) {
    const plan = item.portfolio_plan;
    const card = el("article", "card detail-card");
    const head = el("div", "detail-card__head");
    head.append(
      el("h3", "", plan.portfolio_plan_id),
      badge(item.state),
      badge(plan.constraint_adjustments?.strategy_state || "未知", "accent"),
    );
    if (item.state === "proposed") {
      if (plan.constraint_adjustments?.strategy_state === "champion") {
        const confirm = el("button", "btn btn--primary", "确认真实调仓");
        confirm.type = "button";
        confirm.addEventListener("click", () => confirmCanonicalPlan(plan.portfolio_plan_id, false));
        head.append(confirm);
      }
      const reject = el("button", "btn btn--ghost", "拒绝");
      reject.type = "button";
      reject.addEventListener("click", () => confirmCanonicalPlan(plan.portfolio_plan_id, true));
      head.append(reject);
    }
    card.append(
      head,
      kvEl([
        ["已保存的策略版本", plan.strategy_version_id],
        ["数据快照", plan.data_snapshot_id],
        ["交易日", plan.trading_date],
        ["执行窗口", plan.execution_window],
        ["目标持仓数", String((plan.targets || []).length)],
        ["建议交易数", String((plan.proposed_orders || []).length)],
      ]),
    );
    if ((plan.proposed_orders || []).length) {
      card.append(
        tableEl(
          [
            { key: "instrument_id", label: "标的" },
            { key: "side", label: "方向", format: (v) => badge(v) },
            { key: "quantity", label: "数量", num: true, format: (v) => fmtNum(v, 0) },
            { key: "reference_price", label: "参考价", num: true, format: (v) => fmtNum(v, 4) },
            { key: "estimated_cost", label: "预估成本", num: true, format: (v) => fmtNum(v) },
            { key: "reason", label: "原因" },
          ],
          plan.proposed_orders,
        ),
      );
    }
    return card;
  }

  async function runDailyOperations() {
    try {
      const result = await ctx.api.dailyOperationsRun({ account_ids: [currentAccount], as_of: "" });
      const recoveryErrors = result.execution_recovery?.errors || [];
      const errors = [...(result.errors || []), ...recoveryErrors];
      ctx.bus.emit("toast", {
        kind: errors.length ? "warn" : "success",
        title: `每日策略已生成 ${result.plans?.length || 0} 份计划`,
        detail: errors.map((item) => `[${item.code}] ${item.message}`).join("\n"),
      });
      loaded.delete("plans");
      await loadPlans();
    } catch (err) {
      toastError(ctx.bus, err, "每日策略运行失败");
    }
  }

  async function configureAutomation() {
    try {
      const current = (await ctx.api.automation()).config;
      const values = await formDialog({
        title: "每日策略调度",
        message: "定时更新数据、生成正式与候选策略计划，补齐观察结果和收益归因，并检查表现衰减。不自动下单。",
        submitLabel: "查看并保存",
        fields: [
          { key: "enabled", label: "启用", kind: "checkbox", value: current.enabled },
          { key: "daily_time", label: "每日时间（HH:MM）", value: current.daily_time, required: true },
          { key: "timezone", label: "时区", value: current.timezone, required: true },
          { key: "current_account_only", label: `仅运行当前账户 ${currentAccount}`, kind: "checkbox", value: (current.account_ids || []).length > 0 },
          { key: "sync_data", label: "先更新数据和计算缓存", kind: "checkbox", value: current.sync_data },
        ],
      });
      if (!values) return;
      const config = {
        ...current,
        enabled: values.enabled,
        daily_time: values.daily_time,
        timezone: values.timezone,
        account_ids: values.current_account_only ? [currentAccount] : [],
        sync_data: values.sync_data,
      };
      await runWithConfirm({
        execute: (p) => ctx.api.automationSave({ confirm: p.confirm, config }),
        title: "保存每日策略调度",
        confirmLabel: "确认保存",
      });
      ctx.bus.emit("toast", { kind: "success", title: values.enabled ? "每日调度已启用" : "每日调度已停用" });
    } catch (err) {
      toastError(ctx.bus, err, "每日调度配置失败");
    }
  }

  async function observeShadows() {
    try {
      const snapshots = await ctx.api.dataSnapshots();
      const asOf = snapshots.items?.[0]?.snapshot?.as_of?.slice(0, 10);
      if (!asOf) throw new Error("暂无数据快照，请先运行每日策略任务。");
      const result = await ctx.api.shadowObserve({ as_of: asOf });
      ctx.bus.emit("toast", { kind: "success", title: `已补写 ${result.items?.length || 0} 条模拟观察结果` });
    } catch (err) {
      toastError(ctx.bus, err, "影子结果补写失败");
    }
  }

  async function matureAttributions() {
    try {
      const snapshots = await ctx.api.dataSnapshots();
      const asOf = snapshots.items?.[0]?.snapshot?.as_of?.slice(0, 10);
      if (!asOf) throw new Error("暂无数据快照，无法确定收益观察截止日。");
      const result = await ctx.api.attributionMature({ as_of: asOf });
      ctx.bus.emit("toast", { kind: "success", title: `已生成 ${result.items?.length || 0} 条实盘归因` });
    } catch (err) {
      toastError(ctx.bus, err, "到期归因失败");
    }
  }

  async function recoverExecutions() {
    try {
      const result = await ctx.api.executionRecovery({ account_ids: [currentAccount], as_of: "" });
      const errors = result.errors || [];
      ctx.bus.emit("toast", {
        kind: errors.length ? "warn" : "success",
        title: `已补登记 ${result.recovered?.length || 0} 条成交关联记录`,
        detail: errors.map((item) => `${item.trade_id}: [${item.code}] ${item.message}`).join("\n"),
      });
      loaded.delete("reconcile");
    } catch (err) {
      toastError(ctx.bus, err, "成交关联补齐失败");
    }
  }

  async function scanDecay() {
    try {
      const result = await ctx.api.decayScan();
      const failed = result.trigger_errors || [];
      const triggered = result.triggered_research || [];
      ctx.bus.emit("toast", {
        kind: failed.length ? "error" : result.items?.length ? "warn" : "success",
        title: result.items?.length
          ? `发现 ${result.items.length} 条衰减/执行偏差告警`
          : triggered.length
            ? `已重试并触发 ${triggered.length} 个研究任务`
            : "未发现连续衰减",
        detail: failed.map((item) => `${item.learning_event_id}: ${item.message}`).join("\n"),
      });
      loaded.delete("reconcile");
    } catch (err) {
      toastError(ctx.bus, err, "衰减扫描失败");
    }
  }

  async function confirmCanonicalPlan(planId, reject) {
    try {
      await runWithConfirm({
        execute: (p) => {
          const payload = { confirm: p.confirm, reason: p.note || "", decided_by: p.decided_by || "" };
          return reject
            ? ctx.api.canonicalPlanReject(planId, payload)
            : ctx.api.canonicalPlanConfirm(planId, payload);
        },
        title: reject ? "拒绝调仓计划" : "确认真实调仓计划",
        requireDecidedBy: true,
        notePlaceholder: "记录人工决策理由",
        confirmLabel: reject ? "确认拒绝" : "确认进入执行窗口",
        danger: reject,
      });
      loaded.delete("plans");
      await loadPlans();
    } catch (err) {
      toastError(ctx.bus, err, "调仓计划人工操作失败");
    }
  }

  function planCard(plan) {
    const card = el("article", "card detail-card");
    const compareHost = el("div");
    const head = el("div", "detail-card__head");
    head.append(el("h3", "", plan.plan_id || plan.file));
    const [stateText, stateTone] = PLAN_STATE_LABEL[plan.state] || [plan.state || "未知", "muted"];
    head.append(badge(stateText, stateTone));
    if (plan.bucket === "archive") head.append(badge("归档", "muted"));
    if (plan.plan_id) {
      const transitionBtn = el("button", "btn btn--ghost", "流转");
      transitionBtn.type = "button";
      transitionBtn.addEventListener("click", () => writeOps.planTransition(plan.plan_id));
      head.append(transitionBtn);
      if (plan.bucket === "active") {
        const reviewBtn = el("button", "btn btn--ghost", "重新解读");
        reviewBtn.type = "button";
        reviewBtn.addEventListener("click", () => writeOps.planReview(plan.plan_id));
        head.append(reviewBtn);
      }
      const compareBtn = el("button", "btn btn--ghost", "资金对比");
      compareBtn.type = "button";
      compareBtn.addEventListener("click", () =>
        showComparison(plan.plan_id, compareHost, compareBtn),
      );
      head.append(compareBtn);
    }
    card.append(head);

    const win = plan.effective_window || {};
    card.append(
      kvEl([
        ["策略", plan.strategy_profile_ref || "（手工计划）"],
        ["生成时间", fmtTime(plan.generated_at)],
        ["执行窗口", `${fmtDate(win.start)} ~ ${fmtDate(win.end)}`],
        ["数据截至", plan.data_manifest_ref || "—"],
        ["预估费税", feesText(plan.estimated_fees_by_currency)],
        ["文件", plan.file],
      ]),
    );

    if (plan.trigger_reason) {
      card.append(el("p", "portfolio__plan-reason", plan.trigger_reason));
    }
    if ((plan.caveats || []).length) {
      const list = el("ul", "portfolio__plan-caveats");
      for (const c of plan.caveats) list.append(el("li", "", c));
      card.append(list);
    }

    if ((plan.target_holdings || []).length) {
      card.append(el("h4", "", `目标持仓（${plan.target_holdings.length}）`));
      const wrap = el("div", "portfolio__plan-table");
      wrap.append(
        tableEl(
          [
            { key: "instrument_id", label: "标的" },
            { key: "side", label: "方向", format: (v) => badge(v, "muted") },
            { key: "qty", label: "数量", num: true, format: (v) => fmtNum(v, 0) },
            { key: "weight", label: "权重", num: true, format: (v) => (v == null ? "—" : fmtPct(v)) },
          ],
          plan.target_holdings,
        ),
      );
      card.append(wrap);
    }

    if ((plan.suggested_trades || []).length) {
      card.append(el("h4", "", `建议交易（${plan.suggested_trades.length}）`));
      const wrap = el("div", "portfolio__plan-table");
      wrap.append(
        tableEl(
          [
            { key: "instrument_id", label: "标的" },
            {
              key: "action",
              label: "动作",
              format: (v) =>
                badge(
                  PLAN_ACTION_LABEL[v] || v,
                  v === "open" || v === "increase" ? "accent" : "warn",
                ),
            },
            { key: "qty", label: "数量", num: true, format: (v) => fmtNum(v, 0) },
            { key: "reference_price", label: "参考价", num: true, format: (v) => fmtNum(v, 4) },
            { key: "estimated_cost", label: "预估费税", num: true, format: (v) => fmtNum(v) },
            { key: "currency", label: "币种" },
          ],
          plan.suggested_trades,
        ),
      );
      card.append(wrap);
    }

    card.append(compareHost);
    return card;
  }

  function feesText(fees) {
    const entries = Object.entries(fees || {});
    if (!entries.length) return "—";
    return entries.map(([cur, amount]) => `${cur} ${fmtNum(amount)}`).join("；");
  }

  async function showComparison(planId, host, btn) {
    btn.disabled = true;
    try {
      let payload;
      try {
        payload = await ctx.api.planComparison(currentAccount, planId);
      } catch (err) {
        if (err.code !== "portfolio.comparison_not_found") throw err;
        payload = await writeOps.planCompare(planId);
        if (!payload) return;
      }
      await renderComparison(host, payload);
    } catch (err) {
      toastError(ctx.bus, err, "资金对比加载失败");
    } finally {
      btn.disabled = false;
    }
  }

  async function renderComparison(host, payload) {
    host.innerHTML = "";
    const box = el("div", "portfolio__compare");
    box.append(el("h4", "", "资金口径对比（历史模拟，非预测）"));
    const win = payload.window || {};
    box.append(
      kvEl([
        ["回测窗口", `${fmtDate(win.start)} ~ ${fmtDate(win.end)}`],
        ["参考资金", fmtNum(payload.reference_capital)],
        ["账户资金", fmtNum(payload.account_capital)],
        ["生成时间", fmtTime(payload.generated_at)],
      ]),
    );

    try {
      const [refNav, accNav] = await Promise.all([
        ctx.api.backtestNav(payload.reference_run_id),
        ctx.api.backtestNav(payload.account_run_id),
      ]);
      const chart = comparisonChart(refNav.items || [], accNav.items || []);
      if (chart) box.append(chart);
    } catch (err) {
      box.append(el("p", "item-row__meta", `净值曲线加载失败：${err.message}`));
    }

    const metrics = payload.metrics || {};
    const rows = COMPARE_METRIC_LABELS.map(([key, label]) => {
      const [group, sub] = [key.slice(0, key.indexOf(".")), key.slice(key.indexOf(".") + 1)];
      return {
        label,
        ref: ((metrics.reference || {})[group] || {})[sub],
        acc: ((metrics.account || {})[group] || {})[sub],
        delta: (metrics.delta || {})[key],
      };
    });
    box.append(
      tableEl(
        [
          { key: "label", label: "指标" },
          { key: "ref", label: "参考资金", num: true, format: (v) => compareNum(v) },
          { key: "acc", label: "账户资金", num: true, format: (v) => compareNum(v) },
          { key: "delta", label: "差值（账户−参考）", num: true, format: (v) => deltaCell(v) },
        ],
        rows,
      ),
    );

    const ai = payload.ai || {};
    if (ai.ok) {
      const aiBox = el("div", "portfolio__compare-ai");
      aiBox.append(el("p", "portfolio__plan-reason", ai.summary || ""));
      if ((ai.shortfall_causes || []).length) {
        const list = el("ul", "portfolio__plan-caveats");
        for (const cause of ai.shortfall_causes) list.append(el("li", "", cause));
        aiBox.append(list);
      }
      box.append(aiBox);
    } else if (ai.error) {
      box.append(el("p", "item-row__meta", `模型解读失败：${ai.error}`));
    }
    if (payload.note) box.append(el("p", "item-row__meta", payload.note));

    const rerunBtn = el("button", "btn btn--ghost", "重新对比");
    rerunBtn.type = "button";
    rerunBtn.addEventListener("click", async () => {
      rerunBtn.disabled = true;
      try {
        const fresh = await writeOps.planCompare(payload.plan_id);
        if (fresh) await renderComparison(host, fresh);
      } finally {
        rerunBtn.disabled = false;
      }
    });
    box.append(rerunBtn);
    host.append(box);
  }

  function comparisonChart(refItems, accItems) {
    if (!refItems.length && !accItems.length) return null;
    const base = refItems.length >= accItems.length ? refItems : accItems;
    const labels = base.map((r) => fmtDate(r.date));
    const norm = (items) => {
      const first = items.find((r) => r.nav != null);
      const nav0 = first ? Number(first.nav) || 1 : 1;
      const byDate = new Map(items.map((r) => [r.date, r.nav]));
      return base.map((r) => {
        const v = byDate.get(r.date);
        return v == null ? null : Number(v) / nav0;
      });
    };
    return lineChart({
      series: [
        { label: "参考资金", color: "var(--accent)", values: norm(refItems) },
        { label: "账户资金", color: "var(--warn)", values: norm(accItems) },
      ],
      labels,
      height: 240,
    });
  }

  function compareNum(value) {
    if (value == null || value === "") return "—";
    const num = Number(value);
    if (!Number.isFinite(num)) return String(value);
    return String(Number(num.toPrecision(6)));
  }

  function deltaCell(value) {
    const text = compareNum(value);
    if (text === "—") return text;
    const span = el("span", signClass(value), text);
    return span;
  }

  async function loadReconcile() {
    await renderState(hosts.reconcile, { kind: "loading", title: "对账中…" });
    try {
      const [data, attributionData, learningData] = await Promise.all([
        ctx.api.reconcile(currentAccount),
        ctx.api.attributions({ account_id: currentAccount }),
        ctx.api.learningEvents(),
      ]);
      hosts.reconcile.innerHTML = "";
      const card = el("article", "card detail-card");
      const head = el("div", "detail-card__head");
      head.append(el("h3", "", "三方对账"));
      const variant = data.status === "ok" ? "safe" : data.status === "warn" ? "warn" : "danger";
      head.append(badge(data.status, variant));
      card.append(head);
      card.append(
        kvEl([
          ["对账口径", "成交推导、当前持仓与最新快照"],
          ["有快照", data.has_snapshot ? "是" : "否"],
          ["重置断点", data.since_reinit ? fmtTime(data.since_reinit) : "（无）"],
        ]),
      );
      if (!data.rows.length) {
        card.append(el("p", "item-row__meta", "三方均无持仓，无差异。"));
      } else {
        card.append(
          tableEl(
            [
              { key: "instrument_id", label: "标的" },
              { key: "side", label: "方向", format: (v) => badge(v, "muted") },
              { key: "derived_qty", label: "成交推导", num: true, format: (v) => fmtNum(v, 0) },
              { key: "holdings_qty", label: "持仓表", num: true, format: (v) => fmtNum(v, 0) },
              {
                key: "snapshot_qty",
                label: "最新快照",
                num: true,
                format: (v) => (v === "" ? "—" : fmtNum(v, 0)),
              },
              {
                key: "status",
                label: "结论",
                format: (v) => badge(v, v === "ok" ? "safe" : v === "warn" ? "warn" : "danger"),
              },
            ],
            data.rows,
          ),
        );
      }
      hosts.reconcile.append(card);
      if ((attributionData.items || []).length) {
        const attributionCard = el("article", "card detail-card");
        attributionCard.append(
          el("h3", "", "实际收益归因"),
          tableEl(
            [
              { key: "window", label: "标签区间" },
              { key: "strategy", label: "策略版本" },
              { key: "realized", label: "实际收益", num: true, format: (v) => fmtPct(v) },
              { key: "signal", label: "信号", num: true, format: (v) => fmtPct(v) },
              { key: "constraint", label: "约束", num: true, format: (v) => fmtPct(v) },
              { key: "execution", label: "执行", num: true, format: (v) => fmtPct(v) },
              { key: "override", label: "用户调整", num: true, format: (v) => fmtPct(v) },
            ],
            attributionData.items.map((item) => {
              const record = item.attribution;
              const values = Object.fromEntries((record.components || []).map((part) => [part.layer, part.contribution]));
              return {
                window: `${record.label_window.start} .. ${record.label_window.end}`,
                strategy: record.strategy_version_id,
                realized: record.realized_return,
                signal: values.signal,
                constraint: values.constraint,
                execution: values.execution,
                override: values.user_override,
              };
            }),
          ),
        );
        hosts.reconcile.append(attributionCard);
      }
      if ((learningData.items || []).length) {
        const learningCard = el("article", "card detail-card");
        learningCard.append(el("h3", "", "衰减告警与研究记录"));
        for (const item of learningData.items) {
          const event = item.learning_event;
          const row = el("div", "item-row");
          row.append(
            badge(event.category, item.state === "accepted" ? "safe" : "warn"),
            el("strong", "", event.observation),
            el("span", "item-row__meta", event.recommended_action),
          );
          if (item.state === "verified") {
            const accept = el("button", "btn btn--ghost", "接受为研究记忆");
            accept.type = "button";
            accept.addEventListener("click", async () => {
              try {
                await runWithConfirm({
                  execute: (p) => ctx.api.learningEventAccept(item.object_id, { confirm: p.confirm, reason: p.note || "", decided_by: p.decided_by || "" }),
                  title: "接受实盘经验事实",
                  requireDecidedBy: true,
                  notePlaceholder: "记录人工核验结论",
                  confirmLabel: "接受并供下轮模型 读取",
                });
                loaded.delete("reconcile");
                await loadReconcile();
              } catch (err) {
                toastError(ctx.bus, err, "经验事实确认失败");
              }
            });
            row.append(accept);
          }
          learningCard.append(row);
        }
        hosts.reconcile.append(learningCard);
      }
    } catch (err) {
      await renderState(hosts.reconcile, {
        kind: "error",
        title: "对账失败",
        detail: err.message,
        retry: () => loadReconcile(),
      });
    }
  }
}
