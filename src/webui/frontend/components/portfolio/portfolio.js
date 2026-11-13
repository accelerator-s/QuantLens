import { labelText } from "../../core/labels.js";
import { badge, fmtDate, fmtNum, fmtPct, fmtTime } from "../../core/fmt.js";
import { el, kvEl, selectMarkup, tableEl } from "../../core/dom.js";
import { createSelect } from "../../core/select.js";
import { renderState } from "../state-card/state-card.js";
import { initPortfolioWrite } from "./portfolio-write.js";

const ACTION_LABELS = { open: "买入", increase: "加仓", decrease: "减仓", close: "卖出" };

export async function mount(root, ctx) {
  const accountCard = root.querySelector("[data-account-card]");
  const emptyHost = root.querySelector("[data-empty]");
  const workflow = root.querySelector("[data-recommendation-workflow]");
  const resultHost = root.querySelector("[data-recommendation-result]");
  const generateButton = root.querySelector("[data-recommend-generate]");
  const strategyHint = root.querySelector("[data-strategy-hint]");
  const ledgerHost = root.querySelector("[data-account-ledger]");
  const holdingsHost = root.querySelector("[data-holdings]");
  const tradesHost = root.querySelector("[data-trades]");
  const undoButton = root.querySelector("[data-trade-undo]");
  const moreButton = root.querySelector("[data-trades-more]");
  let accounts = [];
  let strategies = [];
  let currentAccount = ctx.accountId || "";
  let defaultAccountId = "";
  let loadVersion = 0;
  let resultVersion = 0;
  let generating = false;
  let writing = false;
  let ledgerVersion = 0;
  let tradesRows = [];
  let tradesTotal = 0;
  let latestTradeId = "";
  const beijingDate = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai" }).format(new Date());
  let holdingsDate = beijingDate();

  const writeOps = initPortfolioWrite(root, ctx, {
    reloadAccounts: load,
    account: () => accounts.find((item) => item.account_id === currentAccount),
    latestTradeId: () => latestTradeId,
    setWriting: (busy) => { writing = busy; updateControls(); },
  });
  const accountSelectHost = root.querySelector("[data-account-select]");
  accountSelectHost.innerHTML = selectMarkup("选择账户");
  const accountSelect = createSelect(accountSelectHost, {
    emptyLabel: "（无账户）",
    onChange: async (id) => {
      currentAccount = id;
      ctx.accountId = id;
      renderAccountCard();
      updateStrategies();
      await Promise.all([loadSavedSuggestion(), loadLedger()]);
    },
  });
  const strategySelectHost = root.querySelector("[data-strategy-select]");
  strategySelectHost.innerHTML = selectMarkup("选择策略");
  const strategySelect = createSelect(strategySelectHost, {
    emptyLabel: "暂无可用策略",
    onChange: async (id) => {
      ctx.portfolioStrategy = id;
      await loadSavedSuggestion();
    },
  });
  accountSelectHost.querySelector("[data-select-trigger]").setAttribute("aria-label", "选择账户");
  strategySelectHost.querySelector("[data-select-trigger]").setAttribute("aria-labelledby", "portfolio-strategy-label");

  root.querySelector("[data-recommendation-form]").addEventListener("submit", (event) => {
    event.preventDefault();
    generate();
  });
  moreButton.addEventListener("click", loadMoreTrades);
  ctx.bus.on("route", (id) => {
    if (id === "portfolio" && !generating && !writing) load();
  });
  window.setInterval(() => {
    if (root.isConnected && !root.hidden && currentAccount && !generating && !writing && holdingsDate !== beijingDate()) loadLedger();
  }, 60_000);
  await load();

  function state(host, options) {
    // A late icon fetch may finish after the account or strategy changes.
    const slot = el("div");
    host.replaceChildren(slot);
    return renderState(slot, options);
  }

  async function load() {
    const version = ++loadVersion;
    ++resultVersion;
    generateButton.disabled = true;
    const [accountResult, strategyResult] = await Promise.allSettled([
      ctx.api.accounts(), ctx.api.strategies(),
    ]);
    if (version !== loadVersion) return;
    if (accountResult.status === "rejected") {
      accountCard.replaceChildren();
      workflow.hidden = true;
      ledgerHost.hidden = true;
      emptyHost.hidden = false;
      state(emptyHost, { kind: "error", title: "账户加载失败", detail: accountResult.reason.message, retry: load });
      return;
    }
    accounts = accountResult.value.items || [];
    defaultAccountId = accountResult.value.default_account_id || "";
    accountSelect.setOptions(accounts.map((account) => ({
      value: account.account_id, label: account.alias || account.account_id,
    })), accounts.some((account) => account.account_id === currentAccount) ? currentAccount : defaultAccountId);
    currentAccount = accountSelect.value;
    ctx.accountId = currentAccount;
    renderAccountCard();
    emptyHost.hidden = accounts.length > 0;
    workflow.hidden = !accounts.length;
    ledgerHost.hidden = !accounts.length;
    if (!accounts.length) {
      resultHost.replaceChildren();
      ++ledgerVersion;
      latestTradeId = "";
      holdingsHost.replaceChildren();
      tradesHost.replaceChildren();
      state(emptyHost, { kind: "empty", title: "先创建一个账户", detail: "设置可用资金后，选择策略获取选股建议。" });
      return;
    }
    const ledgerLoad = loadLedger();
    if (strategyResult.status === "rejected") {
      strategies = [];
      updateStrategies();
      state(resultHost, { kind: "error", title: "策略加载失败", detail: strategyResult.reason.message, retry: load });
      await ledgerLoad;
      return;
    }
    strategies = strategyResult.value.items || [];
    updateStrategies();
    await Promise.all([loadSavedSuggestion(), ledgerLoad]);
  }

  function updateStrategies() {
    const account = accounts.find((item) => item.account_id === currentAccount);
    const available = strategies.filter((strategy) => strategy.status === "active" && !strategy.is_template &&
      (!strategy.market || strategy.market === account?.market));
    const preferred = available.some((strategy) => strategy.profile_name === ctx.portfolioStrategy)
      ? ctx.portfolioStrategy : "";
    strategySelect.setOptions(available.map((strategy) => ({
      value: strategy.profile_name, label: strategy.display_name || strategy.profile_name,
    })), preferred);
    ctx.portfolioStrategy = strategySelect.value;
    generateButton.disabled = generating || writing || !currentAccount || !strategySelect.value;
    strategyHint.textContent = available.length
      ? "根据账户资金和交易权限，使用最新可用行情生成建议。"
      : "暂无可用策略，请先在策略回测中创建并启用策略。";
  }

  async function loadSavedSuggestion() {
    const version = ++resultVersion;
    const accountId = currentAccount;
    const profile = strategySelect.value;
    if (!accountId || !profile) {
      resultHost.replaceChildren();
      return;
    }
    state(resultHost, { kind: "loading", title: "读取选股建议…" });
    try {
      const data = await ctx.api.plans(accountId);
      if (version !== resultVersion) return;
      const plan = (data.items || []).filter((item) => item.strategy_profile_ref === profile && item.state === "active")
        .sort((a, b) => String(b.generated_at || "").localeCompare(String(a.generated_at || "")))[0];
      if (plan) renderSuggestion(plan);
      else state(resultHost, { kind: "empty", title: "选择策略后，获取选股建议", detail: "建议会显示在这里。" });
    } catch (error) {
      if (version !== resultVersion) return;
      state(resultHost, { kind: "error", title: "历史建议读取失败", detail: error.message, retry: loadSavedSuggestion });
    }
  }

  function setBusy(busy) {
    generating = busy;
    generateButton.textContent = busy ? "正在生成建议…" : "获取选股建议";
    resultHost.setAttribute("aria-busy", String(busy));
    updateControls();
  }

  function updateControls() {
    const busy = generating || writing;
    generateButton.disabled = busy || !currentAccount || !ctx.portfolioStrategy;
    root.querySelectorAll("[data-account-create], [data-account-select] button, [data-strategy-select] button, .portfolio__account-actions button, [data-account-ledger] button")
      .forEach((button) => { button.disabled = busy; });
    undoButton.disabled = busy || !latestTradeId;
  }

  async function generate() {
    const accountId = currentAccount;
    const profile = strategySelect.value;
    if (generating || writing || !accountId || !profile) return;
    const version = ++resultVersion;
    setBusy(true);
    state(resultHost, { kind: "loading", title: "正在生成选股建议", detail: "正在计算策略信号和建议仓位，请稍候。" });
    try {
      const result = await ctx.api.recommendGenerate({ account: accountId, profile, as_of: "" });
      if (version !== resultVersion || accountId !== currentAccount || profile !== strategySelect.value) return;
      renderSuggestion(result.plan, result);
    } catch (error) {
      if (version !== resultVersion) return;
      state(resultHost, { kind: "error", title: "选股建议生成失败", detail: error.message });
    } finally {
      setBusy(false);
    }
  }

  function stockCell(code) {
    const button = el("button", "ql-link", code);
    button.type = "button";
    button.addEventListener("click", () => { ctx.stock = code; ctx.bus.emit("route", "stocks"); });
    return button;
  }

  async function loadLedger() {
    const version = ++ledgerVersion;
    const accountId = currentAccount;
    if (!accountId) return;
    latestTradeId = "";
    undoButton.disabled = true;
    moreButton.hidden = true;
    state(holdingsHost, { kind: "loading", title: "读取持仓…" });
    state(tradesHost, { kind: "loading", title: "读取交易记录…" });
    const [holdings, trades] = await Promise.allSettled([
      ctx.api.holdings(accountId), ctx.api.trades(accountId, { limit: 50, offset: 0 }),
    ]);
    if (version !== ledgerVersion || accountId !== currentAccount) return;
    holdingsDate = beijingDate();
    if (holdings.status === "fulfilled") renderHoldings(holdings.value.items || []);
    else state(holdingsHost, { kind: "error", title: "持仓读取失败", detail: holdings.reason.message, retry: loadLedger });
    if (trades.status === "fulfilled") {
      tradesRows = trades.value.items || [];
      tradesTotal = trades.value.total_rows || 0;
      latestTradeId = trades.value.latest_trade_id || "";
      renderTrades();
    } else {
      tradesRows = [];
      state(tradesHost, { kind: "error", title: "交易记录读取失败", detail: trades.reason.message, retry: loadLedger });
      root.querySelector("[data-trades-count]").textContent = "";
    }
    updateControls();
  }

  function renderHoldings(items) {
    if (!items.length) {
      holdingsHost.replaceChildren(el("p", "portfolio__result-note", "暂无持仓，可录入已有持仓或成交。"));
      return;
    }
    holdingsHost.replaceChildren(tableEl([
      { key: "instrument_id", label: "股票", format: stockCell },
      { key: "qty", label: "持仓（股）", num: true, format: (value) => fmtNum(value, 0) },
      { key: "available_qty", label: "可卖（股）", num: true, format: (value) => fmtNum(value, 0) },
      { key: "frozen_qty", label: "冻结（股）", num: true, format: (value) => fmtNum(value, 0) },
      { key: "cost_price", label: "成本价", num: true, format: (value) => fmtNum(value, 4) },
      { key: "edit", label: "", format: (_value, row) => {
        const button = el("button", "btn btn--ghost btn--sm", "修改");
        button.type = "button";
        button.addEventListener("click", () => writeOps.holdingSet(row));
        return button;
      } },
    ], items));
  }

  function renderTrades() {
    if (tradesRows.length) tradesHost.replaceChildren(tableEl([
      { key: "executed_at", label: "成交时间", format: fmtTime },
      { key: "instrument_id", label: "股票", format: stockCell },
      { key: "action", label: "买卖", format: (value) => ["open", "increase"].includes(value) ? "买入" : "卖出" },
      { key: "qty", label: "数量（股）", num: true, format: (value) => fmtNum(value, 0) },
      { key: "price", label: "成交价", num: true, format: (value) => fmtNum(value, 4) },
      { key: "fee", label: "手续费", num: true, format: (value) => fmtNum(value) },
      { key: "tax", label: "印花税", num: true, format: (value) => fmtNum(value) },
      { key: "is_reversed", label: "状态", format: (value) => badge(value ? "已撤回" : "已记录", value ? "muted" : "accent") },
      { key: "reversed_at", label: "撤回时间", format: fmtTime },
      { key: "note", label: "备注" },
    ], tradesRows));
    else tradesHost.replaceChildren(el("p", "portfolio__result-note", "暂无交易记录。"));
    root.querySelector("[data-trades-count]").textContent = `已显示 ${tradesRows.length} / ${tradesTotal} 笔`;
    moreButton.hidden = tradesRows.length >= tradesTotal;
    moreButton.disabled = generating || writing;
    undoButton.disabled = generating || writing || !latestTradeId;
  }

  async function loadMoreTrades() {
    if (moreButton.disabled || tradesRows.length >= tradesTotal) return;
    const version = ledgerVersion;
    const accountId = currentAccount;
    moreButton.disabled = true;
    try {
      const data = await ctx.api.trades(accountId, { limit: 50, offset: tradesRows.length });
      if (version !== ledgerVersion || accountId !== currentAccount) return;
      tradesRows.push(...(data.items || []));
      tradesTotal = data.total_rows || 0;
      renderTrades();
    } catch (error) { ctx.bus.emit("toast", { kind: "error", title: "交易记录加载失败", detail: error.message }); }
    finally { moreButton.disabled = generating || writing; }
  }

  function renderSuggestion(plan, result = {}) {
    const card = el("article", "card detail-card portfolio__suggestion");
    const heading = el("div", "detail-card__head");
    const strategy = strategies.find((item) => item.profile_name === plan.strategy_profile_ref);
    heading.append(el("h3", "", `${strategy?.display_name || plan.strategy_profile_ref} · 选股建议`));
    card.append(heading);
    const meta = el("div", "item-row__meta");
    meta.append(el("span", "", `生成于 ${fmtTime(plan.generated_at)}`));
    if (result.signal_date) meta.append(el("span", "", `行情截至 ${fmtDate(result.signal_date)}`));
    if (plan.effective_window?.start) meta.append(el("span", "", `参考交易日 ${fmtDate(plan.effective_window.start)}`));
    card.append(meta);
    const targets = plan.target_holdings || [];
    const trades = plan.suggested_trades || [];
    if (targets.length) {
      const rows = targets.map((target) => ({
        ...target, action: trades.find((trade) => trade.instrument_id === target.instrument_id)?.action || "hold",
      }));
      card.append(tableEl([
        { key: "instrument_id", label: "股票", format: stockCell },
        { key: "action", label: "建议", format: (value) => badge(ACTION_LABELS[value] || "持有", value === "open" || value === "increase" ? "accent" : "muted") },
        { key: "qty", label: "建议持仓（股）", num: true, format: (value) => fmtNum(value, 0) },
        { key: "weight", label: "目标仓位", num: true, format: (value) => fmtPct(value) },
      ], rows));
    } else {
      card.append(el("p", "portfolio__result-note", "当前条件下没有符合要求的股票，建议保留现金。"));
    }
    const exits = trades.filter((trade) => trade.action === "close" ||
      (trade.action === "decrease" && !targets.some((target) => target.instrument_id === trade.instrument_id)));
    if (exits.length) {
      card.append(el("h4", "", "现有持仓调整"), tableEl([
        { key: "instrument_id", label: "股票", format: stockCell },
        { key: "action", label: "建议", format: (value) => ACTION_LABELS[value] },
        { key: "qty", label: "调整数量（股）", num: true, format: (value) => fmtNum(value, 0) },
        { key: "reference_price", label: "参考价", num: true, format: (value) => fmtNum(value) },
      ], exits));
    }
    if (plan.trigger_reason) card.append(el("p", "portfolio__plan-reason", plan.trigger_reason));
    if (plan.caveats?.length) {
      const list = el("ul", "portfolio__plan-caveats");
      for (const caveat of plan.caveats) list.append(el("li", "", caveat));
      card.append(list);
    }
    if (result.ai_ok === false) card.append(el("p", "portfolio__result-note", "选股建议已生成，文字解读暂不可用。"));
    resultHost.replaceChildren(card);
  }

  function renderAccountCard() {
    const account = accounts.find((a) => a.account_id === currentAccount);
    accountCard.innerHTML = "";
    if (!account) return;
    const card = el("article", "card detail-card portfolio__account-card");
    const head = el("div", "detail-card__head");
    head.append(el("h3", "", account.alias || account.account_id));
    const tags = el("div", "portfolio__account-tags");
    if (account.account_id === defaultAccountId) tags.append(badge('默认账户', 'accent'));
    if (tags.childElementCount) head.append(tags);
    card.append(head);
    const cashNode = el("div", "portfolio__cash-list");
    for (const c of account.cash || []) {
      const balance = el("div", "portfolio__cash-balance");
      const total = el("div", "portfolio__cash-total");
      total.append(
        el("span", "portfolio__cash-currency", labelText(c.currency)),
        el("span", "", fmtNum(c.total)),
      );
      balance.append(total, el("div", "portfolio__cash-available", `可用 ${fmtNum(c.available)}`));
      cashNode.append(balance);
    }
    if (!(account.cash || []).length) cashNode.textContent = "—";
    const details = kvEl([
      ["现金", cashNode],
      ["券商", account.broker],
      ["科创板权限", account.star_market_enabled ? "已开通" : "未开通"],
      ["创建时间", fmtTime(account.created_at)],
      ["备注", account.notes],
    ]);
    details.classList.add("portfolio__account-details");
    card.append(details);
    const actions = el("div", "portfolio__account-actions");
    const cashBtn = el("button", "btn btn--ghost", "调整现金");
    cashBtn.type = "button";
    cashBtn.addEventListener("click", () =>
      writeOps.cashSet(account),
    );
    actions.append(cashBtn);
    const editBtn = el("button", "btn btn--ghost", "修改账户");
    editBtn.type = "button";
    editBtn.addEventListener("click", () => writeOps.editAccount(account));
    actions.append(editBtn);
    const deleteBtn = el("button", "btn btn--danger", "删除账户");
    deleteBtn.type = "button";
    deleteBtn.addEventListener("click", () => writeOps.deleteAccount(account));
    actions.append(deleteBtn);
    card.append(actions);
    accountCard.append(card);
  }

}
