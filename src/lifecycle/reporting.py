from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import yaml

from exceptions import LifecycleError
from paths import workspace

from .models import (
    DomainObjectRef,
    EvaluationRun,
    MetricCitation,
    ObjectType,
    ResearchProgram,
    ResearchReport,
)
from .states import ActorType
from .store import LifecycleStore

_AI_REPORT_SYSTEM = """你是量化研究报告助手。输出 StructuredConfig 根对象，config_kind 固定为
research_report_narrative。proposed_value 必须仅包含以下非空中文字符串：
- financial_phenomenon：可观察的金融现象；
- transmission_mechanism：从行为偏差、风险补偿、信息扩散或制度约束到收益的传导链；
- falsifiable_hypothesis：能被后续数据推翻的预测；
- persistence_conditions：现象可能继续存在的条件；
- invalidation_conditions：应降权、停用或重新研究的失效信号；
- plain_conclusion：不使用术语堆叠的结论。
只能使用提供的假设和证据，不得补造数据。不得写任何具体数值或百分比，数值由程序从
artifact 逐项引用。必须明确历史样本外表现不代表未来，不得承诺稳定盈利。
""".strip()

_NARRATIVE_FIELDS = (
    "financial_phenomenon",
    "transmission_mechanism",
    "falsifiable_hypothesis",
    "persistence_conditions",
    "invalidation_conditions",
    "plain_conclusion",
)

