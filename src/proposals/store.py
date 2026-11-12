from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import yaml

from ai.schemas import validate_ai_output
from exceptions import QuantTrainingError
from paths import Workspace, workspace

from .models import (
    Proposal,
    ProposalSource,
    ProposalStatus,
    assert_proposal_transition,
)

class ProposalStore:

    def __init__(self, ws: Workspace | None = None) -> None:
        self.ws = ws or workspace()

    def create(
        self,
        *,
        role: str,
        source: ProposalSource,
        payload: dict[str, object],
        model_name: str = "",
        related_issue_id: str = "",
    ) -> tuple[Proposal, Path]:

        payload = {**payload}
        payload.setdefault("task", role)
        payload.setdefault("model_name", model_name)
        if related_issue_id:
            payload.setdefault("related_issue_id", related_issue_id)
        if payload.get("task") != role:
            raise QuantTrainingError(
                code="PROPOSAL_ROLE_MISMATCH",
                message=f"role={role} 与 payload.task={payload.get('task')} 不一致。",
                blocking=True,
            )
        validated = validate_ai_output(role, payload)

        proposal = Proposal(
            source=source,
            role=role,
            model_name=model_name,
            related_issue_id=related_issue_id,
            payload=validated.model_dump(mode="json"),
        )
        path = self._path_for(proposal)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise QuantTrainingError(
                code="PROPOSAL_PATH_CONFLICT",
                message=f"proposal 路径冲突：{path}",
                blocking=True,
            )
        path.write_text(self._dump(proposal), encoding="utf-8")
        return proposal, path

    def load(self, proposal_id: str) -> tuple[Proposal, Path]:
        for p in self._iter_files():
            try:
                proposal = self._parse(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if proposal.proposal_id == proposal_id:
                return proposal, p
        raise QuantTrainingError(
            code="PROPOSAL_NOT_FOUND",
            message=f"未找到 proposal：{proposal_id}",
            blocking=True,
        )

    def list(
        self,
        *,
        role: str | None = None,
        status: ProposalStatus | None = None,
    ) -> list[tuple[Proposal, Path]]:
        out: list[tuple[Proposal, Path]] = []
        for p in self._iter_files():
            try:
                proposal = self._parse(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if role and proposal.role != role:
                continue
            if status and proposal.status != status:
                continue
            out.append((proposal, p))
        out.sort(key=lambda x: x[0].created_at, reverse=True)
        return out

    def transition(
        self,
        proposal_id: str,
        target: ProposalStatus,
        *,
        decided_by: str,
        note: str = "",
    ) -> tuple[Proposal, Path]:
        proposal, path = self.load(proposal_id)
        assert_proposal_transition(proposal.status, target)
        if not decided_by.strip():
            raise QuantTrainingError(
                code="PROPOSAL_DECIDER_REQUIRED",
                message=f"proposal {proposal_id} 状态变更必须填写决策人 (--by)。",
                blocking=True,
            )
        proposal.status = target
        proposal.decided_by = decided_by.strip()
        proposal.decision_note = note
        proposal.touch()
        path.write_text(self._dump(proposal), encoding="utf-8")
        return proposal, path

    def _iter_files(self) -> Iterable[Path]:
        if not self.ws.proposals.exists():
            return iter(())
        return self.ws.proposals.rglob("*.yaml")

    def _path_for(self, proposal: Proposal) -> Path:
        date = proposal.created_at.astimezone().strftime("%Y-%m-%d")
        return self.ws.proposals / proposal.role / f"{date}_{proposal.proposal_id}.yaml"

    @staticmethod
    def _dump(proposal: Proposal) -> str:
        return yaml.safe_dump(
            proposal.model_dump(mode="json"),
            allow_unicode=True,
            sort_keys=False,
        )

    @staticmethod
    def _parse(text: str) -> Proposal:
        raw = yaml.safe_load(text) or {}
        return Proposal.model_validate(raw)
