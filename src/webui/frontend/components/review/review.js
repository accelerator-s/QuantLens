import { renderState } from "../state-card/state-card.js";
import { runWithConfirm } from "../confirm-dialog/confirm-dialog.js";
import { toastError } from "../../core/toast.js";
import { badge, fmtTime } from "../../core/fmt.js";
import { codeEl, el, kvEl, tableEl } from "../../core/dom.js";


const PROPOSAL_ACTIONS = [
  { target: "approved", label: "通过", cls: "btn btn--primary" },
  { target: "rejected", label: "驳回", cls: "btn btn--danger" },
  { target: "superseded", label: "标记已取代", cls: "btn btn--ghost" },
];
const PROPOSAL_STATUS_LABEL = {
  pending: "待评审",
  approved: "已通过",
  rejected: "已驳回",
  superseded: "已取代",
};
const PROPOSAL_STATUS_TONE = {
  pending: "warn",
  approved: "safe",
  rejected: "danger",
  superseded: "muted",
};
const ROLE_LABEL = {
  factor_candidate: "因子候选",
  factor_evolution: "因子进化",
  issue_triage: "事项整理",
  strategy_profile: "策略配置",
  memory_entry: "记忆条目",
};
const ADAPTER_STATUS_LABEL = { ready: "正常", degraded: "降级（仅本地）" };
const ADAPTER_STATUS_TONE = { ready: "safe", degraded: "warn" };
const CANDIDATE_STATUS_LABEL = { pending: "待评审", committed: "已入库", discarded: "已丢弃" };
const CANDIDATE_STATUS_TONE = { pending: "warn", committed: "safe", discarded: "muted" };
const TRASH_CATEGORY_LABEL = {
  factor: "因子",
  strategy: "策略",
  strategy_profile: "策略配置",
  issue: "issue",
  proposal: "候选",
  memory: "记忆",
  data: "数据",
  portfolio_account: "持仓账户",
};
const SOURCE_LABEL = {
  api_model: "模型生成",
  external_ide_ai: "外部开发助手",
  human: "人工录入",
  cli: "本机操作",
};


const PAYLOAD_KEY_LABEL = {
  task: "任务类型",
  produced_at: "产出时间",
  model_name: "产出模型",
  related_issue_id: "关联事项",
  confidence: "置信度",
  rationale: "设计思路",
  factor_name: "因子名",
  assumption: "核心假设",
  direction: "因子方向",
  timescale: "时间尺度",
  dependencies: "依赖字段",
  parameters: "参数",
  standardization: "预处理流程",
  missing_handling: "缺失值处理",
  code: "因子代码",
  evaluation_metric_hints: "建议评价指标",
  suggested_type: "建议类型",
  suggested_priority: "建议优先级",
  suggested_title: "建议标题",
  suggested_owner: "建议负责人",
  background: "背景",
  goal: "目标",
  scope: "范围",
  acceptance: "验收标准",
  risks: "风险",
  extra: "附加信息",
  _evolution_change_type: "进化改动类型",
  _change_summary: "改动摘要",
  _expected_impact: "预期影响",
  verified_fids: "已验证字段",
  title: "标题",
  content: "内容",
  summary: "摘要",
  tags: "标签",
  source: "来源",
  category: "类别",
  kind: "类型",
  created_at: "创建时间",
  notes: "备注",
};
const PAYLOAD_VALUE_LABEL = {
  task: {
    factor_candidate: "因子候选",
    factor_evolution: "因子进化",
    issue_triage: "事项整理",
  },
  direction: {
    positive: "正向（值越大越看多）",
    negative: "负向（值越小越看多）",
    long_only_positive: "仅做多·正向",
    long_only_negative: "仅做多·负向",
  },
  timescale: { daily: "日频", weekly: "周频", monthly: "月频", quarterly: "季频" },
  missing_handling: {
    drop: "剔除缺失样本",
    zero: "按 0 填充",
    skip: "跳过缺失样本",
    fillna: "缺失值填充",
    cross_median: "按全市场中位数填充",
    industry_median: "按行业中位数填充",
  },
  suggested_priority: { urgent: "紧急", high: "高", medium: "中", low: "低" },
  _evolution_change_type: {
    lookback: "回溯期调整",
    parameter: "参数调整",
    formula: "公式调整",
    structure: "结构调整",
    new_factor: "全新因子",
  },
  suggested_type: {
    data_source: "数据源",
    factor: "因子",
    market_rule: "市场规则",
    cli: "本机操作",
    memory: "记忆",
    bug: "缺陷",
    framework: "框架",
    strategy_profile: "策略配置",
    portfolio: "组合",
    provider_acceptance_fail: "数据源验收失败",
    data_quality_conflict: "数据质量冲突",
  },
};

