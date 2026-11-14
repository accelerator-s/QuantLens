import {newestFirst} from '../../core/sort.js';
import { renderState } from "../state-card/state-card.js";
import { configureDatePicker } from "../../core/date-picker.js";
import { areaChart, barChart } from "../../core/charts.js";
import { badge, fmtDate, fmtNum, fmtPct, fmtTime, signClass } from "../../core/fmt.js";
import { el, kvEl, tableEl, remoteTableEl } from "../../core/dom.js";
import { returnComparison } from "./return-comparison.js";

const TRADES_PAGE = 10;


const METRIC_DEFS = {
  start: ["起始", fmtDate],
  end: ["截止", fmtDate],
  trading_days: ["交易日数", String],
  trading_days_per_year: ["年化交易日", String],
  final_nav: ["期末净值", (v) => fmtNum(v, 0)],
  total_return: ["总收益", fmtPct, true],
  annualized_return: ["年化收益", fmtPct, true],
  benchmark_total_return: ["基准总收益", fmtPct, true],
  benchmark_annualized_return: ["基准年化收益", fmtPct, true],
  annualized_excess_return: ["年化超额收益", fmtPct, true],
  sharpe: ["夏普比率", (v) => fmtNum(v, 3), true],
  information_ratio: ["信息比率", (v) => fmtNum(v, 3), true],
  annualized_volatility: ["年化波动率", fmtPct],
  max_drawdown: ["最大回撤", fmtPct, true],
  max_drawdown_peak_date: ["回撤峰值日", fmtDate],
  max_drawdown_trough_date: ["回撤谷底日", fmtDate],
  daily_win_rate: ["日胜率", fmtPct],
  excess_win_rate: ["超额胜率", fmtPct],
  total_buy_amount: ["总买入额", (v) => fmtNum(v, 0)],
  total_sell_amount: ["总卖出额", (v) => fmtNum(v, 0)],
  annualized_turnover_oneside: ["年化换手（单边）", (v) => fmtNum(v, 2)],
  total_cost_paid: ["总交易成本", (v) => fmtNum(v, 2)],
  annualized_cost_drag: ["年化成本拖累", fmtPct],
  avg_holdings: ["平均持仓数", (v) => fmtNum(v, 1)],
  lot_unaffordable_count: ["资金不足一手次数", String],
  lot_unaffordable_amount: ["不足一手的订单金额", (v) => fmtNum(v, 2)],
  lot_unaffordable_amount_ratio: ["不足一手的订单占比", fmtPct],
};

const METRIC_GROUPS = [
  ["returns", "收益"],
  ["risk", "风险"],
  ["trading", "交易"],
  ["orders", "订单执行"],
];


const RUN_STATE_LABEL = { running: "运行中", completed: "已完成", failed: "失败" };
const RUN_STATE_TONE = { running: "accent", completed: "safe", failed: "danger" };
const SIDE_LABEL = { buy: "买入", sell: "卖出" };
const ORDER_STATUS_LABEL = {
  filled: "成交",
  forced_delist_close: "退市强平",
  rejected_lot_unaffordable: "不足一手拒单",
  rejected_insufficient_cash: "现金不足拒单",
  rejected_limit_up: "涨停拒单",
  rejected_suspension: "停牌拒单",
  pending_limit_down: "跌停挂起",
  pending_suspension: "停牌挂起",
};

function runStateBadge(state) {
  return badge(RUN_STATE_LABEL[state] || state || "?", RUN_STATE_TONE[state] || "muted");
}

function benchmarkSummary(run) {
  const benchmark = run.metrics?.benchmark || {};
  const protocol = run.config?.protocol || {};
  const names = {
    same_cluster_equal_weight: "同风格簇等权",
    universe_equal_weight: "股票池等权",
    universe_float_cap_weight: "股票池流通市值加权",
    industry_equal_weight: "同行业等权",
    index: "全收益指数",
    blend: "自定义混合基准",
    cash: "现金零收益",
  };
  const name = benchmark.name || protocol.benchmark;
  const label = benchmark.label || names[name] || name || "未记录口径";
  return { label };
}