def _hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _file_hash(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def _get(payload: dict[str, Any], path: str) -> Any:
    value: Any = payload
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value

def _render(value: Any, *, percent: bool) -> str:
    number = float(value)
    return f"{number:.2%}" if percent else f"{number:.4f}"

class ResearchReportService:
    def __init__(self, store: LifecycleStore | None = None) -> None:
        self.store = store or LifecycleStore()

    def generate(self, program_id: str) -> ResearchReport:
        program = self.store.get(ObjectType.RESEARCH_PROGRAM, program_id)
        assert isinstance(program, ResearchProgram)
        subject_type = (
            ObjectType.FACTOR_VERSION
            if program.loop.value == "factor_research"
            else ObjectType.STRATEGY_VERSION
        )
        subjects = self._subjects(subject_type, program_id)
        if not subjects:
            raise LifecycleError(
                code="RESEARCH_REPORT_SUBJECT_MISSING",
                message=f"研究任务 {program_id} 没有可引用的版本对象。",
                blocking=True,
                suggested_action="先完整登记因子/策略版本后重新生成报告。",
            )
        runs = self._runs(program_id)
        if not runs:
            raise LifecycleError(
                code="RESEARCH_REPORT_METRICS_MISSING",
                message=f"研究任务 {program_id} 没有已完成的指标 artifact。",
                blocking=True,
                suggested_action="先完成评价运行并冻结 metrics.yaml。",
            )
        citations: list[MetricCitation] = []
        rows: list[tuple[str, str, str]] = []
        paths = (
            ("年化超额收益", "returns.annualized_excess_return", True),
            ("Sharpe", "risk.sharpe", False),
            ("最大回撤", "risk.max_drawdown", True),
            ("年化单边换手", "trading.annualized_turnover_oneside", False),
        )
        for run, metrics in runs:
            for label, value_path, percent in paths:
                value = _get(metrics, value_path)
                if value is None:
                    continue
                rendered = _render(value, percent=percent)
                if rendered != _render(_get(metrics, value_path), percent=percent):
                    raise LifecycleError(
                        code="RESEARCH_REPORT_CITATION_MISMATCH",
                        message=f"报告数值引用校验失败：{run.evaluation_run_id}/{value_path}",
                        blocking=True,
                        suggested_action="不得手工改写报告数值；从原 artifact 重新生成。",
                    )
                citations.append(
                    MetricCitation(
                        metric_name=f"{run.evaluation_run_id}:{value_path}",
                        rendered_value=rendered,
                        artifact_ref=f"{run.artifact_ref}/metrics.yaml",
                        artifact_hash=_file_hash(
                            workspace().root / run.artifact_ref / "metrics.yaml"
                        ),
                        value_path=value_path,
                    )
                )
                rows.append((run.purpose.value, label, rendered))
        if not citations:
            raise LifecycleError(
                code="RESEARCH_REPORT_PRIMARY_METRICS_MISSING",
                message="回测 artifact 不包含报告要求的收益、风险或换手指标。",
                blocking=True,
                suggested_action="修复回测报告口径后重新生成。",
            )
        narrative = self._ai_narrative(program, subject_type, subjects, runs)
        report_id = f"RR-{_hash([program_id, narrative, [item.model_dump(mode='json') for item in citations]])[:20].upper()}"
        artifact = workspace().runs / "research_reports" / f"{report_id}.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# 研究报告：{program.objective}",
            "",
            f"- 研究任务：`{program_id}`",
            f"- 数据快照：`{program.data_snapshot_id}`",
            f"- 固定协议哈希：`{program.protocol_hash}`",
            f"- 版本数：{len(subjects)}",
            f"- AI 解读模型：`{narrative['_model_name']}`",
            "",
            "## 金融现象与传导机制",
            "",
            narrative["financial_phenomenon"],
            "",
            narrative["transmission_mechanism"],
            "",
            "## 可证伪预测",
            "",
            narrative["falsifiable_hypothesis"],
            "",
            "## 为何可能持续",
            "",
            narrative["persistence_conditions"],
            "",
            "## 失效条件",
            "",
            narrative["invalidation_conditions"],
            "",
            "## 通俗结论",
            "",
            narrative["plain_conclusion"],
            "",
            "## 可核验指标",
            "",
            "| 评价用途 | 指标 | 数值 |",
            "| --- | --- | ---: |",
            *(f"| {purpose} | {label} | {value} |" for purpose, label, value in rows),
            "",
            "## 局限",
            "",
            "- 所有数值是特定数据快照、交易成本和样本切分下的历史样本外结果。",
            "- 因子去重使用验证期横截面排名相关性；多次试验使用折级符号检验和 Bonferroni 校正，折数较少时统计功效有限。",
            "- 锁定测试曝光后不得围绕该区间继续调参。",
            "",
            "本报告不承诺未来收益；“预期”仅指上述固定口径下的历史样本外统计。",
        ]
        artifact.write_text(json.dumps({"content":"\n".join(lines)},ensure_ascii=False), encoding="utf-8")
        exposure_count = len(
            [
                row
                for row in self.store.list_objects(ObjectType.LOCKED_TEST_EXPOSURE, limit=5000)
                if self.store.get(ObjectType.LOCKED_TEST_EXPOSURE, row["object_id"]).research_program_id
                == program_id
            ]
        )
        record = ResearchReport(
            research_report_id=report_id,
            research_program_id=program_id,
            subject_refs=[
                DomainObjectRef(object_type=subject_type, object_id=subject_id)
                for subject_id in subjects
            ],
            report_kind="factor" if subject_type is ObjectType.FACTOR_VERSION else "strategy",
            report_artifact_ref=str(artifact.relative_to(workspace().root)),
            metric_citations=citations,
            trial_count=len(subjects),
            locked_test_exposure_count=exposure_count,
            failed_candidate_count=sum(
                self.store.current_state(subject_type, subject_id)
                in {"invalid", "eliminated", "rejected"}
                for subject_id in subjects
            ),
            limitations=[
                "历史样本外结果不代表未来结果",
                "数据、容量和市场状态变化会使策略失效",
            ],
            future_return_disclaimer="仅报告特定口径的历史样本外统计，不承诺未来收益。",
        )
        try:
            self.store.add(record)
        except LifecycleError as exc:
            if exc.code != "LIFECYCLE_OBJECT_EXISTS":
                raise
            existing = self.store.get(ObjectType.RESEARCH_REPORT, report_id)
            assert isinstance(existing, ResearchReport)
            return existing
        self.store.transition(
            ObjectType.RESEARCH_REPORT,
            report_id,
            "validated",
            actor_type=ActorType.SYSTEM,
            actor_ref="research_reporting",
            reason="所有渲染数值已逐项回读 artifact 验证",
        )
        return record

    def _ai_narrative(
        self,
        program: ResearchProgram,
        subject_type: ObjectType,
        subject_ids: list[str],
        runs: list[tuple[EvaluationRun, dict[str, Any]]],
    ) -> dict[str, str]:
        
        from ai.router import AIRouter

        subjects = [self.store.get(subject_type, subject_id) for subject_id in subject_ids]
        prompt = json.dumps(
            {
                "objective": program.objective,
                "hypothesis_scope": program.hypothesis_scope,
                "subjects": [
                    {
                        key: value
                        for key, value in subject.model_dump(mode="json").items()
                        if key
                        in {
                            "factor_name",
                            "hypothesis",
                            "change_dimension",
                            "change_summary",
                            "strategy_name",
                        }
                    }
                    for subject in subjects
                ],
                "evidence": [
                    {
                        "purpose": run.purpose.value,
                        "outcome": run.outcome.value,
                        "metrics": {
                            "returns": metrics.get("returns") or {},
                            "risk": metrics.get("risk") or {},
                            "trading": metrics.get("trading") or {},
                        },
                    }
                    for run, metrics in runs
                ],
                "hard_rule": "不得输出任何具体数值，不得承诺未来收益",
            },
            ensure_ascii=False,
            indent=2,
        )
        model_name = str(program.configuration.get("model_name") or "") or None
        payload = AIRouter().run_task(
            task="structured_config",
            prompt=prompt,
            system=_AI_REPORT_SYSTEM,
            model_name=model_name,
        )
        if payload.get("config_kind") != "research_report_narrative":
            raise LifecycleError(
                code="RESEARCH_REPORT_AI_KIND_INVALID",
                message="AI 报告返回了错误的 config_kind。",
                blocking=True,
                suggested_action="修正当前模型的结构化输出后重新生成报告。",
            )
        value = payload.get("proposed_value") or {}
        narrative: dict[str, str] = {}
        for field in _NARRATIVE_FIELDS:
            text = value.get(field)
            if not isinstance(text, str) or not text.strip():
                raise LifecycleError(
                    code="RESEARCH_REPORT_AI_FIELD_MISSING",
                    message=f"AI 报告缺少非空字段：{field}。",
                    blocking=True,
                    suggested_action="按报告 schema 补齐金融现象、传导机制和失效条件。",
                )
            if re.search(r"\d|%|％|百分之", text):
                raise LifecycleError(
                    code="RESEARCH_REPORT_AI_NUMERIC_CLAIM",
                    message=f"AI 报告字段 {field} 包含未经 artifact 引用的数值。",
                    blocking=True,
                    suggested_action="让 AI 只解释现象与机制，数值由报告程序自动引用。",
                )
            narrative[field] = text.strip()
        narrative["_model_name"] = str(payload.get("model_name") or "")
        return narrative

    def verify(self, report_id: str) -> dict[str, int]:
        
        report = self.store.get(ObjectType.RESEARCH_REPORT, report_id)
        assert isinstance(report, ResearchReport)
        root = workspace().root.resolve()
        checked = 0
        for citation in report.metric_citations:
            path = (workspace().root / citation.artifact_ref).resolve()
            if root not in path.parents or not path.is_file():
                raise LifecycleError(
                    code="RESEARCH_REPORT_CITATION_ARTIFACT_MISSING",
                    message=f"报告引用不存在或越界：{citation.artifact_ref}",
                    blocking=True,
                    suggested_action="恢复原始指标 artifact 后重新生成报告。",
                )
            actual_hash = _file_hash(path)
            if actual_hash != citation.artifact_hash:
                raise LifecycleError(
                    code="RESEARCH_REPORT_CITATION_HASH_MISMATCH",
                    message=f"报告引用已变化：{citation.artifact_ref}",
                    context={
                        "expected_hash": citation.artifact_hash,
                        "actual_hash": actual_hash,
                    },
                    blocking=True,
                    suggested_action="停止批准并核查 artifact 完整性。",
                )
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            value = _get(payload, citation.value_path)
            if value is None or _render(
                value, percent=citation.rendered_value.endswith("%")
            ) != citation.rendered_value:
                raise LifecycleError(
                    code="RESEARCH_REPORT_CITATION_VALUE_MISMATCH",
                    message=f"报告展示值无法由原指标复现：{citation.metric_name}",
                    blocking=True,
                    suggested_action="不得手改报告数值；从原 artifact 重新生成。",
                )
            checked += 1
        return {"checked_citations": checked}

    def _subjects(self, kind: ObjectType, program_id: str) -> list[str]:
        result = []
        for row in reversed(self.store.list_objects(kind, limit=5000)):
            record = self.store.get(kind, row["object_id"])
            if record.research_program_id == program_id:
                result.append(row["object_id"])
        return result

    def _runs(self, program_id: str) -> list[tuple[EvaluationRun, dict[str, Any]]]:
        result = []
        for row in self.store.list_objects(ObjectType.EVALUATION_RUN, limit=5000):
            if row["state"] != "completed":
                continue
            run = self.store.get(ObjectType.EVALUATION_RUN, row["object_id"])
            assert isinstance(run, EvaluationRun)
            if run.research_program_id != program_id or run.artifact_ref.startswith("lifecycle://"):
                continue
            path = workspace().root / run.artifact_ref / "metrics.yaml"
            if not path.is_file():
                continue
            metrics = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            result.append((run, metrics))
        return result

__all__ = ["ResearchReportService"]
