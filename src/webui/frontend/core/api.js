



import { fetchApi } from './page-cache.js';

async function request(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetchApi(path, opts);
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    const detail = data && data.detail;
    const message =
      (detail && typeof detail === "object" && detail.message) ||
      (typeof detail === "string" && detail) ||
      `请求失败 (${res.status})`;
    const error = new Error(message);
    error.status = res.status;
    if (detail && typeof detail === "object") {
      error.code = detail.code || "";
      error.module = detail.module || "";
      error.suggestedAction = detail.suggested_action || "";
      error.context = detail.context || {};
    }
    throw error;
  }
  return data;
}

function withQuery(path, params = {}) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, value);
    }
  }
  const qs = search.toString();
  return qs ? `${path}?${qs}` : path;
}

export const api = {
  chatHistoryTitle: (payload) => request("POST", "/api/chat-history/title", payload),
  chatHistory: (scope) => request("GET", withQuery("/api/chat-history", { scope })),
  chatHistorySave: (id, payload) => request("PUT", `/api/chat-history/${id}`, payload),
  chatHistoryDelete: (id) => request("DELETE", `/api/chat-history/${id}`),
  
  status: () => request("GET", "/api/status"),
  session: () => request("GET", "/api/session"),
  saveSession: (payload) => request("PUT", "/api/session", payload),

  

  

  
  aiModels: () => request("GET", "/api/ai/models"),
  aiAddModel: (payload) => request("POST", "/api/ai/models", payload),
  aiUpdateModel: (name, payload) => request("PUT", `/api/ai/models/${name}`, payload),
  aiRemoveModel: (name, payload) => request("POST", `/api/ai/models/${name}/remove`, payload),
  aiSetEnabled: (name, enabled) =>
    request("POST", `/api/ai/models/${name}/enabled`, { enabled }),
  aiDeleteModelKey: (name, payload) =>
    request("POST", `/api/ai/models/${name}/key/delete`, payload),
  aiTestModel: (name) => request("POST", `/api/ai/models/${name}/test`),
  aiConnectionModels: (payload) => request("POST", "/api/ai/connection/models", payload),
  aiTestConnection: (payload) => request("POST", "/api/ai/connection/test", payload),
  aiMatrix: () => request("GET", "/api/ai/matrix"),
  factorEvolutionModelRecommendations: () =>
    request("GET", "/api/ai/recommendations/factor-evolution"),
  strategyEvolutionModelRecommendations: () =>
    request("GET", "/api/ai/recommendations/strategy-evolution"),
  aiSetMatrixTask: (task, payload) =>
    request("POST", `/api/ai/matrix/tasks/${task}`, payload),
  aiDiscussModel: () => request("GET", "/api/ai/discuss-model"),
  aiSetDiscussModel: (name) => request("POST", "/api/ai/discuss-model", { name }),
  aiRoutePreview: (task) => request("GET", withQuery("/api/ai/route-preview", { task })),

  
  providers: () => request("GET", "/api/providers"),
  dataTables: (params) => request("GET", withQuery("/api/data/tables", params)),
  dataUpdateCheck: () => request("GET", "/api/data/update-check"),
  dataSync: (payload) => request("POST", "/api/data/sync", payload),
  activeDataSync: () => request("GET", "/api/data/sync/active"),
  dataSnapshots: () => request("GET", "/api/lifecycle/data-snapshots"),
  dataSnapshotCreate: (payload) => request("POST", "/api/lifecycle/data-snapshots", payload),
  lifecycleSummary: () => request("GET", "/api/lifecycle/summary"),
  lifecycleIntegrity: () => request("GET", "/api/lifecycle/integrity"),
  lifecycleEvents: (limit = 100) => request("GET", `/api/lifecycle/events?limit=${limit}`),
  researchReports: () => request("GET", "/api/lifecycle/research-reports"),
  researchReportGenerate: (id) => request("POST", `/api/lifecycle/research-programs/${id}/report`, {}),
  researchReportContent: (id) => request("GET", `/api/lifecycle/research-reports/${id}/content`),
  researchReportApprove: (id, payload) => request("POST", `/api/lifecycle/research-reports/${id}/approve`, payload),
  factorVersionPromote: (id, payload) => request("POST", `/api/lifecycle/factor-versions/${id}/promote`, payload),
  task: (id) => request("GET", `/api/tasks/${id}`),

  
  factors: (params) => request("GET", withQuery("/api/factors", params)),
  factor: (name) => request("GET", `/api/factors/${name}`),
  factorTransition: (name, payload) =>
    request("POST", `/api/factors/${name}/transition`, payload),
  factorRemove: (name, payload) => request("POST", `/api/factors/${name}/remove`, payload),
  factorResearchChat: (payload) => request("POST", "/api/factors/chat", payload),
  factorResearchStart: (payload) => request("POST", "/api/factors/chat/research", payload),
  factorEvalRuns: (name) => request("GET", `/api/factors/${name}/eval-runs`),
  factorEvalRun: (name, runId) => request("GET", `/api/factors/${name}/eval-runs/${runId}`),
  factorEvalChartUrl: (name, runId, filename) =>
    `/api/factors/${name}/eval-runs/${runId}/charts/${encodeURIComponent(filename)}`,

  
  strategies: (params) => request("GET", withQuery("/api/strategies", params)),
  strategy: (name) => request("GET", `/api/strategies/${name}`),
  strategyUpsert: (payload) => request("POST", "/api/strategies", payload),
  strategyTransition: (name, payload) =>
    request("POST", `/api/strategies/${name}/transition`, payload),
  strategyDelete: (name, payload) => request("POST", `/api/strategies/${name}/delete`, payload),
  strategyChat: (payload) => request("POST", "/api/strategies/chat", payload),
  strategyChatFinalize: (payload) => request("POST", "/api/strategies/chat/finalize", payload),
  strategyEvolutions: () => request("GET", "/api/strategy-evolution"),
  strategyEvolution: (id) => request("GET", `/api/strategy-evolution/${id}`),
  strategyEvolutionStart: (payload) => request("POST", "/api/strategy-evolution", payload),
  strategyEvolutionResume: (id) => request("POST", `/api/strategy-evolution/${id}/resume`, {}),
  dailyOperationsRun: (payload) => request("POST", "/api/operations/daily-run", payload),
  executionRecovery: (payload) => request("POST", "/api/operations/execution-recovery", payload),
  automation: () => request("GET", "/api/operations/automation"),
  automationSave: (payload) => request("PUT", "/api/operations/automation", payload),
  shadowObserve: (payload) => request("POST", "/api/operations/shadow-observe", payload),
  attributionMature: (payload) => request("POST", "/api/operations/attribution-mature", payload),
  attributions: (params) => request("GET", withQuery("/api/operations/attributions", params)),
  decayScan: () => request("POST", "/api/operations/decay-scan", {}),
  learningEvents: () => request("GET", "/api/operations/learning-events"),
  learningEventAccept: (id, payload) => request("POST", `/api/operations/learning-events/${id}/accept`, payload),
  canonicalPlans: (params) => request("GET", withQuery("/api/operations/plans", params)),
  canonicalPlanConfirm: (id, payload) => request("POST", `/api/operations/plans/${id}/confirm`, payload),
  canonicalPlanReject: (id, payload) => request("POST", `/api/operations/plans/${id}/reject`, payload),
  championSlots: () => request("GET", "/api/operations/champion-slots"),
  bootstrapChampion: (id, payload) => request("POST", `/api/operations/strategies/${id}/bootstrap-champion`, payload),
  proposePromotion: (id) => request("POST", `/api/operations/strategies/${id}/propose-promotion`),
  proposeRollback: (id, payload) => request("POST", `/api/operations/strategies/${id}/propose-rollback`, payload),
  approvePromotion: (id, payload) => request("POST", `/api/operations/champion-decisions/${id}/approve`, payload),
  marketRules: () => request("GET", "/api/market-rules"),
  marketRule: (id) => request("GET", `/api/market-rules/${id}`),
  marketRuleCheckFees: (name, params) =>
    request("GET", withQuery(`/api/market-rules/${name}/check-fees`, params)),
  marketRuleCheckPriceLimit: (name, params) =>
    request("GET", withQuery(`/api/market-rules/${name}/check-price-limit`, params)),

  
  backtestRuns: (params) => request("GET", withQuery("/api/backtest/runs", params)),
  backtestRun: (id) => request("GET", `/api/backtest/runs/${id}`),
  backtestNav: (id) => request("GET", `/api/backtest/runs/${id}/nav`),
  backtestTrades: (id, params) =>
    request("GET", withQuery(`/api/backtest/runs/${id}/trades`, params)),
  backtestPositions: (id, params) =>
    request("GET", withQuery(`/api/backtest/runs/${id}/positions`, params)),

  
  experiments: (params) => request("GET", withQuery("/api/experiments", params)),
  experiment: (id) => request("GET", `/api/experiments/${id}`),
  experimentCompare: (id) => request("GET", `/api/experiments/${id}/compare`),
  factorEvolutions: () => request("GET", "/api/factor-evolution"),
  factorEvolutionLockedTests: (params) =>
    request("GET", withQuery("/api/factor-evolution/locked-tests", params)),
  factorEvolution: (id) => request("GET", `/api/factor-evolution/${id}`),
  factorEvolutionStart: (payload) => request("POST", "/api/factor-evolution", payload),
  factorEvolutionBatchStart: (payload) => request("POST", "/api/factor-evolution/batch", payload),
  factorEvolutionResume: (id) => request("POST", `/api/factor-evolution/${id}/resume`, {}),

  
  accounts: () => request("GET", "/api/accounts"),
  accountCreate: (payload) => request("POST", "/api/accounts", payload),
  accountSettings: (id, payload) => request("POST", `/api/accounts/${id}/settings`, payload),
  backtestStart: (payload) => request("POST", "/api/backtests", payload),
  accountArchive: (id, payload) => request("POST", `/api/accounts/${id}/archive`, payload),
  accountCashSet: (id, payload) => request("POST", `/api/accounts/${id}/cash`, payload),
  holdings: (account) => request("GET", `/api/portfolio/${account}/holdings`),
  trades: (account, params) =>
    request("GET", withQuery(`/api/portfolio/${account}/trades`, params)),
  portfolioSnapshots: (account) => request("GET", `/api/portfolio/${account}/snapshots`),
  plans: (account) => request("GET", `/api/portfolio/${account}/plans`),
  snapshotSubmit: (payload) => request("POST", "/api/portfolio/snapshot/submit", payload),
  snapshotCommit: (payload) => request("POST", "/api/portfolio/snapshot/commit", payload),
  tradesAdd: (payload) => request("POST", "/api/portfolio/trades/add", payload),
  tradesAppend: (payload) => request("POST", "/api/portfolio/trades/append", payload),
  tradesFix: (payload) => request("POST", "/api/portfolio/trades/fix", payload),
  reconcile: (account) => request("GET", `/api/portfolio/${account}/reconcile`),
  planSubmit: (payload) => request("POST", "/api/portfolio/plan/submit", payload),
  planTransition: (planId, payload) =>
    request("POST", `/api/portfolio/plan/${planId}/transition`, payload),
  recommendGenerate: (payload) => request("POST", "/api/portfolio/recommend", payload),
  planDetail: (account, planId) => request("GET", `/api/portfolio/${account}/plan/${planId}`),
  planReview: (planId, payload) => request("POST", `/api/portfolio/plan/${planId}/review`, payload),
  planCompareRun: (planId, payload) =>
    request("POST", `/api/portfolio/plan/${planId}/capital-comparison`, payload),
  planComparison: (account, planId) =>
    request("GET", `/api/portfolio/${account}/plan/${planId}/capital-comparison`),
  portfolioReinit: (account, payload) =>
    request("POST", `/api/portfolio/${account}/reinit`, payload),

  
  proposals: (params) => request("GET", withQuery("/api/proposals", params)),
  proposal: (id) => request("GET", `/api/proposals/${id}`),
  proposalTransition: (id, payload) =>
    request("POST", `/api/proposals/${id}/transition`, payload),
  trashList: (params) => request("GET", withQuery("/api/trash", params)),
  trashCategories: () => request("GET", "/api/trash/categories"),
  trashRestore: (id, payload) => request("POST", `/api/trash/${id}/restore`, payload),
  trashPurge: (id, payload) => request("POST", `/api/trash/${id}/purge`, payload),

  
  trashPurgeOlderThan: (payload) => request("POST", "/api/trash/purge-older-than", payload),
  doctorInit: (payload) => request("POST", "/api/doctor/init", payload),
  sessionMarket: (payload) => request("POST", "/api/session/market", payload),
  sessionFee: (payload) => request("POST", "/api/session/fee", payload),
};