function keyLabel(key) {
  return PAYLOAD_KEY_LABEL[key] || key;
}


const STANDARDIZATION_STEP_LABEL = {
  winsorize: "极值缩尾",
  zscore: "标准化",
  rank: "分位排名",
  industry_neutral: "行业中性化",
  size_neutral: "市值中性化",
  fillna: "缺失值填充",
};

function standardizationLabel(step) {
  const [name, ...args] = String(step).split(":");
  const label = STANDARDIZATION_STEP_LABEL[name] || name;
  return args.length ? `${label}（${args.join(" – ")}）` : label;
}

function payloadScalar(value, key) {
  if (value === null || value === undefined || value === "") return "—";
  if (value === true) return "是";
  if (value === false) return "否";
  if (key === "standardization") return standardizationLabel(value);
  if (key.endsWith("_at") && typeof value === "string") return fmtTime(value);
  const mapped = PAYLOAD_VALUE_LABEL[key]?.[value];
  if (mapped) return mapped;
  if (key === "confidence") {
    const num = Number(value);
    if (Number.isFinite(num) && num >= 0 && num <= 1) {
      return `${value}（${Number((num * 100).toFixed(1))}%）`;
    }
  }
  return String(value);
}

function isLongText(value) {
  return typeof value === "string" && (value.includes("\n") || value.length > 80);
}

function longTextEl(value) {
  return el("p", "item-row__meta payload-view__text", value);
}


function subObjectEl(value) {
  if (value === null || typeof value !== "object") {
    return el("p", "item-row__meta", payloadScalar(value, ""));
  }
  if (Array.isArray(value)) {
    if (value.every((v) => typeof v !== "object" || v === null)) {
      const text = value.length ? value.map((v) => payloadScalar(v, "")).join("、") : "—";
      return el("p", "item-row__meta", text);
    }
    const box = el("div", "payload-view__nested");
    value.forEach((item, idx) => {
      box.append(el("p", "payload-view__subtitle", `第 ${idx + 1} 项`), subObjectEl(item));
    });
    return box;
  }
  const box = el("div", "payload-view__nested");
  const pairs = [];
  const nested = [];
  for (const [k, v] of Object.entries(value)) {
    if (v && typeof v === "object") nested.push([k, subObjectEl(v)]);
    else if (isLongText(v)) nested.push([k, longTextEl(v)]);
    else pairs.push([keyLabel(k), payloadScalar(v, k)]);
  }
  if (pairs.length) box.append(kvEl(pairs));
  for (const [k, node] of nested) {
    box.append(el("p", "payload-view__subtitle", keyLabel(k)), node);
  }
  return box;
}


