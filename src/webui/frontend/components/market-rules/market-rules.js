import {labelText} from "../../core/labels.js";
import {renderState} from "../state-card/state-card.js";
import {formDialog} from "../confirm-dialog/form-dialog.js";
import {toastError} from "../../core/toast.js";
import {badge} from "../../core/fmt.js";
import {el, kvEl, tableEl as createTable} from "../../core/dom.js";
// Rule definitions are fixed reference tables, so show every row directly.
function tableEl(columns, rows) { return createTable(columns, rows, {pagination: false}); }
const DIRECTION_LABEL = { buy: "买入", sell: "卖出", both: "买卖双向" };
const SESSION_TYPE_LABEL = {
  call_auction: "集合竞价",
  continuous: "连续竞价",
  closing_auction: "收盘集合竞价",
  night: "夜盘",
};

function statusBadge(status) { return badge(({active:"已启用",disabled:"已停止",draft:"研究中"})[status] || status); }
function ruleHeading(key, title) {
  const heading = el("h4", "", title);
  heading.dataset.ruleSection = key;
  return heading;
}

function ruleSections(rule) {
  const nodes = [];

  const baseRows = [
    ["交收制度", rule.t_plus === undefined || rule.t_plus === null ? "" : `T+${rule.t_plus}`],
    ["最小交易单位", rule.min_unit],
    ["支持零碎股", rule.fractional_shares ? "是" : "否"],
    ["夜盘", rule.night_session ? "有" : "无"],
    ["时区", rule.timezone],
    ["交易日历来源", rule.calendar_source],
    ["节假日来源", rule.holidays_source],
    ["结算方式", rule.settlement],
    ["停牌处理", rule.suspension_handling],
    ["价格锚点", rule.price_anchor],
    ["回测锚点", rule.backtest_anchor],
    ["实盘窗口", rule.live_window],
    ["时段锚点", rule.session_anchor],
  ].filter(([, v]) => v !== "" && v !== undefined && v !== null);
  if (baseRows.length) {
    const descriptions = new Set(["交易日历来源", "节假日来源", "结算方式", "停牌处理", "实盘窗口", "时段锚点"]);
    const facts = kvEl(baseRows.filter(([label]) => !descriptions.has(label)));
    facts.classList.add('rule-overview__facts');
    const notes = kvEl(baseRows.filter(([label]) => descriptions.has(label)));
    notes.classList.add('rule-overview__notes');
    nodes.push(ruleHeading("overview", "交易规则"), facts, notes);
  }

  const sessions = rule.sessions || [];
  if (sessions.length) {
    nodes.push(
      ruleHeading("sessions", "交易时段"),
      tableEl(
        [
          { key: "name", label: "名称" },
          { key: "time", label: "时间" },
          { key: "type", label: "类型" },
          { key: "notes", label: "备注" },
        ],
        sessions.map((s) => ({
          name: s.name,
          time: `${s.start} - ${s.end}`,
          type: SESSION_TYPE_LABEL[s.session_type] || s.session_type,
          notes: s.notes || "",
        })),
      ),
    );
  }

  const pl = rule.price_limit;
  if (pl) {
    nodes.push(ruleHeading("limits", "涨跌停"));
    if (pl.enabled === false) {
      nodes.push(el("p", "item-row__meta", "该市场没有涨跌停限制。"));
    } else {
      const rows = [
        ["默认幅度", pl.default_pct === undefined || pl.default_pct === null ? "" : `${pl.default_pct}%`],
        ["上市首日", pl.first_day_rule],
        ["备注", pl.notes],
      ].filter(([, v]) => v);
      if (rows.length) nodes.push(kvEl(rows));
      const specials = pl.special_rules || [];
      if (specials.length) {
        nodes.push(
          tableEl(
            [
              { key: "condition", label: "特殊条件" },
              { key: "pct", label: "幅度", num: true },
              { key: "notes", label: "备注" },
            ],
            specials.map((s) => ({
              condition: s.condition,
              pct: `${s.limit_pct}%`,
              notes: s.notes || "",
            })),
          ),
        );
      }
    }
  }

  const quantityRules = rule.quantity_rules || [];
  if (quantityRules.length) {
    nodes.push(
      ruleHeading("quantity", "板块交易单位"),
      tableEl(
        [
          { key: "board", label: "板块" },
          { key: "min_unit", label: "最小单位", num: true },
          { key: "increment", label: "递增单位", num: true },
          { key: "notes", label: "备注" },
        ],
        quantityRules,
      ),
    );
  }

  nodes.push(...feeTaxSection("fees", "费用", rule.fees, true));
  nodes.push(...feeTaxSection("taxes", "税费", rule.taxes, false));

  const deadlineSchedule = rule.disclosure_deadlines || {};
  const deadlines = deadlineSchedule.items || [];
  if (deadlines.length) {
    nodes.push(
      ruleHeading("disclosures", "定期报告披露截止日"),
      tableEl(
        [
          { key: "period", label: "报告期截止" },
          { key: "deadline", label: "披露截止" },
          { key: "notes", label: "备注" },
        ],
        deadlines.map((d) => ({
          period: d.period_end,
          deadline: `${deadlineYearLabel(d.year_offset)} ${d.deadline}`,
          notes: d.notes || "",
        })),
      ),
    );
    if (deadlineSchedule.notes) nodes.push(el("p", "item-row__meta", deadlineSchedule.notes));
  }

  return nodes;
}

