import {labelText} from "../../core/labels.js";
import { hydrateIcons, iconEl } from "../../core/icons.js";
import { runWithConfirm } from "../confirm-dialog/confirm-dialog.js";
import { toastError } from "../../core/toast.js";
import { fmtTime } from "../../core/fmt.js";

const RING_RADIUS = 48;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;


const STAT_CARDS = [
  { key: "issues", label: "开放事项", icon: "doc", route: "issues", load: (api) => api.issues() },
  { key: "factors", label: "注册因子", icon: "spark", route: "factors", load: (api) => api.factors() },
  {
    key: "backtest",
    label: "回测运行",
    icon: "chart",
    route: "backtest",
    load: (api) => api.backtestRuns(),
  },
  {
    key: "proposals",
    label: "待评审候选",
    icon: "check",
    route: "review",
    load: (api) => api.proposals(),
  },
];

const SESSION_LABELS = {
  active_market: "交易市场",
  active_instrument_class: "标的类别",
  last_login: "上次登录",
  commission_rate: "佣金费率",
  last_data_update: "上次数据更新",
};

const OBJECT_LABELS = {
  data_snapshot: "研究数据",
  research_program: "研究任务",
  factor_version: "因子版本",
  factor_evaluation: "因子评价",
  strategy_version: "策略版本",
  evaluation_run: "评价运行",
  locked_test_exposure: "锁定测试",
  champion_decision: "正式策略决策",
  portfolio_plan: "调仓计划",
  execution_record: "成交记录",
  attribution_record: "收益归因",
  learning_event: "研究经验",
  research_report: "研究报告",
};

const STATE_LABELS = {
  ready: "数据可用",
  quarantined: "暂停使用",
  superseded: "已被更新",
  draft: "草稿",
  queued: "排队中",
  running: "执行中",
  paused: "已暂停",
  frozen: "候选已冻结",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  proposed: "待评价",
  validating: "检查中",
  evaluated: "已评价",
  invalid: "无效",
  eliminated: "已淘汰",
  shadow: "影子观察中",
  production: "已批准使用",
  watching: "持续观察中",
  disabled: "已停用",
  recorded: "已记录",
  reviewed: "已复核",
  invalidated: "已作废",
  evaluating: "评价中",
  rejected: "未通过",
  challenger: "挑战者",
  champion: "正式策略",
  rolled_back: "已回滚",
  retired: "已退出",
  planned: "已计划",
  reserved: "已预留",
  opened: "已开封",
  approved: "已批准",
  applied: "已执行",
  confirmed: "已确认",
  active: "执行中",
  expired: "已过期",
  partially_executed: "部分成交",
  executed: "已成交",
  received: "已收到",
  reconciled: "已核对",
  disputed: "待核对",
  attributed: "已完成归因",
  validated: "已校验",
  observed: "已发现",
  verified: "已核实",
  accepted: "已采纳",
};

const ACTOR_LABELS = {
  ai: "AI",
  system: "系统",
  broker_adapter: "券商回报",
  human: "人工",
  mixed: "人工与系统",
};

function countOf(data) {
  if (Array.isArray(data)) return data.length;
  if (Array.isArray(data?.items)) return data.items.length;
  if (typeof data?.total === "number") return data.total;
  return null;
}

