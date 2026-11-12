from __future__ import annotations

import json
from typing import Any

from exceptions import PortfolioContractError

from .models import Account, Plan
from .store import PortfolioStore

_TASK = "structured_config"
_MAX_ATTEMPTS = 3

_SYSTEM = (
    "你是量化持仓决策的解读助手。根据规则引擎生成的调仓计划输出 JSON 格式的 "
    "structured_config：\n"
    "- config_kind: 固定为 'plan_review'\n"
    "- proposed_value: dict，必须包含：\n"
    "  - trigger_reason: str，本次调仓的核心理由（依据因子信号与仓位约束，2-4 句）\n"
    "  - caveats: list[str]，执行层面的风险提示（可为空列表；如资金错配、"
    "集中度、冻结持仓等）\n"
    "  - change_notes: str，与上一期计划的主要变化；无上期计划时说明这是首个计划\n"
    "- open_questions: list[str]，需要持有人线下确认的问题（可为空）\n\n"
    "硬性要求：输出必须是纯 JSON 对象；只依据给出的计划数据，不得编造行情或预测收益；"
    "计划由确定性规则引擎生成，你只负责解读，不得建议修改计划本身。"
)

def _plan_digest(plan: Plan) -> dict:
    return {
        "plan_id": plan.plan_id,
        "strategy_profile": plan.strategy_profile_ref,
        "generated_at": plan.generated_at.isoformat(),
        "effective_window": {
            "start": plan.effective_window.start.date().isoformat(),
            "end": plan.effective_window.end.date().isoformat(),
        },
        "data_manifest_ref": plan.data_manifest_ref,
        "target_holdings": [
            {"instrument_id": t.instrument_id, "qty": str(t.qty), "weight": str(t.weight)}
            for t in plan.target_holdings
        ],
        "suggested_trades": [
            {
                "instrument_id": t.instrument_id,
                "action": t.action.value,
                "qty": str(t.qty),
                "reference_price": str(t.reference_price),
                "estimated_cost": str(t.estimated_cost),
            }
            for t in plan.suggested_trades
        ],
        "rule_caveats": list(plan.caveats),
        "estimated_fees_by_currency": dict(plan.estimated_fees_by_currency),
    }

def _previous_digest(previous_plan: dict | None) -> dict | None:
    if not previous_plan:
        return None
    return {
        "plan_id": previous_plan.get("plan_id"),
        "generated_at": previous_plan.get("generated_at"),
        "state": previous_plan.get("state"),
        "target_holdings": [
            {"instrument_id": t.get("instrument_id"), "qty": t.get("qty")}
            for t in (previous_plan.get("target_holdings") or [])
        ],
        "trade_count": len(previous_plan.get("suggested_trades") or []),
        "caveats": list(previous_plan.get("caveats") or []),
    }

def _build_prompt(plan: Plan, account: Account, previous_plan: dict | None) -> str:
    sections = [
        f"账户：{account.account_id}（{account.market}/{account.instrument_class}，"
        f"计价 {account.reporting_currency}）",
        "\n本期调仓计划（规则引擎产出）：",
        json.dumps(_plan_digest(plan), ensure_ascii=False, indent=2),
    ]
    previous = _previous_digest(previous_plan)
    if previous is not None:
        sections += [
            "\n上一期计划摘要：",
            json.dumps(previous, ensure_ascii=False, indent=2),
        ]
    else:
        sections.append("\n该策略在此账户下没有上一期计划（首个计划）。")
    sections.append(
        "\n请解读：调仓理由、执行风险提示、与上期变化。按 system 中的 structured_config 格式输出。"
    )
    return "\n".join(sections)

def _validate_review(payload: dict) -> tuple[str, list[str], str]:
    
    value = payload.get("proposed_value") or {}
    trigger_reason = value.get("trigger_reason")
    caveats = value.get("caveats")
    change_notes = value.get("change_notes")
    if not isinstance(trigger_reason, str) or not trigger_reason.strip():
        raise ValueError("proposed_value.trigger_reason 必须是非空字符串。")
    if not isinstance(caveats, list) or not all(isinstance(c, str) for c in caveats):
        raise ValueError("proposed_value.caveats 必须是字符串列表（可为空列表）。")
    if not isinstance(change_notes, str) or not change_notes.strip():
        raise ValueError("proposed_value.change_notes 必须是非空字符串。")
    return (
        trigger_reason.strip(),
        [c.strip() for c in caveats if c.strip()],
        change_notes.strip(),
    )

def annotate_plan(
    plan: Plan,
    *,
    account: Account,
    previous_plan: dict | None = None,
    model_name: str | None = None,
) -> dict[str, Any]:
    
    from ai.router import AIRouter  
    from exceptions import ContractViolation, ModelRouteError  

    prompt = _build_prompt(plan, account, previous_plan)
    router = AIRouter()
    last_detail = ""
    for attempt in range(_MAX_ATTEMPTS):
        try:
            result = router.run_task(
                task=_TASK,
                prompt=prompt
                if attempt == 0
                else f"{prompt}\n\n注意：你上一次输出校验失败：{last_detail}\n请修正后重新输出。",
                system=_SYSTEM,
                model_name=model_name,
            )
        except (ContractViolation, ModelRouteError):
            raise  
        except Exception as exc:  
            raise PortfolioContractError(
                code="portfolio.plan_review_call_failed",
                message=f"计划 AI 解读调用失败：{exc}",
                blocking=False,
                context={"plan_id": plan.plan_id, "exception": type(exc).__name__},
                suggested_action=(
                    f"检查网络后用 `recommend review {plan.plan_id} "
                    f"--account {account.account_id}` 重试；计划本身已生成，不受影响。"
                ),
            ) from exc
        try:
            trigger_reason, caveats, change_notes = _validate_review(result)
        except ValueError as exc:
            last_detail = str(exc)
            continue
        merged_reason = f"{trigger_reason}\n\n与上期变化：{change_notes}"
        PortfolioStore().update_plan_annotation(
            plan.plan_id,
            primary_account=account.account_id,
            trigger_reason=merged_reason,
            extra_caveats=caveats,
        )
        return {
            "model": result.get("model_name"),
            "task": _TASK,
            "trigger_reason": trigger_reason,
            "caveats": caveats,
            "change_notes": change_notes,
            "open_questions": [str(q) for q in result.get("open_questions") or []],
        }

    raise PortfolioContractError(
        code="portfolio.plan_review_failed",
        message=f"计划 AI 解读输出连续 {_MAX_ATTEMPTS} 次未通过本地校验：{last_detail}",
        blocking=False,
        context={"plan_id": plan.plan_id},
        suggested_action=(
            f"稍后用 `recommend review {plan.plan_id} --account {account.account_id}` 重试，"
            "或加 --model 指定注册表内模型名；计划本身已生成，不受影响。"
        ),
    )

__all__ = ["annotate_plan"]
