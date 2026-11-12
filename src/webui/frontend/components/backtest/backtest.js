import { renderState } from "../state-card/state-card.js";
import { createSelect } from "../../core/select.js";
import { areaChart, barChart, lineChart } from "../../core/charts.js";
import { badge, fmtDate, fmtNum, fmtPct, fmtTime, signClass } from "../../core/fmt.js";
import { el, kvEl, selectMarkup, tableEl } from "../../core/dom.js";

const TRADES_PAGE = 100;


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
  ["window", "回测窗口"],
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

export async function mount(root, ctx) {
  const listHost = root.querySelector("[data-list]");
  const detailHost = root.querySelector("[data-detail]");

  let currentId = ctx.backtestRun || "";
  let experiments = null;

  ctx.bus.on("route", (id) => {
    if (id === "backtest") load();
  });
  
  ctx.bus.on("backtest:open", (runId) => {
    currentId = runId;
  });
  await load();

  function factorSummary(factors) {
    const items = factors?.items || [];
    if (!items.length) return "";
    const names = items.map((f) => (f.weight != null ? `${f.name}（权重 ${f.weight}）` : f.name));
    const tail = factors.weighting === "equal" && items.length > 1 ? "，等权合成" : "";
    return `${names.join("、")}${tail}`;
  }

  async function load() {
    await renderState(listHost, { kind: "loading", title: "加载回测运行…" });
    experiments = null;
    try {
      const data = await ctx.api.backtestRuns();
      renderList(data.items);
    } catch (err) {
      await renderState(listHost, {
        kind: "error",
        title: "回测运行加载失败",
        detail: err.message,
        retry: () => load(),
      });
    }
  }

  function renderList(items) {
    listHost.innerHTML = "";
    if (!items.length) {
      renderState(listHost, {
        kind: "empty",
        title: "暂无回测运行",
        detail: "在“自动研究”启动实验后，可在此查看回测。",
      });
      detailHost.innerHTML = "";
      return;
    }
    
    const groups = new Map();
    for (const item of items) {
      if (!groups.has(item.profile)) groups.set(item.profile, []);
      groups.get(item.profile).push(item);
    }
    for (const [profile, runs] of groups) {
      const groupEl = el("div", "item-list__group");
      const displayName = runs[0].profile_display_name || profile;
      groupEl.append(el("span", "", displayName));
      if (runs[0].archived) groupEl.append(badge("历史归档", "muted"));
      listHost.append(groupEl);
      const factorNames = runs[0].factor_display_names || [];
      if (factorNames.length) {
        groupEl.append(el("span", "item-list__group-sub", `使用因子：${factorNames.join("、")}`));
      }
      for (const item of runs) {
        const row = el("button", "item-row");
        row.type = "button";
        row.dataset.runId = item.run_id;
        if (item.run_id === currentId) row.classList.add("is-active");
        row.append(el("span", "item-row__title", item.run_id));
        const meta = el("span", "item-row__meta");
        meta.append(runStateBadge(item.state), el("span", "", `${item.start} ~ ${item.end}`));
        if (item.protocol_locked === false) meta.append(badge("协议未锁定", "warn"));
        row.append(meta);
        row.addEventListener("click", () => {
          currentId = item.run_id;
          listHost.querySelectorAll(".item-row").forEach((n) => {
            n.classList.toggle("is-active", n.dataset.runId === currentId);
          });
          loadDetail(item.run_id);
        });
        listHost.append(row);
      }
    }
    if (!currentId || !items.some((i) => i.run_id === currentId)) {
      currentId = items[0].run_id;
      listHost.querySelector(".item-row")?.classList.add("is-active");
    }
    loadDetail(currentId);
  }

  async function loadDetail(runId) {
    await renderState(detailHost, { kind: "loading", title: "加载运行详情…" });
    try {
      const [run, nav] = await Promise.all([
        ctx.api.backtestRun(runId),
        ctx.api.backtestNav(runId).catch(() => null),
      ]);
      renderDetail(run, nav);
    } catch (err) {
      await renderState(detailHost, {
        kind: "error",
        title: "运行详情加载失败",
        detail: err.message,
        retry: () => loadDetail(runId),
      });
    }
  }

  function metricItem(key, value) {
    const def = METRIC_DEFS[key] || [key, null, false];
    const item = el("div", "metric");
    item.append(el("span", "metric__label", def[0]));
    const text = value == null ? "—" : def[1] ? def[1](value) : String(value);
    const val = el("span", "metric__value", text);
    if (def[2]) val.classList.add(signClass(value));
    item.append(val);
    return item;
  }

  function renderDetail(run, nav) {
    detailHost.innerHTML = "";
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
    for (const f of profile.factors?.items || []) {
      head.append(badge(`因子 ${run.factor_display_names?.[f.name] || f.name}`));
    }
    if (run.config && run.config.protocol_locked === false) {
      head.append(badge("协议未锁定", "warn"));
    }
    card.append(head);
    const timescale = profile.timescale === "daily" ? "日频" : profile.timescale;
    card.append(
      kvEl([
        ["采用策略", run.profile_display_name || run.profile],
        ["使用因子", factorSummary({
          ...profile.factors,
          items: (profile.factors?.items || []).map((item) => ({
            ...item,
            name: run.factor_display_names?.[item.name] || item.name,
          })),
        })],
        ["市场 / 频率", profile.market ? `${profile.market} · ${timescale || "?"}` : null],
        ["回测区间", `${run.config?.config?.start || "?"} ~ ${run.config?.config?.end || "?"}`],
        ["初始资金", fmtNum(run.config?.config?.initial_capital, 0)],
        ["config_hash", run.config?.config_hash],
        ["protocol_hash", run.config?.protocol_hash],
        ["最近更新", fmtTime(run.status?.updated_at)],
      ]),
    );

    // ---- 指标卡矩阵
    const metrics = run.metrics || {};
    if (Object.keys(metrics).length) {
      const grid = el("div", "metric-groups");
      for (const [group, title] of METRIC_GROUPS) {
        const values = metrics[group];
        if (!values) continue;
        const g = el("div", "card metric-group");
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
      card.append(grid);
    } else {
      card.append(el("p", "item-row__meta", "暂无指标，请查看运行状态。"));
    }

    // ---- 净值与回撤
    if (nav && nav.items.length) {
      card.append(el("h4", "", `净值曲线（${nav.total_rows} 个交易日）`));
      const labels = nav.items.map((r) => fmtDate(r.date));
      const nav0 = Number(nav.items[0].nav) || 1;
      const series = [
        {
          label: "策略（归一化）",
          color: "var(--accent)",
          values: nav.items.map((r) => (r.nav == null ? null : Number(r.nav) / nav0)),
        },
      ];
      if (nav.items[0].benchmark_nav != null) {
        series.push({
          label: "基准",
          color: "var(--warn)",
          values: nav.items.map((r) => (r.benchmark_nav == null ? null : Number(r.benchmark_nav))),
        });
      }
      card.append(lineChart({ series, labels, height: 280 }));
      card.append(el("h4", "", "回撤"));
      card.append(
        areaChart({ values: nav.items.map((r) => Number(r.drawdown) || 0), labels, height: 140 }),
      );

      // ---- 月度收益热力表
      if ((nav.monthly_returns || []).length) {
        card.append(el("h4", "", "月度收益"));
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
        card.append(heat);
      }
    }

    detailHost.append(card);

    
    detailHost.append(buildTradesCard(run.run_id));
    detailHost.append(buildPositionsCard(run.run_id));
    buildAnalysisCard(run.run_id).then((node) => {
      if (node) detailHost.append(node);
    });
  }

  
  function buildTradesCard(runId) {
    const card = el("article", "card detail-card");
    card.append(el("h4", "", "交易明细"));
    const host = el("div");
    const bar = el("div", "detail-card__head");
    const more = el("button", "btn btn--ghost", "加载更多");
    more.type = "button";
    const counter = el("span", "item-row__meta");
    bar.append(more, counter);
    card.append(host, bar);

    let offset = 0;
    let rows = [];
    async function loadPage() {
      more.disabled = true;
      try {
        const data = await ctx.api.backtestTrades(runId, { limit: TRADES_PAGE, offset });
        rows = rows.concat(data.items);
        offset = rows.length;
        host.innerHTML = "";
        if (!rows.length) {
          host.append(el("p", "item-row__meta", "无交易记录。"));
          bar.hidden = true;
          return;
        }
        host.append(
          tableEl(
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
            rows,
          ),
        );
        counter.textContent = `已显示 ${rows.length} / ${data.total_rows} 条`;
        more.hidden = rows.length >= data.total_rows;
      } catch (err) {
        host.innerHTML = "";
        host.append(el("p", "item-row__meta", `交易明细加载失败：${err.message}`));
      } finally {
        more.disabled = false;
      }
    }
    more.addEventListener("click", () => loadPage());
    loadPage();
    return card;
  }

  
  function buildPositionsCard(runId) {
    const card = el("article", "card detail-card");
    const head = el("div", "detail-card__head");
    head.append(el("h4", "", "持仓快照"));
    const selectHost = el("div", "select backtest__date-select");
    selectHost.innerHTML = selectMarkup("选择日期");
    head.append(selectHost);
    const host = el("div");
    card.append(head, host);

    const dateSelect = createSelect(selectHost, { onChange: (d) => loadDay(d) });
    async function loadDay(date) {
      host.innerHTML = "";
      try {
        const data = await ctx.api.backtestPositions(runId, date ? { date } : {});
        if (!dateSelect.value && data.dates.length) {
          dateSelect.setOptions(data.dates.slice().reverse(), data.date);
        }
        if (!data.items.length) {
          host.append(el("p", "item-row__meta", "该日无持仓。"));
          return;
        }
        host.append(
          tableEl(
            [
              { key: "instrument", label: "标的" },
              { key: "weight", label: "权重", num: true, format: (v) => fmtPct(v, 2) },
              { key: "market_value", label: "市值", num: true, format: (v) => fmtNum(v, 0) },
              { key: "shares_nominal", label: "股数", num: true, format: (v) => fmtNum(v, 0) },
            ],
            data.items,
          ),
        );
      } catch (err) {
        host.append(el("p", "item-row__meta", `持仓加载失败：${err.message}`));
      }
    }
    loadDay("");
    return card;
  }

  
  async function buildAnalysisCard(runId) {
    try {
      if (!experiments) experiments = (await ctx.api.experiments()).items;
      const exp = experiments.find((e) => e.run_id === runId);
      if (!exp) return null;
      const detail = await ctx.api.experiment(exp.exp_id);
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
          el("p", "item-row__meta", "暂无模型复盘。评价完成后自动生成。"),
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
