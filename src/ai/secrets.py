from __future__ import annotations

import os
import re
import stat
from pathlib import Path

_SECRETS_DIR = Path.home() / ".config" / "quantlens"
_SECRETS_FILE = _SECRETS_DIR / "secrets.env"
_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$")

def secrets_path() -> Path:
    return _SECRETS_FILE

def _ensure_dir() -> None:
    _SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(_SECRETS_DIR, 0o700)

def _read_pairs() -> dict[str, str]:
    if not _SECRETS_FILE.exists():
        return {}
    out: dict[str, str] = {}
    for raw in _SECRETS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out

def _write_pairs(pairs: dict[str, str]) -> None:
    _ensure_dir()
    body = ["# QuantLens local secrets — never commit this file."]
    for k, v in sorted(pairs.items()):
        body.append(f"{k}={v}")
    _SECRETS_FILE.write_text("\n".join(body) + "\n", encoding="utf-8")
    os.chmod(_SECRETS_FILE, stat.S_IRUSR | stat.S_IWUSR)

def set_secret(env_name: str, value: str) -> Path:
    
    pairs = _read_pairs()
    pairs[env_name] = value
    _write_pairs(pairs)
    os.environ[env_name] = value
    return _SECRETS_FILE

def remove_secret(env_name: str) -> bool:
    pairs = _read_pairs()
    if env_name not in pairs:
        return False
    pairs.pop(env_name)
    _write_pairs(pairs)
    os.environ.pop(env_name, None)
    return True

def list_secret_names() -> list[str]:
    return sorted(_read_pairs().keys())

def load_into_env() -> None:
    
    for k, v in _read_pairs().items():
        os.environ.setdefault(k, v)