export async function mount(root, ctx) {
  await hydrateIcons(root);

  const ring = root.querySelector("[data-readiness-ring]");
  const ringValue = root.querySelector("[data-readiness-value]");
  const chipsHost = root.querySelector("[data-chips]");
  const dirsHost = root.querySelector("[data-dirs]");
  const diagnostic = root.querySelector("[data-diagnostic]");
  const statCards = root.querySelector("[data-stat-cards]");
  const sessionHost = root.querySelector("[data-session]");
  const envHost = root.querySelector("[data-env]");
  const lifecycleHost = root.querySelector("[data-lifecycle]");
  const lifecycleNote = root.querySelector("[data-lifecycle-note]");
  const auditHost = root.querySelector("[data-audit-events]");

  ring.style.strokeDasharray = `${RING_CIRCUMFERENCE}`;
  ring.style.strokeDashoffset = `${RING_CIRCUMFERENCE}`;

  let countsLoading = false;

  ctx.bus.on("health", (data) => renderHealth(data));
  ctx.bus.on("route", (id) => {
    if (id === "overview") {
      loadCounts();
      loadLifecycle();
      loadAuditEvents();
    }
  });
  loadCounts();
  loadLifecycle();
  loadAuditEvents();

  function renderHealth(data) {
    const reachable = data != null;
    const dirs = data?.workspace?.dirs || [];
    const dirTotal = dirs.length;
    const dirOk = dirs.filter((d) => d.exists).length;
    const matrixOk = data?.workspace?.matrix_file_exists !== false;
    const pkgs = Object.entries(data?.packages || {});
    const pkgOk = pkgs.filter(([, version]) => version).length;

    const total = dirTotal + pkgs.length + 1 || 1;
    const okCount = dirOk + pkgOk + (matrixOk ? 1 : 0);
    const readiness = reachable ? okCount / total : 0;
    const missingDirs = dirs.filter((d) => !d.exists);
    const missingPkgs = pkgs.filter(([, version]) => !version);
    const state = !reachable || !matrixOk
      ? "bad"
      : missingDirs.length || missingPkgs.length
        ? "warn"
        : "ok";

    ring.dataset.state = state;
    ring.style.strokeDashoffset = `${RING_CIRCUMFERENCE * (1 - readiness)}`;
    ringValue.textContent = `${Math.round(readiness * 100)}%`;

    chipsHost.innerHTML = "";
    if (reachable) {
      chipsHost.append(
        chipItem(missingDirs.length ? "warn" : "ok", "目录", `${dirOk}/${dirTotal}`),
        chipItem(matrixOk ? "ok" : "bad", "任务配置", matrixOk ? "正常" : "缺失"),
        chipItem(missingPkgs.length ? "warn" : "ok", "依赖包", `${pkgOk}/${pkgs.length}`),
      );
    }

    
    dirsHost.innerHTML = "";
    for (const dir of missingDirs) dirsHost.append(moduleItem("pending", dir.path, "缺失"));
    for (const [name] of missingPkgs) dirsHost.append(moduleItem("pending", name, "未安装"));
    dirsHost.hidden = !dirsHost.childElementCount;

    diagnostic.hidden = state === "ok";
    diagnostic.dataset.state = state;
    diagnostic.replaceChildren(
      document.createTextNode(
        diagnosticText({ reachable, matrixOk, missing: missingDirs.length, pkgMissing: missingPkgs.length }),
      ),
    );
    if (reachable && matrixOk && missingDirs.length) {
      const fixBtn = document.createElement("button");
      fixBtn.type = "button";
      fixBtn.className = "btn btn--primary";
      fixBtn.textContent = "补齐目录";
      fixBtn.addEventListener("click", () => fixDirs(fixBtn));
      diagnostic.append(fixBtn);
    }

    renderSession(data?.session);
    renderEnv(data);
  }

  function chipItem(state, label, value) {
    const chip = document.createElement("span");
    chip.className = "overview__chip";
    chip.dataset.state = state;
    const dot = document.createElement("span");
    dot.className = "overview__chip-dot";
    const name = document.createElement("span");
    name.textContent = label;
    const val = document.createElement("strong");
    val.textContent = value;
    chip.append(dot, name, val);
    return chip;
  }

  function moduleItem(state, text, stateLabel) {
    const item = document.createElement("div");
    item.className = "overview__module";
    item.dataset.state = state;
    const dot = document.createElement("span");
    dot.className = "overview__module-dot";
    const label = document.createElement("strong");
    label.textContent = text;
    label.title = text;
    const stateText = document.createElement("span");
    stateText.textContent = stateLabel;
    item.append(dot, label, stateText);
    return item;
  }

  async function fixDirs(btn) {
    btn.disabled = true;
    try {
      const done = await runWithConfirm({
        title: "补齐工作区目录",
        message:
          "补齐数据、结果和记忆目录；" +
          "已有文件保持不变。",
        confirmLabel: "确认补齐",
        execute: (extra) => ctx.api.doctorInit({ ...extra }),
      });
      if (done) {
        ctx.bus.emit("toast", {
          kind: "success",
          title: "目录补齐完成",
          detail: `新建目录 ${done.created_dirs.length} 个，补写 .gitkeep ${done.gitkeep_written} 个。`,
        });
        const fresh = await ctx.api.status();
        ctx.bus.emit("health", fresh);
      }
    } catch (err) {
      toastError(ctx.bus, err, "目录补齐失败");
    } finally {
      btn.disabled = false;
    }
  }

  function diagnosticText({ reachable, matrixOk, missing, pkgMissing }) {
    if (!reachable) return "服务无响应，请重新打开 QuantLens。";
    if (!matrixOk) return "任务配置配置缺失，模型功能不可用。";
    const parts = [];
    if (missing) parts.push(`${missing} 个目录缺失`);
    if (pkgMissing) parts.push(`${pkgMissing} 个依赖包未安装`);
    return parts.length ? `${parts.join("，")}。` : "工作区就绪。";
  }

  function renderSession(session) {
    sessionHost.innerHTML = "";
    if (!session) {
      sessionHost.textContent = "会话信息不可用。";
      return;
    }
    for (const [key, label] of Object.entries(SESSION_LABELS)) {
      const k = document.createElement("dt");
      k.textContent = label;
      const v = document.createElement("dd");
      v.textContent = session[key] ? labelText(session[key]) : "未设置";
      const row = document.createElement("div");
      row.className = "kv__row"; k.className = "kv__key"; v.className = "kv__val";
      row.append(k, v); sessionHost.append(row);
    }
  }

  function renderEnv(data) {
    envHost.innerHTML = "";
    if (!data) {
      envHost.textContent = "环境信息不可用。";
      return;
    }
    
    const rows = [
      ["框架版本", data.version],
      ["运行环境版本", data.python],
      ["工作区根目录", data.workspace?.root],
    ];
    for (const [key, value] of rows) {
      const k = document.createElement("dt");
      k.textContent = key;
      const v = document.createElement("dd");
      v.textContent = value ?? "—";
      const row = document.createElement("div");
      row.className = "kv__row"; k.className = "kv__key"; v.className = "kv__val";
      row.append(k, v); envHost.append(row);
    }
  }

  async function loadLifecycle() {
    try {
      const [summary, integrity] = await Promise.all([
        ctx.api.lifecycleSummary(),
        ctx.api.lifecycleIntegrity(),
      ]);
      lifecycleHost.innerHTML = "";
      const rows = [
        ["已保存的研究与实盘记录", summary.total_objects],
        ["状态变化与人工审批", summary.total_events],
        ["通过完整性检查的记录", integrity.checked_objects],
        ["资料齐全的记录", integrity.checked_references],
        ["可还原的状态变化", integrity.checked_events],
        ["已建立的正式策略席位", integrity.champion_slots],
      ];
      for (const [key, value] of rows) {
        const item = document.createElement("div");
        item.className = "overview__lifecycle-stat";
        const v = document.createElement("strong");
        v.textContent = String(value ?? "—");
        const k = document.createElement("span");
        k.textContent = key;
        item.append(v, k);
        lifecycleHost.append(item);
      }
      lifecycleNote.textContent = summary.total_objects
        ? "统计来自已保存记录，可按时间查看状态变化。"
        : "暂无研究记录。训练后可在此查看数据、版本、评价与审批。";
    } catch (err) {
      lifecycleHost.textContent = `研究账本校验失败：${err.message}`;
      lifecycleNote.textContent = "";
    }
  }

  async function loadAuditEvents() {
    try {
      const data = await ctx.api.lifecycleEvents(100);
      const items = data.items || [];
      auditHost.innerHTML = "";
      if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "overview__audit-empty";
        const title = document.createElement("strong");
        title.textContent = "暂无待复核记录";
        const detail = document.createElement("p");
        detail.textContent = "研究后可查看以下记录：";
        const steps = document.createElement("div");
        steps.className = "overview__audit-steps";
        [
          ["1", "研究运行", "记录数据口径、版本和评价结果"],
          ["2", "人工决定", "记录策略晋升、回滚和批准理由"],
          ["3", "实盘反馈", "记录成交核对、偏差和收益归因"],
        ].forEach(([index, label, description]) => {
          const step = document.createElement("div");
          step.className = "overview__audit-step";
          const number = document.createElement("span");
          number.textContent = index;
          const copy = document.createElement("div");
          const name = document.createElement("strong");
          name.textContent = label;
          const text = document.createElement("small");
          text.textContent = description;
          copy.append(name, text);
          step.append(number, copy);
          steps.append(step);
        });
        empty.append(title, detail, steps);
        auditHost.append(empty);
        return;
      }
      const list = document.createElement("div");
      list.className = "overview__event-list";
      for (const item of items.slice(0, 8)) {
        const event = document.createElement("article");
        event.className = "overview__event";
        const head = document.createElement("div");
        head.className = "overview__event-head";
        const object = document.createElement("strong");
        object.textContent = OBJECT_LABELS[item.object_type] || item.object_type;
        const time = document.createElement("time");
        time.textContent = fmtTime(item.created_at);
        head.append(object, time);
        const state = document.createElement("p");
        state.className = "overview__event-state";
        state.textContent = `${STATE_LABELS[item.from_state] || item.from_state} → ${STATE_LABELS[item.to_state] || item.to_state}`;
        const meta = document.createElement("p");
        meta.className = "overview__event-meta";
        const actor = `${ACTOR_LABELS[item.actor_type] || item.actor_type}${item.actor_ref ? `：${item.actor_ref}` : ""}`;
        meta.textContent = item.reason ? `${actor} · ${item.reason}` : actor;
        event.append(head, state, meta);
        list.append(event);
      }
      auditHost.append(list);
    } catch (err) {
      auditHost.textContent = `审计事件加载失败：${err.message}`;
    }
  }

  async function loadCounts() {
    if (countsLoading) return;
    countsLoading = true;
    try {
      const results = await Promise.allSettled(STAT_CARDS.map((c) => c.load(ctx.api)));
      statCards.innerHTML = "";
      for (let i = 0; i < STAT_CARDS.length; i++) {
        const meta = STAT_CARDS[i];
        const result = results[i];
        const count = result.status === "fulfilled" ? countOf(result.value) : null;

        const card = document.createElement("article");
        card.className = "card overview__stat is-clickable";
        card.tabIndex = 0;
        card.addEventListener("click", () => ctx.bus.emit("route", meta.route));

        const ic = await iconEl(meta.icon);
        const copy = document.createElement("div");
        const num = document.createElement("strong");
        num.textContent = count == null ? "—" : String(count);
        const label = document.createElement("span");
        label.textContent = meta.label;
        copy.append(num, label);
        card.append(ic, copy);
        statCards.append(card);
      }
    } finally {
      countsLoading = false;
    }
  }
}