function payloadEl(payload) {
  const wrap = el("div", "payload-view");
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    wrap.append(subObjectEl(payload));
    return wrap;
  }
  const pairs = [];
  const sections = [];
  for (const [key, value] of Object.entries(payload)) {
    if (key === "schema_version") continue;
    if (key === "code" && typeof value === "string") {
      sections.push([key, codeEl(value)]);
    } else if (isLongText(value)) {
      sections.push([key, longTextEl(value)]);
    } else if (Array.isArray(value) && value.every((v) => typeof v !== "object" || v === null)) {
      const text = value.length ? value.map((v) => payloadScalar(v, key)).join("、") : "—";
      pairs.push([keyLabel(key), text]);
    } else if (value && typeof value === "object") {
      sections.push([key, subObjectEl(value)]);
    } else {
      pairs.push([keyLabel(key), payloadScalar(value, key)]);
    }
  }
  if (pairs.length) wrap.append(kvEl(pairs));
  for (const [key, node] of sections) {
    wrap.append(el("h5", "payload-view__title", keyLabel(key)), node);
  }
  return wrap;
}


export async function mount(root, ctx) {
  const tabs = root.querySelectorAll("[data-tab]");
  const panels = root.querySelectorAll("[data-panel]");
  const loaded = new Set();

  const hosts = {
    proposalList: root.querySelector("[data-proposal-list]"),
    proposalDetail: root.querySelector("[data-proposal-detail]"),
    memoryStatus: root.querySelector("[data-memory-status]"),
    memoryList: root.querySelector("[data-memory-list]"),
    memoryDetail: root.querySelector("[data-memory-detail]"),
    trash: root.querySelector("[data-trash]"),
  };
  let currentProposal = "";
  let currentCandidate = "";

  for (const btn of tabs) {
    btn.addEventListener("click", () => {
      tabs.forEach((b) => b.classList.toggle("is-active", b === btn));
      panels.forEach((p) => {
        p.hidden = p.dataset.panel !== btn.dataset.tab;
      });
      activate(btn.dataset.tab);
    });
  }

  ctx.bus.on("route", (id) => {
    if (id === "review") {
      loaded.clear();
      activate(root.querySelector("[data-tab].is-active").dataset.tab);
    }
  });
  await activate("proposals");

  async function activate(tab) {
    if (loaded.has(tab)) return;
    loaded.add(tab);
    if (tab === "proposals") await loadProposals();
    if (tab === "memory") await loadMemory();
    if (tab === "trash") await loadTrash();
  }

  
  async function loadProposals() {
    await renderState(hosts.proposalList, { kind: "loading", title: "加载候选…" });
    try {
      const data = await ctx.api.proposals();
      hosts.proposalList.innerHTML = "";
      if (!data.items.length) {
        renderState(hosts.proposalList, {
          kind: "empty",
          title: "暂无候选",
          detail: "模型生成的候选在此等待人工评审。",
        });
        hosts.proposalDetail.innerHTML = "";
        return;
      }
      for (const item of data.items) {
        const row = el("button", "item-row");
        row.type = "button";
        row.dataset.id = item.proposal_id;
        if (item.proposal_id === currentProposal) row.classList.add("is-active");
        row.append(el("span", "item-row__title", item.proposal_id));
        const meta = el("span", "item-row__meta");
        meta.append(
          badge(
            PROPOSAL_STATUS_LABEL[item.status] || item.status,
            PROPOSAL_STATUS_TONE[item.status] || "muted",
          ),
          badge(ROLE_LABEL[item.role] || item.role, "muted"),
          el("span", "", fmtTime(item.created_at)),
        );
        row.append(meta);
        row.addEventListener("click", () => {
          currentProposal = item.proposal_id;
          hosts.proposalList.querySelectorAll(".item-row").forEach((n) => {
            n.classList.toggle("is-active", n.dataset.id === currentProposal);
          });
          loadProposalDetail(item.proposal_id);
        });
        hosts.proposalList.append(row);
      }
      if (!currentProposal || !data.items.some((i) => i.proposal_id === currentProposal)) {
        currentProposal = data.items[0].proposal_id;
        hosts.proposalList.querySelector(".item-row")?.classList.add("is-active");
      }
      loadProposalDetail(currentProposal);
    } catch (err) {
      await renderState(hosts.proposalList, {
        kind: "error",
        title: "候选加载失败",
        detail: err.message,
        retry: () => loadProposals(),
      });
    }
  }

  async function loadProposalDetail(id) {
    await renderState(hosts.proposalDetail, { kind: "loading", title: "加载候选详情…" });
    try {
      const data = await ctx.api.proposal(id);
      const p = data.proposal;
      hosts.proposalDetail.innerHTML = "";
      const card = el("article", "card detail-card");
      const head = el("div", "detail-card__head");
      head.append(el("h3", "", p.proposal_id));
      head.append(
        badge(PROPOSAL_STATUS_LABEL[p.status] || p.status, PROPOSAL_STATUS_TONE[p.status] || "muted"),
        badge(ROLE_LABEL[p.role] || p.role, "muted"),
      );
      card.append(head);
      card.append(
        kvEl([
          ["来源", SOURCE_LABEL[p.source] || p.source],
          ["模型", p.model_name],
          ["关联事项", p.related_issue_id],
          ["创建时间", fmtTime(p.created_at)],
          ["决策人", p.decided_by],
          ["决策备注", p.decision_note],
          ["文件路径", data.path],
        ]),
      );
      if (p.status === "pending") {
        const ops = el("div", "detail-card__head");
        ops.append(el("h4", "", "评审"));
        for (const action of PROPOSAL_ACTIONS) {
          const btn = el("button", action.cls, action.label);
          btn.type = "button";
          btn.addEventListener("click", () =>
            transitionProposal(p.proposal_id, action.target, action.label),
          );
          ops.append(btn);
        }
        card.append(ops);
      }
      card.append(el("h4", "", "候选内容"));
      card.append(payloadEl(p.payload));
      hosts.proposalDetail.append(card);
    } catch (err) {
      await renderState(hosts.proposalDetail, {
        kind: "error",
        title: "候选详情加载失败",
        detail: err.message,
        retry: () => loadProposalDetail(id),
      });
    }
  }

  async function transitionProposal(id, target, label) {
    try {
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.proposalTransition(id, { target, ...extra }),
        title: `评审候选：${label}`,
        message: "评审必须填写决策人。",
        requireDecidedBy: true,
        notePlaceholder: "决策备注",
        confirmLabel: "确认评审",
        danger: target === "rejected",
      });
      if (!result) return;
      ctx.bus.emit("toast", { kind: "success", title: `${id} 已评审为「${label}」` });
      await loadProposals();
    } catch (err) {
      toastError(ctx.bus, err, "候选评审失败");
    }
  }

  
  async function loadMemory() {
    renderState(hosts.memoryList, { kind: "loading", title: "加载记忆候选…" });
    try {
      const status = await ctx.api.memoryStatus();
      hosts.memoryStatus.innerHTML = "";
      const card = el("article", "card detail-card");
      const head = el("div", "detail-card__head");
      head.append(el("h3", "", "记忆适配器"));
      head.append(
        badge(
          ADAPTER_STATUS_LABEL[status.status] || status.status,
          ADAPTER_STATUS_TONE[status.status] || "muted",
        ),
      );
      card.append(head);
      card.append(
        kvEl([
          ["外部框架", status.package_name],
          ["固定版本", status.pinned_version || "（未配置）"],
          ["上次检查", fmtTime(status.last_check)],
        ]),
      );
      hosts.memoryStatus.append(card);
    } catch (err) {
      hosts.memoryStatus.innerHTML = "";
      renderState(hosts.memoryStatus, {
        kind: "error",
        title: "适配器状态加载失败",
        detail: err.message,
      });
    }

    try {
      const data = await ctx.api.memoryCandidates();
      hosts.memoryList.innerHTML = "";
      if (!data.items.length) {
        renderState(hosts.memoryList, {
          kind: "empty",
          title: "暂无记忆候选",
          detail: "模型讨论记录需经人工确认后存入记忆。",
        });
        hosts.memoryDetail.innerHTML = "";
        return;
      }
      for (const item of data.items) {
        const row = el("button", "item-row");
        row.type = "button";
        row.dataset.id = item.candidate_id;
        if (item.candidate_id === currentCandidate) row.classList.add("is-active");
        row.append(el("span", "item-row__title", item.candidate_id));
        const meta = el("span", "item-row__meta");
        meta.append(
          badge(
            CANDIDATE_STATUS_LABEL[item.status] || item.status,
            CANDIDATE_STATUS_TONE[item.status] || "muted",
          ),
          el("span", "", fmtTime(item.created_at)),
        );
        row.append(meta);
        row.addEventListener("click", () => {
          currentCandidate = item.candidate_id;
          hosts.memoryList.querySelectorAll(".item-row").forEach((n) => {
            n.classList.toggle("is-active", n.dataset.id === currentCandidate);
          });
          loadCandidateDetail(item.candidate_id);
        });
        hosts.memoryList.append(row);
      }
      if (!currentCandidate || !data.items.some((i) => i.candidate_id === currentCandidate)) {
        currentCandidate = data.items[0].candidate_id;
        hosts.memoryList.querySelector(".item-row")?.classList.add("is-active");
      }
      loadCandidateDetail(currentCandidate);
    } catch (err) {
      await renderState(hosts.memoryList, {
        kind: "error",
        title: "记忆候选加载失败",
        detail: err.message,
        retry: () => loadMemory(),
      });
    }
  }

  async function loadCandidateDetail(id) {
    await renderState(hosts.memoryDetail, { kind: "loading", title: "加载候选详情…" });
    try {
      const data = await ctx.api.memoryCandidate(id);
      const rec = data.candidate;
      hosts.memoryDetail.innerHTML = "";
      const card = el("article", "card detail-card");
      const head = el("div", "detail-card__head");
      head.append(el("h3", "", rec.candidate_id));
      head.append(
        badge(
          CANDIDATE_STATUS_LABEL[rec.status] || rec.status,
          CANDIDATE_STATUS_TONE[rec.status] || "muted",
        ),
      );
      if (data.secret_matches.length) head.append(badge("疑似密钥", "danger"));
      card.append(head);
      card.append(
        kvEl([
          ["创建时间", fmtTime(rec.created_at)],
          ["提交时间", fmtTime(rec.committed_at)],
          ["决策人", rec.decided_by],
          ["文件路径", data.path],
        ]),
      );
      if (data.secret_matches.length) {
        card.append(el("h4", "", "密钥扫描命中"));
        for (const m of data.secret_matches) {
          const p = el("p", "item-row__meta");
          p.append(badge(m.name, "danger"), el("span", "", m.excerpt));
          card.append(p);
        }
      }
      if (rec.status === "pending") {
        const ops = el("div", "detail-card__head");
        ops.append(el("h4", "", "评审"));
        const commitBtn = el("button", "btn btn--primary", "入长期记忆");
        commitBtn.type = "button";
        commitBtn.addEventListener("click", () =>
          commitCandidate(rec.candidate_id, data.secret_matches.length),
        );
        const discardBtn = el("button", "btn btn--danger", "丢弃");
        discardBtn.type = "button";
        discardBtn.addEventListener("click", () => discardCandidate(rec.candidate_id));
        ops.append(commitBtn, discardBtn);
        card.append(ops);
      }
      card.append(el("h4", "", "候选内容"));
      card.append(payloadEl(rec.payload));
      hosts.memoryDetail.append(card);
    } catch (err) {
      await renderState(hosts.memoryDetail, {
        kind: "error",
        title: "候选详情加载失败",
        detail: err.message,
        retry: () => loadCandidateDetail(id),
      });
    }
  }

  async function commitCandidate(id, secretCount) {
    try {
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.memoryCommit(id, extra),
        title: "候选入长期记忆",
        message: secretCount
          ? `密钥扫描命中 ${secretCount} 处，请先确认内容安全再提交！`
          : "确认后由记忆适配器写入长期记忆文件。",
        requireDecidedBy: true,
        confirmLabel: "确认入库",
        danger: secretCount > 0,
      });
      if (!result) return;
      ctx.bus.emit("toast", {
        kind: "success",
        title: `${id} 已入长期记忆`,
        detail: result.long_term_file,
      });
      await loadMemory();
    } catch (err) {
      toastError(ctx.bus, err, "记忆入库失败");
    }
  }

  async function discardCandidate(id) {
    try {
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.memoryDiscard(id, extra),
        title: "丢弃记忆候选",
        requireDecidedBy: true,
        notePlaceholder: "丢弃原因",
        confirmLabel: "确认丢弃",
        danger: true,
      });
      if (!result) return;
      ctx.bus.emit("toast", { kind: "success", title: `${id} 已丢弃` });
      await loadMemory();
    } catch (err) {
      toastError(ctx.bus, err, "记忆丢弃失败");
    }
  }

  
  async function loadTrash() {
    await renderState(hosts.trash, { kind: "loading", title: "加载回收站…" });
    try {
      const data = await ctx.api.trashList();
      hosts.trash.innerHTML = "";
      if (!data.items.length) {
        renderState(hosts.trash, {
          kind: "empty",
          title: "回收站为空",
          detail: "被删除的因子、策略等条目会进入回收站，可恢复或清除。",
        });
        return;
      }
      hosts.trash.append(
        tableEl(
          [
            { key: "bucket", label: "条目" },
            { key: "category", label: "类别", format: (v) => badge(TRASH_CATEGORY_LABEL[v] || v, "muted") },
            { key: "original_path", label: "原路径" },
            { key: "deleted_by", label: "删除人" },
            { key: "reason", label: "原因" },
            { key: "trashed_at", label: "删除时间", format: (v) => fmtTime(v) },
            {
              key: "bucket",
              label: "操作",
              format: (bucket) => {
                const wrap = el("span", "detail-card__head");
                const restore = el("button", "btn btn--ghost", "恢复");
                restore.type = "button";
                restore.addEventListener("click", () => restoreTrash(bucket));
                const purge = el("button", "btn btn--danger", "清除");
                purge.type = "button";
                purge.addEventListener("click", () => purgeTrash(bucket));
                wrap.append(restore, purge);
                return wrap;
              },
            },
          ],
          data.items,
        ),
      );
    } catch (err) {
      await renderState(hosts.trash, {
        kind: "error",
        title: "回收站加载失败",
        detail: err.message,
        retry: () => loadTrash(),
      });
    }
  }

  async function restoreTrash(bucket) {
    try {
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.trashRestore(bucket, extra),
        title: `恢复 ${bucket}`,
        message: "目标路径已存在时后端会拒绝恢复，不会覆盖现有文件。",
        confirmLabel: "确认恢复",
      });
      if (!result) return;
      ctx.bus.emit("toast", { kind: "success", title: "已恢复", detail: result.restored_to });
      await loadTrash();
    } catch (err) {
      toastError(ctx.bus, err, "回收站恢复失败");
    }
  }

  async function purgeTrash(bucket) {
    try {
      const result = await runWithConfirm({
        execute: (extra) => ctx.api.trashPurge(bucket, extra),
        title: `永久清除 ${bucket}`,
        message: "清除后不可恢复。",
        confirmLabel: "确认清除",
        danger: true,
      });
      if (!result) return;
      ctx.bus.emit("toast", { kind: "success", title: `已清除 ${bucket}` });
      await loadTrash();
    } catch (err) {
      toastError(ctx.bus, err, "回收站清除失败");
    }
  }
}
