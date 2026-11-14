"""Bounded evidence dialogue shared by configured AI and development analysts."""
from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from exceptions import QuantTrainingError
from .diagnostics import EvidenceQueries, fail


class Citation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    evidence_id: str
    path: list[str] = Field(min_length=1)
    value: Any


class AnalysisAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['query', 'hypothesis', 'stop']
    section: Literal['overview', 'annual', 'costs', 'drawdown', 'signal', 'limitations'] | None = None
    observation: str = ''
    hypothesis: str = ''
    alternative: str = ''
    falsification: str = ''
    change_type: Literal['formula', 'lookback', 'winsorization', 'neutralization', 'missing',
                         'direction', 'market_regime', 'orthogonalization'] | None = None
    primary_metric: Literal['prediction', 'net_return', 'drawdown'] | None = None
    evidence_refs: list[Citation] = Field(default_factory=list)
    stop_reason: str = ''

    @model_validator(mode='after')
    def complete(self):
        if self.action == 'query' and self.section is None:
            raise ValueError('query 必须指定 section')
        if self.action == 'hypothesis' and (not all([self.observation.strip(), self.hypothesis.strip(),
                self.alternative.strip(), self.falsification.strip(), self.change_type,
                self.primary_metric]) or not self.evidence_refs):
            raise ValueError('假设必须包含观察、机制、替代解释、反证、唯一改动和证据引用')
        if self.action == 'stop' and not self.stop_reason.strip():
            raise ValueError('停止分析必须说明原因')
        return self


def analyze(evidence, responder, *, max_turns, timeout_seconds, on_progress=None):
    queries = EvidenceQueries(evidence)
    # Mandatory evidence cannot be omitted by a persuasive but incomplete story.
    packets = [queries.query(k) for k in ('overview', 'signal', 'costs', 'drawdown', 'limitations')]
    transcript = []
    deadline = time.monotonic() + timeout_seconds
    for turn in range(max_turns):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise QuantTrainingError(code='EVOLUTION_ANALYSIS_TIMEOUT', message='训练证据分析超时。', blocking=True)
        if on_progress:
            on_progress(f'训练证据分析 {turn + 1}/{max_turns}；验证与测试不可见')
        context = {'stage': 'training_only_diagnosis', 'evidence': packets,
                   'dialogue': transcript, 'turns_remaining': max_turns - turn,
                   'schema': AnalysisAction.model_json_schema()}
        action = AnalysisAction.model_validate(responder(context, remaining))
        if time.monotonic() > deadline:
            raise QuantTrainingError(code='EVOLUTION_ANALYSIS_TIMEOUT', message='训练证据分析超时。', blocking=True)
        transcript.append(action.model_dump(mode='json'))
        if action.action == 'query':
            packet = queries.query(action.section)
            if not any(p['evidence_id'] == packet['evidence_id'] for p in packets):
                packets.append(packet)
            continue
        if action.action == 'hypothesis':
            queries.verify(action.evidence_refs)
        return {'decision': action.model_dump(mode='json'), 'dialogue': transcript,
                'evidence_ids': [p['evidence_id'] for p in packets],
                'run_id': evidence['run_id'], 'definition_hash': evidence['definition_hash'],
                'comparison_hash': evidence['comparison_hash']}
    fail('分析达到轮数上限仍未提出有证据的假设或停止结论。')
