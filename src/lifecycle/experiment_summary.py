"""Generate a terminal experiment sentence without feeding it back into search."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExperimentSentence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    conclusion: str = Field(min_length=1, max_length=240)

    @field_validator('conclusion')
    @classmethod
    def single_line(cls, value):
        value = value.strip()
        if not value or '\n' in value or '\r' in value:
            raise ValueError('实验总结须为一句非空文字')
        return value


def generate_summary(evidence: dict, *, model_name=None, router=None) -> dict:
    from ai.registry import ModelRegistry
    from ai.router import AIRouter

    router = router or AIRouter(request_timeout=30)
    selected = router.pick('structured_config', model_name=model_name or ModelRegistry().get_discuss_model() or None)
    raw = json.dumps(evidence, ensure_ascii=False, sort_keys=True, allow_nan=False, default=str)
    payload = router.run_task(task='structured_config', model_name=selected.name,
        system='依据提供的实验终态事实，用一句简短中文说明本次实验结果。直接说明候选是否优于基线、'
               '实际完成或跳过哪些阶段、是否通过既定条件；只写证据支持的内容。'
               '不要寒暄、标题、列表、Markdown、建议或补造指标，不把未运行说成失败。'
               '这是实验结束后的展示总结，不参与选模、调参或启停决策。'
               '只返回 StructuredConfig，config_kind=experiment_summary，'
               'proposed_value 仅含 conclusion 字符串。',
        prompt=json.dumps({'evidence': json.loads(raw), 'schema': ExperimentSentence.model_json_schema()}, ensure_ascii=False))
    if payload.get('config_kind') != 'experiment_summary':
        raise ValueError('模型未返回实验总结')
    sentence = ExperimentSentence.model_validate(payload.get('proposed_value') or {})
    return {**sentence.model_dump(), 'ai_generated': True, 'model_name': selected.name,
            'created_at': datetime.now(UTC).isoformat(), 'evidence_hash': hashlib.sha256(raw.encode()).hexdigest()}
