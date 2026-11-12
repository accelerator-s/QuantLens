from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from exceptions import IssueError, IssueNotFoundError
from paths import Workspace, workspace

from .ids import generate_issue_id
from .lifecycle import Status, assert_transition
from .models import CreatedBy, Issue, IssueType, Priority
from .templates import render_template

_FRONTMATTER_DELIM = "---"
_INVALID_CHARS = re.compile(r"[\\/\:\*\?\"<>\|\r\n\t]+")

def _slugify(title: str, max_len: int = 60) -> str:
    cleaned = _INVALID_CHARS.sub("-", title).strip(" .-_")
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    return cleaned[:max_len] or "untitled"

def _date_prefix(dt: datetime) -> str:
    return dt.astimezone().strftime("%Y-%m-%d")

def _bucket_dir(ws: Workspace, status: Status) -> Path:
    
    if status in (Status.SOLVED, Status.REJECTED):
        return ws.issues_solved
    return ws.issues_open

def _serialize(issue: Issue) -> str:
    return issue.model_dump_json(indent=2)

def _parse(text: str) -> Issue:
    return Issue.model_validate_json(text)

class IssueStore:

    def __init__(self, ws: Workspace | None = None) -> None:
        self.ws = ws or workspace()

    def create(
        self,
        *,
        type: IssueType,
        title: str,
        priority: Priority = Priority.P2,
        created_by: CreatedBy = CreatedBy.HUMAN,
        owner: str = "",
        background: str = "",
        goal: str = "",
        scope: str = "",
        acceptance: str = "",
        risks: str = "",
        constraint_index: list[str] | None = None,
        related_files: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> tuple[Issue, Path]:
        
        issue = Issue(
            issue_id=generate_issue_id(),
            type=type,
            status=Status.OPEN,
            priority=priority,
            created_by=created_by,
            owner=owner,
            human_confirmed=False,
            title=title,
            background=background,
            goal=goal,
            scope=scope,
            acceptance=acceptance,
            risks=risks,
            constraint_index=constraint_index or [],
            related_files=related_files or [],
            extra=extra or {},
        )
        path = self._path_for(issue)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise IssueError(
                code="ISSUE_PATH_CONFLICT",
                message=f"目标路径已存在：{path}",
                blocking=True,
            )
        path.write_text(_serialize(issue), encoding="utf-8")
        return issue, path

    def load(self, issue_id: str) -> tuple[Issue, Path]:
        for path in self._iter_files():
            issue = _parse(path.read_text(encoding="utf-8"))
            if issue.issue_id == issue_id:
                return issue, path
        raise IssueNotFoundError(
            code="ISSUE_NOT_FOUND",
            message=f"未找到 issue_id={issue_id}",
            context={"issue_id": issue_id},
            blocking=True,
        )

    def list(
        self,
        *,
        status: Status | None = None,
        type: IssueType | None = None,
    ) -> list[tuple[Issue, Path]]:
        out: list[tuple[Issue, Path]] = []
        for path in self._iter_files():
            issue = _parse(path.read_text(encoding="utf-8"))
            if status is not None and issue.status != status:
                continue
            if type is not None and issue.type != type:
                continue
            out.append((issue, path))
        out.sort(key=lambda x: x[0].created_at, reverse=True)
        return out

    def transition(
        self,
        issue_id: str,
        target: Status,
        *,
        human_confirmed: bool = False,
        resolution_summary: str = "",
    ) -> tuple[Issue, Path]:
        
        issue, old_path = self.load(issue_id)
        assert_transition(issue.status, target)

        if target in (Status.ACCEPTED, Status.SOLVED) and not human_confirmed:
            raise IssueError(
                code="ISSUE_HUMAN_CONFIRM_REQUIRED",
                message=f"迁移到 {target.value} 需要人工确认。",
                context={"issue_id": issue_id, "target": target.value},
                blocking=True,
                suggested_action="重新执行命令并加上 --confirm。",
            )

        issue.status = target
        if human_confirmed:
            issue.human_confirmed = True
        if resolution_summary:
            issue.resolution_summary = resolution_summary
        issue.touch()

        new_path = self._path_for(issue, original=old_path)
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_text(_serialize(issue), encoding="utf-8")
        if new_path != old_path:
            old_path.unlink(missing_ok=True)
        return issue, new_path

    def _iter_files(self) -> Iterable[Path]:
        for base in (self.ws.issues_open, self.ws.issues_solved):
            if not base.exists():
                continue
            yield from base.rglob("*.json")

    def _path_for(self, issue: Issue, original: Path | None = None) -> Path:
        
        bucket = _bucket_dir(self.ws, issue.status)
        if original is not None:
            return bucket / issue.type.value / original.name
        filename = f"{_date_prefix(issue.created_at)}_{_slugify(issue.title)}.json"
        return bucket / issue.type.value / filename
