"""Select once from metadata, then freeze the same plan for all research windows."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
import fcntl
from datetime import date
from paths import workspace
from ai.router import AIRouter
from exceptions import ContractViolation
from .catalogue import catalogue, catalogue_hash, coverage
from .models import BenchmarkPlan, BENCHMARK_NAMES


def available_indices(start: str, end: str) -> list[dict]:
    quotes = coverage()
    from market_rules.markets.cn_a.calendar import ChinaAShareCalendar
    calendar = ChinaAShareCalendar()
    effective_end = date.fromisoformat(end)
    if not calendar.is_trading_day(effective_end):
        effective_end = calendar.prev_trading_day(effective_end)
    return [{**row, 'coverage': quotes[row['index_code']]} for row in catalogue() if row["domestic_equity"] and row['index_code'] in quotes
               and str(quotes[row['index_code']]['first_date']) < start
               and str(quotes[row['index_code']]['last_date']) >= str(effective_end)]


def select_plan(profile: dict, start: str, end: str, *, model_name=None) -> dict:
    records = available_indices(start, end)
    choices = [{"code": row["index_code"], "name": row["name"], "return_type": row["return_type"],
                "start_date": row.get("start_date"), "market_eligible": row["market_eligible"],
                "coverage": row['coverage']} for row in records]
    if not any(row['market_eligible'] for row in records):
        raise ContractViolation(code='BENCHMARK_MARKET_DATA_INCOMPLETE', message='没有覆盖整个研究区间的整体市场参照，请先补齐 CSMAR 数据。')
    # Never send realized returns, holdings, IC, candidate scores or test outcomes.
    context = {"market": profile.get("market"), "universe": profile.get("universe"),
               "factors": profile.get("factors"), "hypothesis": (profile.get("extra") or {}).get("research_hypothesis", ""),
               "rebalance_frequency": profile.get('rebalance_frequency'), "holding_period": profile.get('holding_period'),
               "position_constraint": profile.get('position_constraint'), "constraints": profile.get('constraints'),
               "risk": profile.get('risk'),
               "start": start, "end": end,
               "computed_benchmarks": BENCHMARK_NAMES, "index_catalogue": choices,
               "plan_schema": BenchmarkPlan.model_json_schema()}
    output = AIRouter().run_task(task="benchmark_selection", model_name=model_name,
        system="选择回测前冻结的收益基准及整体市场参照。只按投资范围选择，不按回测业绩挑选。"
               "收益基准可选同风格簇等权、股票池等权、股票池流通市值加权、同行业等权、现金、全收益指数或预设权重混合。"
               "整体市场参照必须选代表所在股票市场的宽基指数，不用行业或风格指数冒充整体市场。"
               "指数收益基准及混合成分必须是目录标记total的人民币全收益指数；price只可作市场参照。"
               "不确定时不能编造代码、样本范围或全收益口径。输出JSON：return_benchmark{kind,code,components,cash_weight,rebalance}，"
               "严格遵守plan_schema。rebalance仅允许daily或monthly，用于混合基准；非混合基准填写monthly，"
               "components=[]、cash_weight=0；非index的code=null。"
               "market_reference（目录代码），rationale（匹配投资范围的理由），task=benchmark_selection，model_name。",
        prompt=json.dumps(context, ensure_ascii=False, default=str))
    plan = BenchmarkPlan.model_validate({key: output[key] for key in ["return_benchmark", "market_reference", "rationale"]})
    eligible = {row['index_code']: row for row in records}
    if plan.market_reference not in eligible or not eligible[plan.market_reference]['market_eligible']:
        raise ContractViolation(code='BENCHMARK_MARKET_SCOPE_INVALID', message='模型选择的整体市场参照不在已覆盖的宽基候选中。')
    legs = [c.code for c in plan.return_benchmark.components]
    if plan.return_benchmark.code:
        legs.append(plan.return_benchmark.code)
    if any(code not in eligible or eligible[code]['return_type'] != 'total' for code in legs):
        raise ContractViolation(code='BENCHMARK_TOTAL_RETURN_REQUIRED', message='模型选择的收益基准不是已覆盖的人民币全收益指数。')
    return {**plan.model_dump(mode="json"), "selection_model": output["model_name"],
            "selected_at": datetime.now(UTC).isoformat(), "selection_window": {"start": start, "end": end},
            "catalogue_hash": catalogue_hash(), "source": "csmar", "version": 1}


def plan_from_profile(profile: dict) -> BenchmarkPlan:
    raw = (profile.get("extra") or {}).get("benchmark_plan")
    if not raw:
        raise ContractViolation(code="BENCHMARK_PLAN_REQUIRED", message="回测前必须选择并冻结基准方案。", blocking=True)
    return BenchmarkPlan.model_validate({k: raw[k] for k in ["return_benchmark", "market_reference", "rationale"]})


def freeze_profile(profile, start, end, *, model_name=None, key=None):
    payload = profile.model_dump(mode="json")
    if not (payload.get("extra") or {}).get("benchmark_plan"):
        identity = json.dumps({"profile": payload, "start": str(start), "end": str(end), "key": key}, sort_keys=True, default=str)
        digest = hashlib.sha256(identity.encode()).hexdigest()
        root = workspace().runs / 'benchmark_plans'
        root.mkdir(parents=True, exist_ok=True)
        path = root / f'{digest}.json'
        with (root / f'{digest}.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if path.exists():
                raw = json.loads(path.read_text())
                if raw.get('source') != 'csmar' or raw.get('version') != 1:
                    raise ContractViolation(code='BENCHMARK_SNAPSHOT_INVALID', message='冻结的基准方案无效。')
            else:
                raw = select_plan(payload, str(start), str(end), model_name=model_name)
                pending = path.with_suffix('.pending')
                pending.write_text(json.dumps(raw, ensure_ascii=False, allow_nan=False))
                pending.replace(path)
        profile = profile.model_copy(update={"extra": {**profile.extra, "benchmark_plan": raw}})
    return profile
