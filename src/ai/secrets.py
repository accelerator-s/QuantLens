from __future__ import annotations

import os
import re
import tempfile
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
        if not m or not re.fullmatch(r"QUANT_API_KEY_[A-Z0-9_]+", m.group(1)):
            continue
        out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out

def _write_pairs(pairs: dict[str, str]) -> None:
    _ensure_dir()
    body = ["# QuantLens local secrets — never commit this file."]
    for k, v in sorted(pairs.items()):
        body.append(f"{k}={v}")
    fd, temporary = tempfile.mkstemp(prefix=".secrets-", dir=_SECRETS_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("\n".join(body) + "\n")
        os.replace(temporary, _SECRETS_FILE)
    finally:
        Path(temporary).unlink(missing_ok=True)

def set_secret(env_name: str, value: str) -> Path:
    if not re.fullmatch(r"QUANT_API_KEY_[A-Z0-9_]+", env_name):
        raise ValueError("Invalid QuantLens secret name")
    if any(char in value for char in ("\r", "\n", "\x00")):
        raise ValueError("Secret values must be a single line")
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
