import {labelText} from "../../core/labels.js";
import {renderStrategyConfig} from "./config-summary.js";
import { renderState } from "../state-card/state-card.js";
import { runWithConfirm } from "../confirm-dialog/confirm-dialog.js";
import { formDialog } from "../confirm-dialog/form-dialog.js";
import { openStrategyChat } from "./strategy-chat.js";
import { toastError } from "../../core/toast.js";
import { badge, fmtTime } from "../../core/fmt.js";
import { codeEl, el, kvEl, tableEl } from "../../core/dom.js";
import { captureLogScroll, updateTaskLog } from "../../core/task-log.js";


const STRATEGY_TRANSITIONS = {
  draft: ["active", "disabled"],
  active: ["disabled"],
  disabled: ["draft"],
};


const STATUS_LABEL = { draft: "草稿", active: "启用中", disabled: "已停用" };
const STATUS_TONE = { draft: "muted", active: "safe", disabled: "warn" };
const ACTION_LABEL = { draft: "转回草稿", active: "启用", disabled: "停用" };
const DIRECTION_LABEL = { buy: "买入", sell: "卖出", both: "买卖双向" };
const SESSION_TYPE_LABEL = {
  call_auction: "集合竞价",
  continuous: "连续竞价",
  closing_auction: "收盘集合竞价",
  night: "夜盘",
};

