"""Path confinement for backend stores."""
import re
from pathlib import Path
from exceptions import ContractViolation

def confined_path(root: Path, name: str, suffix: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,127}", name) or name == "_meta":
        raise ContractViolation(code="UNSAFE_PATH", message="文件名称不合法。", blocking=True)
    path = root / f"{name}{suffix}"
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ContractViolation(code="UNSAFE_PATH", message="文件路径超出允许目录。", blocking=True)
    return path
