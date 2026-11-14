from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from exceptions import QuantTrainingError
from paths import Workspace, workspace
from security import confined_path

@dataclass
class FactorPlugin:

    ws: Workspace

    @classmethod
    def default(cls) -> FactorPlugin:
        return cls(ws=workspace())

    @property
    def root(self) -> Path:
        return self.ws.root / "factors"

    def path_for(self, name: str) -> Path:
        return confined_path(self.root, name, ".py")

    def write(self, name: str, source: str, *, override: bool = False) -> Path:
        path = self.path_for(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not override:
            raise QuantTrainingError(
                code="FACTOR_PLUGIN_EXISTS",
                message=f"因子源码已存在：{path}",
                blocking=True,
                suggested_action="加 --override 强制覆盖（先记录因子改动并创建新版本）。",
            )
        path.write_text(source, encoding="utf-8")
        return path

    def read(self, name: str) -> str:
        path = self.path_for(name)
        if not path.exists():
            raise QuantTrainingError(
                code="FACTOR_PLUGIN_MISSING",
                message=f"因子源码不存在：{path}",
                blocking=True,
            )
        return path.read_text(encoding="utf-8")

__all__ = ["FactorPlugin"]