function deadlineYearLabel(offset) {
  if (!offset) return "当年";
  if (offset === 1) return "次年";
  return `${offset} 年后`;
}

function feeTaxSection(key, title, schedule, withLimits) {
  const items = schedule?.items || [];
  if (!items.length) return [];
  const cols = [
    { key: "name", label: "费目" },
    { key: "rate", label: "费率", num: true },
    { key: "direction", label: "方向" },
  ];
  if (withLimits) {
    cols.push({ key: "min", label: "最低", num: true }, { key: "max", label: "最高", num: true });
  }
  cols.push({ key: "notes", label: "备注" });
  const rows = items.map((it) => ({
    name: it.name,
    rate: it.rate,
    direction: DIRECTION_LABEL[it.direction] || it.direction,
    min: it.min_amount,
    max: it.max_amount,
    notes: it.notes || "",
  }));
  const nodes = [ruleHeading(key, title), tableEl(cols, rows)];
  if (schedule.notes) nodes.push(el("p", "item-row__meta", schedule.notes));
  return nodes;
}

export async function mount(root, ctx) {
  const hosts = {ruleList: root.querySelector('[data-rule-list]'), ruleDetail: root.querySelector('[data-rule-detail]')};
  let currentRule = '';
  ctx.bus.on('route', id => { if (id === 'settings') loadRules(); });
  await loadRules();
  async function loadRules() {
    await renderState(hosts.ruleList, { kind: "loading", title: "加载市场规则…" });
    try {
      const data = await ctx.api.marketRules();
      renderRuleList(data.items);
    } catch (err) {
      await renderState(hosts.ruleList, {
        kind: "error",
        title: "市场规则加载失败",
        detail: err.message,
        retry: () => loadRules(),
      });
    }
  }

  function renderRuleList(items) {
    hosts.ruleList.innerHTML = "";
    hosts.ruleList.hidden = items.length === 1;
    if (!items.length) {
      renderState(hosts.ruleList, {
        kind: "empty",
        title: "暂无市场规则",
        detail: "未找到 A 股市场规则，请检查 config/market_rules/CN-A-stock.yaml 配置。",
      });
      hosts.ruleDetail.innerHTML = "";
      return;
    }
    if (items.length === 1) {
      currentRule = items[0].name;
      renderRuleDetail(items[0]);
      return;
    }
    for (const item of items) {
      const row = el("button", "item-row");
      row.type = "button";
      row.dataset.name = item.name;
      if (item.name === currentRule) row.classList.add("is-active");
      row.append(el("span", "item-row__title", item.name));
      const meta = el("span", "item-row__meta");
      meta.append(statusBadge(item.status));
      row.append(meta);
      row.addEventListener("click", () => {
        currentRule = item.name;
        hosts.ruleList.querySelectorAll(".item-row").forEach((n) => {
          n.classList.toggle("is-active", n.dataset.name === currentRule);
        });
        renderRuleDetail(item);
      });
      hosts.ruleList.append(row);
    }
    const first = items.find((i) => i.name === currentRule) || items[0];
    currentRule = first.name;
    hosts.ruleList.querySelector(`[data-name="${currentRule}"]`)?.classList.add("is-active");
    renderRuleDetail(first);
  }

  function renderRuleDetail(rule) {
    hosts.ruleDetail.innerHTML = "";
    const card = el("article", "card detail-card");
    const head = el("div", "rule-detail__head");
    head.append(el("h3", "", rule.name));
    head.append(statusBadge(rule.status));
    card.append(head);

    const ops = el("div", "rule-detail__actions");
    const calcHost = el("div", "rule-detail__calculation");
    const feeBtn = el("button", "btn btn--ghost", "费用计算");
    feeBtn.type = "button";
    feeBtn.addEventListener("click", () => checkFees(rule, calcHost));
    const limitBtn = el("button", "btn btn--ghost", "涨跌停计算");
    limitBtn.type = "button";
    limitBtn.addEventListener("click", () => checkPriceLimit(rule, calcHost));
    ops.append(feeBtn, limitBtn);
    head.append(ops);
    card.classList.add("rule-detail");
    card.append(calcHost);

    const sections = el("div", "rule-detail__sections");
    const byKey = new Map();
    let section;
    for (const node of ruleSections(rule)) {
      if (node.tagName === "H4") {
        section = el("section", "rule-detail__section");
        section.dataset.ruleSection = node.dataset.ruleSection;
        byKey.set(node.dataset.ruleSection, section);
      }
      section.append(node);
    }
    for (const keys of [["overview"], ["sessions", "quantity"], ["limits"], ["fees", "taxes"], ["disclosures"]]) {
      const items = keys.map(key => byKey.get(key)).filter(Boolean);
      if (!items.length) continue;
      if (items.length === 1) sections.append(items[0]);
      else {
        const group = el("div", "rule-detail__group");
        group.append(...items);
        sections.append(group);
      }
    }
    card.append(sections);
    hosts.ruleDetail.append(card);
  }

  async function checkFees(rule, host) {
    try {
      const values = await formDialog({
        title: `费用计算（${labelText(rule.name)}）`,
        submitLabel: "计算",
        fields: [
          { key: "amount", label: "成交金额", required: true, placeholder: "如 100000" },
          {
            key: "direction",
            label: "方向",
            kind: "select",
            options: [
              { value: "buy", label: "买入" },
              { value: "sell", label: "卖出" },
            ],
          },
        ],
      });
      if (!values) return;
      const data = await ctx.api.marketRuleCheckFees(rule.name, {
        amount: values.amount,
        direction: values.direction,
      });
      host.innerHTML = "";
      host.append(
        el(
          "h4",
          "",
          `费用明细（${DIRECTION_LABEL[data.direction] || data.direction} ${data.amount}）`,
        ),
      );
      host.append(
        tableEl(
          [
            { key: "name", label: "费目" },
            { key: "amount", label: "金额", num: true },
            { key: "notes", label: "说明" },
          ],
          data.items,
        ),
      );
      host.append(el("p", "item-row__meta", `总费用：${data.total}`));
    } catch (err) {
      toastError(ctx.bus, err, "费用计算失败");
    }
  }

  async function checkPriceLimit(rule, host) {
    try {
      const values = await formDialog({
        title: `涨跌停计算（${labelText(rule.name)}）`,
        submitLabel: "计算",
        fields: [
          { key: "prev_close", label: "前收盘价", required: true, placeholder: "如 10.00" },
          { key: "condition", label: "特殊条件（可留空）", placeholder: "如 st / 上市首日" },
        ],
      });
      if (!values) return;
      const data = await ctx.api.marketRuleCheckPriceLimit(rule.name, {
        prev_close: values.prev_close,
        condition: values.condition,
      });
      host.innerHTML = "";
      host.append(el("h4", "", "涨跌停计算"));
      const metrics = kvEl([
        ["前收盘价", data.prev_close],
        ["幅度", `${data.limit_pct}%`],
        ["涨停价", data.upper],
        ["跌停价", data.lower],
      ]);
      metrics.classList.add("rule-calculation__metrics");
      const notes = kvEl([
        ["首日规则", data.first_day_rule || "（无）"],
        ["特殊条件", (data.conditions || []).join("、") || "（无）"],
      ]);
      notes.classList.add("rule-calculation__notes");
      host.append(metrics, notes);
    } catch (err) {
      toastError(ctx.bus, err, "涨跌停计算失败");
    }
  }

}