async function mountContent(root, ctx) {
  const listHost = root.querySelector("[data-list]");
  const detailHost = root.querySelector("[data-detail]");

  const viewState = ((ctx.tabState ||= {}).backtest ||= {});
  let requestedRun = viewState.requestedRun || (!viewState.runId ? ctx.backtestRun : '') || '';
  let currentId = requestedRun || viewState.runId || '';
  let linkedRun = requestedRun || viewState.linkedRun || '';
  let experiments = null;
  const curveVisibility = (ctx.tabState ||= {}).curveVisibility ||= {};
  let comparisonChart;
  let detailRequest = 0;
  let renderedRunId = '';
  let listRequest = 0;
  let listRevision = '';
  let refreshTimer;
  let runItems = [];
  let loadingRunId = '';
  const detailCache = new Map();
  const resourceCache = new Map();
  const signature = id => JSON.stringify(runItems.find(item => item.run_id === id) || {});
  function cachedResource(key, fetcher) {
    if (!resourceCache.has(key)) {
      const promise = fetcher().catch(error => { resourceCache.delete(key); throw error; });
      resourceCache.set(key, promise);
      if (resourceCache.size > 100) resourceCache.delete(resourceCache.keys().next().value);
    }
    return resourceCache.get(key);
  }

  ctx.bus.on("route", (id) => {
    if (id === "backtest") load({quiet: !!listRevision});
  });
  
  ctx.bus.on("backtest:open", (runId) => {
    currentId = runId;
    requestedRun = runId;
    viewState.requestedRun = runId;
    viewState.runId = runId;
    linkedRun = viewState.linkedRun = runId;
    if (listRevision) renderList(runItems, false);
  });
  await load();

  function factorSummary(factors) {
    const items = factors?.items || [];
    if (!items.length) return "";
    const names = items.map((f) => {
      if (f.weight_override == null) return f.name;
      const weight = fmtNum(f.weight_override, 3).replace(/\.?0+$/, "");
      return `${f.name}（权重 ${weight}）`;
    });
    const tail = factors.weighting === "equal" && items.length > 1 ? "，等权合成" : "";
    const preview = names.slice(0, 2).join("、") + (names.length > 2 ? `…等 ${names.length} 个因子` : "");
    const node = el("span", "backtest__factor-summary", `${preview}${tail}`);
    node.title = `${names.join("、")}${tail}`;
    node.setAttribute("aria-label", node.title);
    return node;
  }

  function scheduleRefresh() {
    clearTimeout(refreshTimer);
    if (!runItems.some(item => item.state === "running")) return;
    refreshTimer = setTimeout(() => {
      if (root.closest('.view')?.hidden || document.hidden) scheduleRefresh();
      else load({ quiet: true });
    }, 5000);
  }

  async function load({ quiet = !!listRevision } = {}) {
    clearTimeout(refreshTimer);
    const request = ++listRequest;
    if (!quiet) await renderState(listHost, { kind: "loading", title: "加载回测运行…" });
    try {
      const data = await ctx.api.backtestRuns();
      if (request !== listRequest) return;
      data.items = newestFirst(data.items, ['updated_at', 'created_at']);
      const revision = JSON.stringify(data.items);
      const previousSignature = signature(currentId);
      runItems = data.items;
      const changed = previousSignature !== signature(currentId);
      if (changed) {
        detailCache.delete(currentId);
        resourceCache.clear();
        experiments = null;
      }
      if (!quiet || revision !== listRevision || currentId !== renderedRunId || requestedRun) renderList(data.items, changed);
      listRevision = revision;
    } catch (err) {
      if (request !== listRequest) return;
      if (quiet && listRevision) {
        ctx.bus.emit('toast', {kind: 'error', title: '回测列表更新失败', detail: err.message});
        return;
      }
      listRevision = '';
      await renderState(listHost, {
        kind: "error",
        title: "回测运行加载失败",
        detail: err.message,
        retry: () => load(),
      });
    } finally {
      if (request === listRequest) scheduleRefresh();
    }
  }

  function renderList(items, refreshDetail = true) {
    const previousId = currentId;
    const pageState = listHost.querySelector('.table-panel')?.pageState?.() || viewState;
    listHost.innerHTML = "";
    if (currentId === linkedRun && !items.some(item => item.run_id === currentId)) {
      requestedRun = viewState.requestedRun = currentId;
    }
    if (!items.length) {
      renderState(listHost, {
        kind: "empty",
        title: "暂无已发布回测",
      });
      if (requestedRun) loadDetail(requestedRun, refreshDetail);
      else {
        ++detailRequest;
        comparisonChart?.dispose();
        detailHost.innerHTML = "";
      }
      return;
    }
    
    if (!currentId || (!requestedRun && !items.some(item => item.run_id === currentId))) currentId = items[0].run_id;
    viewState.runId = currentId;
    const targetIndex = requestedRun ? items.findIndex(item => item.run_id === currentId) : -1;
    function selectRun(item) {
      requestedRun = '';
      viewState.requestedRun = '';
      linkedRun = viewState.linkedRun = '';
      currentId = item.run_id;
      viewState.runId = currentId;
      listHost.querySelectorAll('tbody tr').forEach(row => {
        const active = row.dataset.runId === currentId;
        row.classList.toggle('is-active', active);
        row.setAttribute('aria-selected', String(active));
      });
      loadDetail(currentId);
    }
    const table = tableEl([
      { key: 'run_id', label: '回测编号' },
      { key: 'profile', label: '采用策略', format: (value, row) => row.profile_display_name || value },
      { key: 'start', label: '回测区间', format: (value, row) => `${value} 至 ${row.end}` },
      { key: 'state', label: '状态', format: runStateBadge },
      { key: 'account_name', label: '账户', format: value => value || '通用研究' },
    ], items, {
      ...pageState,
      ...(targetIndex >= 0 ? {page: Math.floor(targetIndex / (pageState.pageSize || 10)) + 1} : {}),
      onPageChange: (page, pageSize) => Object.assign(viewState, {page, pageSize}),
      onRowClick: selectRun,
      onRowRender: (row, item) => {
        row.dataset.runId = item.run_id;
        row.classList.toggle('is-active', item.run_id === currentId);
        row.setAttribute('aria-selected', String(item.run_id === currentId));
      },
    });
    listHost.append(table);
    if (targetIndex >= 0) {
      requestedRun = '';
      viewState.requestedRun = '';
    }
    if (refreshDetail || currentId !== previousId || renderedRunId !== currentId) loadDetail(currentId, refreshDetail);
  }

  async function loadDetail(runId, force = false) {
    if (!force && (renderedRunId === runId || loadingRunId === runId)) return;
    const request = ++detailRequest;
    const revision = signature(runId);
    loadingRunId = runId;
    renderedRunId = '';
    comparisonChart?.dispose();
    const cached = detailCache.get(runId);
    if (cached && cached.revision === revision && !force) {
      renderDetail(cached.run, cached.nav);
      renderedRunId = runId;
      loadingRunId = '';
      cached.reference.then(reference => {
        if (currentId === runId && request === detailRequest) comparisonChart?.updateReference(reference);
      });
      return;
    }
    const slot = el('div');
    detailHost.replaceChildren(slot);
    renderState(slot, {kind: 'loading', title: '加载运行详情…'});
    try {
      const reference = ctx.api.backtestReferences(runId).catch(error => {
        detailCache.delete(runId);
        return {status: 'error', items: [], message: error.message || '市场指数加载失败，请重试。'};
      });
      const [data, nav] = await Promise.all([
        ctx.api.backtestRun(runId),
        ctx.api.backtestNav(runId).catch(() => null),
      ]);
      const run = {...data, run_id: runId};
      detailCache.set(runId, {run, nav, reference, revision});
      if (detailCache.size > 12) detailCache.delete(detailCache.keys().next().value);
      if (currentId !== runId || request !== detailRequest) return;
      renderDetail(run, nav);
      renderedRunId = runId;
      loadingRunId = '';
      const marketReference = await reference;
      if (currentId !== runId || request !== detailRequest) return;
      if (marketReference.status === 'error') detailCache.delete(runId);
      comparisonChart?.updateReference(marketReference);
    } catch (err) {
      if (currentId !== runId || request !== detailRequest) return;
      await renderState(detailHost, {
        kind: 'error', title: '运行详情加载失败', detail: err.message,
        retry: () => loadDetail(runId, true),
      });
    } finally {
      if (request === detailRequest) loadingRunId = '';
    }
  }

  function metricItem(key, value) {
    const def = METRIC_DEFS[key] || [key, null, false];
    const item = el("div", "metric");
    item.dataset.metric = key;
    item.append(el("span", "metric__label", def[0]));
    const text = value == null ? "—" : def[1] ? def[1](value) : String(value);
    const val = el("span", "metric__value", text);
    const sign = def[2] ? signClass(value) : "";
    if (sign) val.classList.add(sign);
    item.append(val);
    return item;
  }

  function renderDetail(run, nav) {
    comparisonChart = null;
    detailHost.innerHTML = "";
    const benchmark = benchmarkSummary(run);
    const card = el("article", "card detail-card");

    // ---- 头部与运行信息
    const head = el("div", "detail-card__head");
    head.append(el("h3", "", run.run_id));
    head.append(
      runStateBadge(run.status?.state),
      badge(run.profile_display_name || run.profile, "accent"),
    );
    if (run.archived) head.append(badge("历史归档", "muted"));
    const profile = run.config?.profile || {};
    const factorNames = (profile.factors?.items || []).map(f => run.factor_display_names?.[f.name] || f.name);
    for (const name of factorNames.slice(0, 3)) {
      const tag = badge(`因子 ${name}`);
      tag.classList.add("backtest__factor-badge");
      tag.title = name;
      head.append(tag);
    }
    if (factorNames.length > 3) {
      const more = badge(`另 ${factorNames.length - 3} 个因子`);
      more.title = factorNames.join("、");
      head.append(more);
    }
    if (run.config && run.config.protocol_locked === false) {
      head.append(badge("协议未锁定", "warn"));
    }
    card.append(head);
    const summary = kvEl([
      ["采用策略", run.profile_display_name || run.profile],
      ["使用因子", factorSummary({
        ...profile.factors,
        items: (profile.factors?.items || []).map((item) => ({
          ...item,
          name: run.factor_display_names?.[item.name] || item.name,
        })),
      })],
      ["回测区间", `${run.config?.config?.start || "?"} ~ ${run.config?.config?.end || "?"}`],
      ["初始资金", fmtNum(run.config?.config?.initial_capital, 0)],
      ["账户", run.config?.config?.account_name || "通用研究"],
      ["科创板权限", run.config?.config?.star_market_enabled === false ? "未开通（已排除）" : "包含科创板"],
      ["最近更新", fmtTime(run.status?.updated_at)],
    ]);
    summary.classList.add("backtest__summary");
    card.append(summary);
    detailHost.append(card);

    // ---- 指标卡矩阵
    const metrics = run.metrics || {};
    if (Object.keys(metrics).length) {
      const metricsCard = el("article", "card detail-card backtest__metrics");
      metricsCard.append(el("h3", "", "回测指标"));
      const window = metrics.window || {};
      if (window.start && window.end) metricsCard.append(el("p", "backtest__metric-window item-row__meta",
        `实际交易区间 ${window.start} 至 ${window.end} · ${window.trading_days} 个交易日 · 年化按 ${window.trading_days_per_year} 日计算`));
      const grid = el("div", "metric-groups");
      for (const [group, title] of METRIC_GROUPS) {
        const values = metrics[group];
        if (!values) continue;
        const g = el("div", "metric-group");
        g.dataset.metricGroup = group;
        g.append(el("h4", "", title));
        const items = el("div", "metric-group__items");
        for (const [key, value] of Object.entries(values)) {
          if (key === "status_counts") continue;
          items.append(metricItem(key, value));
        }
        g.append(items);
        if (group === "orders" && values.status_counts) {
          g.append(
            barChart({
              items: Object.entries(values.status_counts).map(([key, value]) => ({
                label: ORDER_STATUS_LABEL[key] || key,
                value,
                color: key === "filled" ? "var(--safe)" : "var(--warn)",
              })),
            }),
          );
        }
        grid.append(g);
      }
      metricsCard.append(grid);
      detailHost.append(metricsCard);
    } else {
      card.append(el("p", "item-row__meta", "暂无指标"));
    }

    // ---- 净值与回撤
    if (nav && nav.items.length) {
      const labels = nav.items.map((r) => fmtDate(r.date));
      comparisonChart = returnComparison({run, nav, benchmark, visibility: curveVisibility,
        onRetry: () => loadDetail(run.run_id, true)});
      detailHost.append(comparisonChart.node);
      const drawdownCard = el("article", "card detail-card");
      drawdownCard.append(el("h3", "", "回撤"));
      drawdownCard.append(
        areaChart({ values: nav.items.map((r) => Number(r.drawdown) || 0), labels, height: 140 }),
      );
      detailHost.append(drawdownCard);

      // ---- 月度收益热力表
      if ((nav.monthly_returns || []).length) {
        const monthlyCard = el("article", "card detail-card");
        monthlyCard.append(el("h3", "", "月度收益"));
        const heat = el("div", "heatmap");
        for (const m of nav.monthly_returns) {
          const cell = el("div", "heatmap__cell");
          
          const pct = Math.min(60, 18 + Math.abs(m.value) * 350);
          const tone = m.value >= 0 ? "var(--up)" : "var(--down)";
          cell.style.background = `color-mix(in srgb, ${tone} ${pct.toFixed(0)}%, transparent)`;
          cell.append(el("span", "heatmap__month", m.month));
          cell.append(el("span", `heatmap__value ${signClass(m.value)}`, fmtPct(m.value, 1)));
          heat.append(cell);
        }
        monthlyCard.append(heat);
        detailHost.append(monthlyCard);
      }
    }

    
    detailHost.append(buildTradesCard(run.run_id));
    detailHost.append(buildPositionsCard(run.run_id));
    const request = detailRequest;
    buildAnalysisCard(run.run_id).then((node) => {
      if (node && currentId === run.run_id && request === detailRequest) detailHost.append(node);
    });
  }

  
  function buildTradesCard(runId) {
    const card = el("article", "card detail-card");
    card.append(el("h4", "", "交易明细"));
    card.append(remoteTableEl(
      [
        { key: "date", label: "日期", format: (v) => fmtDate(v) },
        { key: "instrument", label: "标的" },
        {
          key: "side",
          label: "方向",
          format: (v) => badge(SIDE_LABEL[v] || v, v === "buy" ? "accent" : "muted"),
        },
        {
          key: "status",
          label: "状态",
          format: (v) => badge(ORDER_STATUS_LABEL[v] || v, v === "filled" ? "safe" : "warn"),
        },
        { key: "target_weight", label: "目标权重", num: true, format: (v) => fmtPct(v, 2) },
        { key: "shares", label: "股数", num: true, format: (v) => fmtNum(v, 0) },
        { key: "exec_price", label: "成交价", num: true, format: (v) => fmtNum(v, 3) },
        { key: "amount", label: "金额", num: true, format: (v) => fmtNum(v, 0) },
        { key: "note", label: "备注" },
      ],
      params => cachedResource(`trades:${runId}:${signature(runId)}:${params.limit}:${params.offset}`,
        () => ctx.api.backtestTrades(runId, params)),
      {pageSize: TRADES_PAGE},
    ));
    return card;
  }

  
  function buildPositionsCard(runId) {
    const card = el("article", "card detail-card backtest__positions");
    const head = el("div", "detail-card__head");
    head.append(el("h4", "", "持仓快照"));
    const dateHost = el("div", "backtest__date-select");
    const dateInput = el("input", "input");
    dateInput.type = 'date';
    dateInput.required = true;
    dateInput.disabled = true;
    dateInput.setAttribute('aria-label', '持仓快照日期');
    dateHost.append(dateInput);
    head.append(dateHost);
    const host = el("div");
    card.append(head, host);

    let selectedDate = '';
    dateInput.addEventListener('change', () => {
      const value = dateInput.value;
      if (value && value >= dateInput.min && value <= dateInput.max) loadDay(value);
      else dateInput.value = selectedDate;
    });
    let dayRequest = 0;
    async function loadDay(date) {
      const request = ++dayRequest;
      // Preserve the document height across loading and shorter/empty snapshots.
      host.style.minHeight = `${host.getBoundingClientRect().height}px`;
      host.replaceChildren(el('p', 'item-row__meta', '正在读取持仓…'));
      host.setAttribute('aria-busy', 'true');
      try {
        const params = {date, limit: 10, offset: 0};
        const data = await cachedResource(`positions:${runId}:${signature(runId)}:${date}:10:0`,
          () => ctx.api.backtestPositions(runId, params));
        if (request !== dayRequest) return;
        const dates = (data.dates || []).map(fmtDate).sort();
        selectedDate = data.date ? fmtDate(data.date) : '';
        dateInput.value = selectedDate;
        dateInput.min = data.date_range?.start || dates[0] || '';
        dateInput.max = data.date_range?.end || dates.at(-1) || '';
        dateInput.disabled = !dates.length;
        configureDatePicker(dateInput, {highlightedDates: data.adjustment_dates || []});
        // The shared calendar enhances dynamically mounted inputs before data arrives.
        dateHost.querySelector('.date-input__toggle')?.toggleAttribute('disabled', dateInput.disabled);
        host.replaceChildren(remoteTableEl([
          { key: "instrument", label: "标的", format: (code, row) => {
            const stock = el('span', 'backtest__stock');
            stock.append(el('span', '', row.stock_name || code));
            if (row.stock_name) stock.append(el('span', 'backtest__stock-code', code));
            return stock;
          } },
          { key: "weight", label: "权重", num: true, format: (v) => fmtPct(v, 2) },
          { key: "market_value", label: "市值", num: true, format: (v) => fmtNum(v, 0) },
          { key: "shares_nominal", label: "股数", num: true, format: (v) => fmtNum(v, 0) },
        ], page => cachedResource(`positions:${runId}:${signature(runId)}:${data.date}:${page.limit}:${page.offset}`,
          () => ctx.api.backtestPositions(runId, {...page, date: data.date})), {initialData: data}));
        if (data.snapshot_date && fmtDate(data.snapshot_date) !== selectedDate) {
          host.prepend(el('p', 'item-row__meta backtest__snapshot-note', `非交易日，显示 ${fmtDate(data.snapshot_date)} 收盘持仓`));
        }
      } catch (err) {
        if (request !== dayRequest) return;
        const errorHost = el('div');
        host.replaceChildren(errorHost);
        renderState(errorHost, {kind: 'error', title: '持仓加载失败', detail: err.message, retry: () => loadDay(date)});
      } finally {
        if (request === dayRequest) host.setAttribute('aria-busy', 'false');
      }
    }
    loadDay("");
    return card;
  }

  
  async function buildAnalysisCard(runId) {
    try {
      if (!experiments) experiments = ctx.api.experiments().then(data => data.items).catch(error => { experiments = null; throw error; });
      const exp = (await experiments).find((e) => e.run_id === runId);
      if (!exp) return null;
      const detail = await cachedResource(`analysis:${exp.exp_id}`, () => ctx.api.experiment(exp.exp_id));
      const analyses = detail.events.filter((e) => e.event_type === "ai_analysis");
      const card = el("article", "card detail-card");
      const head = el("div", "detail-card__head");
      head.append(el("h4", "", "模型复盘"));
      head.append(badge(exp.exp_id, "accent"));
      if (exp.improved != null) {
        head.append(badge(exp.improved ? "有改善" : "未改善", exp.improved ? "safe" : "warn"));
      }
      card.append(head);
      if (!analyses.length) {
        card.append(
          el("p", "item-row__meta", "暂无复盘"),
        );
        return card;
      }
      const a = analyses[analyses.length - 1].payload;
      if (a.assessment) {
        card.append(el("h4", "", "整体评价"));
        card.append(el("p", "item-row__meta", a.assessment));
      }
      if (a.failure_reason) {
        card.append(el("h4", "", "失败原因 / 归因"));
        card.append(el("p", "item-row__meta", a.failure_reason));
      }
      if ((a.next_steps || []).length) {
        card.append(el("h4", "", "下一步建议"));
        for (const [i, step] of a.next_steps.entries()) {
          card.append(el("p", "item-row__meta", `${i + 1}. ${step}`));
        }
      }
      if ((a.open_questions || []).length) {
        card.append(el("h4", "", "待确认问题"));
        card.append(el("p", "item-row__meta", a.open_questions.join("；")));
      }
      if (a.model) card.append(el("p", "item-row__meta", a.model));
      return card;
    } catch {
      return null;
    }
  }
}

export async function mount(root, ctx) {
  await mountContent(root, ctx);
}
