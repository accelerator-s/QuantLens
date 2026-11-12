from __future__ import annotations

import json
from typing import Any

from exceptions import ExperimentError

from .compare import primary_metric
from .store import ExperimentStore

_TASK = "structured_config"
_MAX_ATTEMPTS = 3

_SYSTEM = (
    "你是量化因子研究的复盘助手。根据实验档案输出 JSON 格式的 structured_config：\n"
    "- config_kind: 固定为 'experiment_review'\n"
    "- proposed_value: dict，必须包含：\n"
    "  - failure_reason: str，本次尝试未达预期的主要原因分析；若结果是改善的，"
    "则说明改善来源与仍存在的短板\n"
    "  - next_steps: list[str]，1-5 条具体可执行的下一步建议\n"
    "  - assessment: str，对本次实验的整体评价（2-4 句）\n"
    "- open_questions: list[str]，需要研究者线下确认的问题（可为空）\n\n"
    "硬性要求：输出必须是纯 JSON 对象；分析中禁止出现任何具体股票名称或股票代码；"
    "只依据给出的指标与档案，不得编造数据。"
)

def _metrics_digest(metrics: dict) -> dict:
    
    digest = {group: metrics.get(group) or {} for group in ("window", "returns", "risk", "trading")}
    orders = metrics.get("orders") or {}
    digest["orders"] = {
        "status_counts": orders.get("status_counts"),
        "lot_unaffordable_count": orders.get("lot_unaffordable_count"),
        "lot_unaffordable_amount_ratio": orders.get("lot_unaffordable_amount_ratio"),
    }
    return digest

def _build_prompt(store: ExperimentStore, exp_id: str) -> tuple[str, bool | None]:
    
    record = store.get(exp_id)
    result = store.latest_event(exp_id, "result")
    if result is None:
        raise ExperimentError(
            code="EXPERIMENT_NO_RESULT",
            message=f"实验 {exp_id} 尚无回测结果，无法复盘。",
            blocking=True,
            suggested_action=f"先运行 `experiment attach {exp_id} <run_id>` 挂载回测结果。",
        )
    comparison = store.latest_event(exp_id, "comparison")

    metrics = result.payload.get("metrics") or {}
    name, value = primary_metric(metrics)
    improved = comparison.payload.get("improved") if comparison else None

    sections: list[str] = [
        "实验档案：",
        json.dumps(
            {
                "exp_id": record.exp_id,
                "subject_type": record.subject_type,
                "subject_name": record.subject_name,
                "parent_exp_id": record.parent_exp_id,
                "修改了什么": record.change_summary,
                "为什么修改": record.change_reason,
                "训练区间": record.train_window,
                "验证区间": record.valid_window,
                "测试区间": record.test_window,
                "策略profile": record.profile_name,
            },
            ensure_ascii=False,
            indent=2,
        ),
        "\n本次回测指标：",
        json.dumps(_metrics_digest(metrics), ensure_ascii=False, indent=2),
        f"\n主指标 returns.{name} = {value}",
    ]
    if comparison:
        sections += [
            "\n与父版本对比（improved 由固定规则自动判定，不要推翻）：",
            json.dumps(comparison.payload, ensure_ascii=False, indent=2),
        ]
    else:
        sections.append("\n本实验为基线（无父版本），不做改善判定。")
    sections.append(
        "\n请复盘：失败原因（或改善来源与短板）、下一步建议、整体评价。"
        "按 system 中的 structured_config 格式输出。"
    )
    return "\n".join(sections), improved

def _validate_review(payload: dict) -> tuple[str, list[str], str]:
    
    value = payload.get("proposed_value") or {}
    failure_reason = value.get("failure_reason")
    next_steps = value.get("next_steps")
    if not isinstance(failure_reason, str) or not failure_reason.strip():
        raise ValueError("proposed_value.failure_reason 必须是非空字符串。")
    if (
        not isinstance(next_steps, list)
        or not next_steps
        or not all(isinstance(s, str) and s.strip() for s in next_steps)
    ):
        raise ValueError("proposed_value.next_steps 必须是非空的字符串列表。")
    assessment = value.get("assessment")
    return (
        failure_reason.strip(),
        [s.strip() for s in next_steps],
        assessment.strip() if isinstance(assessment, str) else "",
    )

def run_ai_analysis(
    store: ExperimentStore,
    exp_id: str,
    *,
    model_name: str | None = None,
) -> dict[str, Any]:
    
    from ai.router import AIRouter  
    from exceptions import ContractViolation, ModelRouteError

    prompt, improved = _build_prompt(store, exp_id)

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
            raise ExperimentError(
                code="EXPERIMENT_AI_CALL_FAILED",
                message=f"AI 复盘调用失败：{exc}",
                blocking=False,
                context={"exception": type(exc).__name__},
                suggested_action=f"检查网络后重试 `experiment analyze {exp_id}`。"
                "回测结果已入库，不受影响。",
            ) from exc
        try:
            failure_reason, next_steps, assessment = _validate_review(result)
        except ValueError as exc:
            last_detail = str(exc)
            continue
        analysis_payload = {
            "model": result.get("model_name"),
            "task": _TASK,
            "failure_reason": failure_reason,
            "next_steps": next_steps,
            "assessment": assessment,
            "open_questions": [str(q) for q in result.get("open_questions") or []],
        }
        store.append_event(exp_id, "ai_analysis", analysis_payload)
        store.append_event(
            exp_id,
            "conclusion",
            {
                "improved": improved,
                "failure_reason": failure_reason,
                "next_steps": next_steps,
                "source": "auto",
            },
        )
        return analysis_payload

    raise ExperimentError(
        code="EXPERIMENT_AI_OUTPUT_INVALID",
        message=f"AI 复盘输出连续 {_MAX_ATTEMPTS} 次未通过本地校验：{last_detail}",
        blocking=False,
        suggested_action=f"稍后用 `experiment analyze {exp_id}` 重试，或换模型"
        "（--model 指定注册表内模型名）。回测结果已入库，不受影响。",
    )

__all__ = ["run_ai_analysis"]