function statusBadge(status) {
  return badge(STATUS_LABEL[status] || status, STATUS_TONE[status] || "muted");
}


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
  ].filter(([, v]) => v !== "" && v !== undefined && v !== null)
    .map(([label, value]) => [label, value, {wide: ["停牌处理", "时段锚点"].includes(label)}]);
  if (baseRows.length) nodes.push(ruleHeading("overview", "交易规则"), kvEl(baseRows));

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
  const tabs = root.querySelectorAll("[data-tab]");
  const panels = root.querySelectorAll("[data-panel]");
  const loaded = new Set();

  const hosts = {
    profileList: root.querySelector("[data-profile-list]"),
    profileDetail: root.querySelector("[data-profile-detail]"),
    ruleList: root.querySelector("[data-rule-list]"),
    ruleDetail: root.querySelector("[data-rule-detail]"),
    trainingList: root.querySelector("[data-training-list]"),
    trainingDetail: root.querySelector("[data-training-detail]"),
    championSlots: root.querySelector("[data-champion-slots]"),
  };
  let currentProfile = "";
  let currentRule = "";
  let currentTrainingId = "";
  const trainingPolls = new Map();

  root
    .querySelector("[data-profile-create]")
    .addEventListener("click", () => openStrategyChat(ctx, { onYaml: saveDraft }));
  root.querySelector("[data-strategy-train]").addEventListener("click", () => startTraining());

  for (const btn of tabs) {
    btn.addEventListener("click", () => {
      tabs.forEach((b) => b.classList.toggle("is-active", b === btn));
      panels.forEach((p) => {
        p.hidden = p.dataset.panel !== btn.dataset.tab;
      });
      root.querySelector('[data-profile-create]').hidden = btn.dataset.tab !== 'profiles';
      activate(btn.dataset.tab);
    });
  }

  ctx.bus.on("route", (id) => {
    if (id === "strategy") {
      loaded.clear();
      activate(root.querySelector("[data-tab].is-active").dataset.tab);
    }
  });
  await activate("profiles");

  async function activate(tab) {
    if (loaded.has(tab)) return;
    loaded.add(tab);
    if (tab === "profiles") await loadProfiles();
    if (tab === "training") await loadTrainings();
    if (tab === "rules") await loadRules();
  }

  
  async function loadTrainings() {
    await renderState(hosts.trainingList, { kind: "loading", title: "加载策略研究任务…" });
    try {
      const data = await ctx.api.strategyEvolutions();
      const items = data.items || [];
      hosts.trainingList.replaceChildren();
      if (!items.length) {
        await renderState(hosts.trainingList, {
          kind: "empty",
          title: "尚无多因子训练",
          message: "所需因子完成迭代研究与锁定测试后，可启动策略训练。",
        });
        return;
      }
      for (const item of items) {
        const button = el("button", "item-row");
        button.type = "button";
        button.append(
          el("strong", "", item.program.objective),
          el(
            "span",
            "item-row__meta",
            `${fmtTime(item.created_at)} · ${item.program.configuration?.model_name || "模型未记录"} · ${item.object_id}`,
          ),
          badge(item.state),
        );
        button.addEventListener("click", () => showTraining(item.object_id));
        hosts.trainingList.append(button);
      }
      const selected = items.find((item) => item.object_id === currentTrainingId) || items[0];
      await showTraining(selected.object_id);
      await loadChampionSlots();
    } catch (err) {
      toastError(ctx.bus, err, "策略训练列表加载失败");
    }
  }

  async function loadChampionSlots() {
    try {
      const data = await ctx.api.championSlots();
      const wrap = el("section", "detail-card");
      wrap.append(el("h3", "", "正式与候选策略比较"));
      const policy = data.policy || {};
      wrap.append(
        kvEl([
          ["固定影子观察期", policy.shadow_observation_days ? `${policy.shadow_observation_days} 个交易日` : "—"],
          ["最小同期观察数", policy.min_shadow_observations],
          ["排序目标", "回测协议主目标"],
          ["同分顺序", "最大回撤、年化换手"],
        ]),
      );
      if (!data.items?.length) {
        wrap.append(el("p", "item-row__meta", "尚无可比较的策略方案。"));
      }
      for (const slot of data.items || []) {
        wrap.append(el("h4", "", slot.strategy_name));
        const rows = [];
        if (slot.champion) rows.push({ role: "champion", ...slot.champion });
        rows.push(...(slot.challengers || []).map((item) => ({ role: "challenger", ...item })));
        rows.push(...(slot.watching || []).map((item) => ({ role: "回滚观察", ...item })));
        wrap.append(
          tableEl(
            [
              { key: "role", label: "角色", format: (v) => badge(v) },
              { key: "object_id", label: "策略方案" },
              { key: "change", label: "研究变更" },
              { key: "action", label: "人工审批", format: (v) => v },
            ],
            rows.map((item) => ({
              ...item,
              change: item.strategy_version.change_summary,
              action: championAction(slot, item),
            })),
          ),
        );
      }
      hosts.championSlots.replaceChildren(wrap);
    } catch (err) {
      toastError(ctx.bus, err, "策略比较加载失败");
    }
  }

  function championAction(slot, item) {
    if (item.role === "回滚观察" && slot.champion) {
      const rollback = el("button", "btn btn--danger", "生成回滚证据");
      rollback.type = "button";
      rollback.addEventListener("click", async () => {
        rollback.disabled = true;
        try {
          const result = await ctx.api.proposeRollback(slot.champion.object_id, {
            rollback_strategy_version_id: item.object_id,
            rationale: "用户发起回滚审阅",
          });
          await runWithConfirm({
            execute: (p) => ctx.api.approvePromotion(result.decision.decision_id, {
              confirm: p.confirm,
              reason: p.note || "",
              decided_by: p.decided_by || "",
            }),
            title: "批准策略回滚",
            message: `${result.decision.rationale}\n证据运行：${(result.decision.evidence_run_ids || []).join("、")}`,
            requireDecidedBy: true,
            notePlaceholder: "记录风险、衰减或归因证据",
            confirmLabel: "批准回滚",
            danger: true,
          });
          await loadChampionSlots();
        } catch (err) {
          toastError(ctx.bus, err, "策略回滚失败");
        } finally {
          rollback.disabled = false;
        }
      });
      return rollback;
    }
    if (item.role !== "challenger") return "—";
    const btn = el("button", "btn btn--ghost", slot.champion ? "检查正式使用条件" : "设为首个正式策略");
    btn.type = "button";
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        if (!slot.champion) {
          await runWithConfirm({
            execute: (p) => ctx.api.bootstrapChampion(item.object_id, { confirm: p.confirm, reason: p.note || "", decided_by: p.decided_by || "" }),
            title: "确认首个正式策略",
            requireDecidedBy: true,
            notePlaceholder: "说明审阅结论",
            confirmLabel: "确认正式使用",
          });
        } else {
          const result = await ctx.api.proposePromotion(item.object_id);
          ctx.bus.emit("toast", { kind: "info", title: `正式使用评估已生成：${result.decision.decision_id}` });
          await runWithConfirm({
            execute: (p) => ctx.api.approvePromotion(result.decision.decision_id, { confirm: p.confirm, reason: p.note || "", decided_by: p.decided_by || "" }),
            title: "批准正式策略切换",
            message: `${result.decision.rationale}\n证据运行：${(result.decision.evidence_run_ids || []).join("、")}`,
            requireDecidedBy: true,
            notePlaceholder: "记录人工审阅理由",
            confirmLabel: "批准并切换",
          });
        }
        await loadChampionSlots();
      } catch (err) {
        toastError(ctx.bus, err, "正式策略操作失败");
      } finally {
        btn.disabled = false;
      }
    });
    return btn;
  }

  async function showTraining(programId, { showLoading = true } = {}) {
    currentTrainingId = programId;
    const previousLog = hosts.trainingDetail.querySelector("[data-task-log]");
    const retainedLog = previousLog?.dataset.subject === programId ? previousLog : null;
    if (showLoading) {
      await renderState(hosts.trainingDetail, { kind: "loading", title: "加载研究结果…" });
    }
    try {
      const [data, reportData] = await Promise.all([
        ctx.api.strategyEvolution(programId),
        ctx.api.researchReports(),
      ]);
      const versions = data.strategy_versions || [];
      const runs = data.evaluation_runs || [];
      const challenger = versions.find((item) => item.state === "challenger");
      const report = (reportData.items || []).find(
        (item) => item.research_report.research_program_id === programId,
      );
      const box = el("section", "detail-card");
      box.append(
        el("h3", "", data.program.objective),
        kvEl([
          ["研究状态", badge(data.state)],
          ["研究模型", data.program.configuration?.model_name || "历史任务未记录"],
          ["训练区间", `${data.program.train_window.start} .. ${data.program.train_window.end}`],
          ["验证区间", `${data.program.validation_window.start} .. ${data.program.validation_window.end}`],
          ["锁定测试", `${data.program.locked_test_window.start} .. ${data.program.locked_test_window.end}`],
          ["已保存方案数", versions.length],
          ["评价次数", runs.length],
          ["当前挑战者", challenger?.object_id || "尚未产生"],
        ]),
      );
      if (data.state === "paused") {
        const resume = el("button", "btn btn--ghost", "从已完成窗口继续训练");
        resume.type = "button";
        resume.addEventListener("click", async () => {
          resume.disabled = true;
          try {
            const started = await ctx.api.strategyEvolutionResume(programId);
            ctx.bus.emit("toast", { kind: "info", title: `已恢复策略训练：${programId}` });
            pollTraining(programId, started.task_id);
          } catch (err) {
            toastError(ctx.bus, err, "策略训练恢复失败");
            resume.disabled = false;
          }
        });
        box.append(resume);
      }
      if (versions.length) {
        box.append(
          el("h4", "", "已保存的策略方案"),
          tableEl(
            [
              { key: "id", label: "方案" },
              { key: "change", label: "单轮变更" },
              { key: "factors", label: "因子数", num: true },
              { key: "state", label: "状态", format: (v) => badge(v) },
            ],
            versions.map((item) => ({
              id: item.object_id,
              change: item.strategy_version.change_summary,
              factors: item.strategy_version.factor_inputs.length,
              state: item.state,
            })),
          ),
        );
      }
      if (report) {
        const content = await ctx.api.researchReportContent(report.object_id);
        box.append(el("h4", "", "研究报告（数值已核验）"), codeEl(content.content));
        if (report.state === "validated") {
          const approve = el("button", "btn btn--ghost", "人工批准报告");
          approve.type = "button";
          approve.addEventListener("click", async () => {
            approve.disabled = true;
            try {
              await runWithConfirm({
                execute: (p) => ctx.api.researchReportApprove(report.object_id, {
                  confirm: p.confirm,
                  decided_by: p.decided_by || "",
                  reason: p.note || "",
                }),
                title: "批准研究报告",
                requireDecidedBy: true,
                notePlaceholder: "填写对指标依据与研究局限的审阅意见",
                confirmLabel: "批准报告",
              });
              await showTraining(programId);
            } catch (err) {
              toastError(ctx.bus, err, "研究报告批准失败");
              approve.disabled = false;
            }
          });
          box.append(approve);
        }
      }
      const completed = runs.filter((item) => item.state === "completed");
      if (completed.length) {
        box.append(
          el("h4", "", "评价证据"),
          tableEl(
            [
              { key: "purpose", label: "用途" },
              { key: "subject", label: "策略方案" },
              { key: "window", label: "区间" },
              { key: "run", label: "回测运行" },
            ],
            completed.slice(0, 30).map((item) => ({
              purpose: item.evaluation_run.purpose,
              subject: item.evaluation_run.subject_id,
              window: `${item.evaluation_run.window.start} .. ${item.evaluation_run.window.end}`,
              run: item.evaluation_run.evaluation_run_id,
            })),
          ),
        );
      }
      if (currentTrainingId !== programId) return;
      const restoreScroll = captureLogScroll(retainedLog);
      hosts.trainingDetail.replaceChildren(...[box, retainedLog].filter(Boolean));
      restoreScroll();
    } catch (err) {
      toastError(ctx.bus, err, "策略训练详情加载失败");
    }
  }

  async function startTraining() {
    try {
      const [data, recommendationData] = await Promise.all([
        ctx.api.strategies(),
        ctx.api.strategyEvolutionModelRecommendations(),
      ]);
      const profiles = (data.items || data || []).filter((item) => !item.is_template && item.status !== "disabled");
      const details = await Promise.all(profiles.map((item) => ctx.api.strategy(item.profile_name)));
      const eligible = details.filter((item) => (item.resolved?.factors?.items || []).length >= 2);
      if (!eligible.length) throw new Error("请先配置至少包含两个因子的策略。");
      const models = (recommendationData.items || []).filter((item) => item.selectable);
      if (!models.length) throw new Error("没有可执行策略研究的模型，请检查模型连接和任务配置。");
      const values = await formDialog({
        title: "多因子策略训练",
        message: "每轮调整一类参数，检查因子、滚动验证，并对确定的候选执行一次锁定测试。",
        submitLabel: "启动训练",
        fields: [
          {
            key: "profile_name",
            label: "策略配置",
            kind: "select",
            options: eligible.map((item) => ({
              value: item.resolved.profile_name,
              label: item.display_name || item.resolved.display_name || item.resolved.profile_name,
            })),
          },
          {
            key: "model_name",
            label: "研究模型（优先使用所选模型）",
            kind: "select",
            value: recommendationData.recommended,
            hint: recommendationData.policy,
            options: models.map((item) => ({
              value: item.name,
              label: `${item.name}${item.recommended ? "（自动匹配）" : ""}`,
            })),
          },
        ],
      });
      if (!values) return;
      const started = await ctx.api.strategyEvolutionStart({
        profile_name: values.profile_name,
        model_name: values.model_name,
      });
      ctx.bus.emit("toast", { kind: "info", title: `策略训练已启动：${started.program_id}` });
      await loadTrainings();
      pollTraining(started.program_id, started.task_id);
    } catch (err) {
      toastError(ctx.bus, err, "策略训练启动失败");
    }
  }

  function pollTraining(programId, taskId) {
    const previous = trainingPolls.get(taskId);
    if (previous) clearTimeout(previous);
    async function tick() {
      try {
        const task = await ctx.api.task(taskId);
        if (currentTrainingId === programId) {
          await showTraining(programId, { showLoading: false });
        }
        if (task.status === "running") {
          if (task.log?.length && currentTrainingId === programId) {
            const taskLog = updateTaskLog(
              hosts.trainingDetail.querySelector("[data-task-log]"),
              task.log,
              { limit: 10, subject: programId },
            );
            if (taskLog && taskLog.parentNode !== hosts.trainingDetail) {
              hosts.trainingDetail.append(taskLog);
            }
          }
          trainingPolls.set(taskId, setTimeout(tick, 2500));
          return;
        }
        trainingPolls.delete(taskId);
        await loadTrainings();
        ctx.bus.emit("toast", {
          kind: task.status === "completed" ? "success" : "error",
          title: task.status === "completed" ? "策略训练已完成" : "策略训练失败",
          detail: task.error || "",
        });
      } catch (err) {
        trainingPolls.delete(taskId);
        toastError(ctx.bus, err, "策略训练状态查询失败");
      }
    }
    tick();
  }

  
  async function loadProfiles() {
    await renderState(hosts.profileList, { kind: "loading", title: "加载策略配置…" });
    try {
      const data = await ctx.api.strategies();
      renderProfileList(data.items);
    } catch (err) {
      await renderState(hosts.profileList, {
        kind: "error",
        title: "策略列表加载失败",
        detail: err.message,
        retry: () => loadProfiles(),
      });
    }
  }

  function renderProfileList(items) {
    hosts.profileList.innerHTML = "";
    if (!items.length) {
      renderState(hosts.profileList, {
        kind: "empty",
        title: "暂无策略配置",
        detail: "点击「新建策略配置」，从因子库组合因子，并设置调仓与风控。",
      });
      hosts.profileDetail.innerHTML = "";
      return;
    }
    for (const item of items) {
      const row = el("button", "item-row");
      row.type = "button";
      row.dataset.name = item.profile_name;
      if (item.profile_name === currentProfile) row.classList.add("is-active");
      row.append(el("span", "item-row__title", item.display_name || item.profile_name));
      const meta = el("span", "item-row__meta");
      meta.append(statusBadge(item.status));
      if (item.is_template) meta.append(badge("模板", "accent"));
      if (item.inheritance_parent) meta.append(el("span", "", "继承基础研究规则"));
      row.append(meta);
      row.addEventListener("click", () => {
        currentProfile = item.profile_name;
        hosts.profileList.querySelectorAll(".item-row").forEach((n) => {
          n.classList.toggle("is-active", n.dataset.name === currentProfile);
        });
        loadProfileDetail(item.profile_name);
      });
      hosts.profileList.append(row);
    }
    if (!currentProfile || !items.some((i) => i.profile_name === currentProfile)) {
      currentProfile = items[0].profile_name;
      hosts.profileList.querySelector(".item-row")?.classList.add("is-active");
    }
    loadProfileDetail(currentProfile);
  }

  async function loadProfileDetail(name) {
    await renderState(hosts.profileDetail, { kind: "loading", title: "加载策略详情…" });
    try {
      const data = await ctx.api.strategy(name);
      renderProfileDetail(data);
    } catch (err) {
      await renderState(hosts.profileDetail, {
        kind: "error",
        title: "策略详情加载失败",
        detail: err.message,
        retry: () => loadProfileDetail(name),
      });
    }
  }

  function renderProfileDetail(data) {
    const doc = data.profile;
    hosts.profileDetail.innerHTML = "";
    const card = el("article", "card detail-card");

    
    const head = el("div", "detail-card__head");
    head.append(el("h3", "", data.display_name || doc.display_name || doc.profile_name));
    head.append(statusBadge(doc.status));
    if (doc.is_template) head.append(badge("模板", "accent"));
    card.append(head);

    const ops = el("div", "strategy-actions");
    const editBtn = el("button", "btn btn--ghost", "编辑");
    editBtn.type = "button";
    editBtn.addEventListener("click", () =>
      openStrategyChat(ctx, { profileName: doc.profile_name, onYaml: saveDraft }),
    );
    ops.append(editBtn);
    for (const target of STRATEGY_TRANSITIONS[doc.status] || []) {
      const btn = el("button", "btn btn--ghost", ACTION_LABEL[target] || target);
      btn.type = "button";
      btn.addEventListener("click", () => transitionProfile(doc.profile_name, target));
      ops.append(btn);
    }
    const deleteBtn = el("button", "btn btn--danger", "删除");
    deleteBtn.type = "button";
    deleteBtn.addEventListener("click", () => deleteProfile(doc.profile_name));
    ops.append(deleteBtn);
    card.append(ops);

    if (data.resolve_error) {
      card.append(el("p", "ql-error", `配置解析失败：${data.resolve_error}`));
    } else if (data.resolved) {
      card.append(renderStrategyConfig(data));
    }
    hosts.profileDetail.append(card);
  }

  
  async function saveDraft(yamlText) {
    try {
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.strategyUpsert({ yaml_text: yamlText, override: true, ...extra }),
        title: "保存策略配置草案",
        confirmLabel: "确认保存",
      });
      if (!result) return;
      currentProfile = result.profile.profile_name;
      ctx.bus.emit("toast", { kind: "success", title: `已保存 ${currentProfile}` });
      await loadProfiles();
    } catch (err) {
      toastError(ctx.bus, err, "策略保存失败");
    }
  }

  async function transitionProfile(name, target) {
    try {
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.strategyTransition(name, { target, ...extra }),
        title: `将策略配置 ${name} ${ACTION_LABEL[target] || target}`,
        message: target === "active" ? "启用前会自动执行完整性校验，结果见预览。" : "",
        confirmLabel: "确认",
      });
      if (!result) return;
      ctx.bus.emit("toast", { kind: "success", title: `${name} 已${ACTION_LABEL[target] || target}` });
      await loadProfiles();
    } catch (err) {
      toastError(ctx.bus, err, "策略流转失败");
    }
  }

  async function deleteProfile(name) {
    try {
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.strategyDelete(name, extra),
        title: `删除策略配置 ${name}`,
        message: "配置文件移入回收站，可从回收站恢复。",
        requireDecidedBy: true,
        notePlaceholder: "删除原因",
        confirmLabel: "确认删除",
        danger: true,
      });
      if (!result) return;
      currentProfile = "";
      ctx.bus.emit("toast", { kind: "success", title: `已删除策略配置 ${name}` });
      await loadProfiles();
    } catch (err) {
      toastError(ctx.bus, err, "策略删除失败");
    }
  }

  
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
      meta.append(statusBadge(item.status), el("span", "", `${item.market} · ${item.instrument_class}`));
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
    head.append(
      statusBadge(rule.status),
      badge(rule.market, "accent"),
      badge(rule.instrument_class, "muted"),
    );
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
        title: `费用计算（${rule.name}）`,
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
        title: `涨跌停计算（${rule.name}）`,
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
