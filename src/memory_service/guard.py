from __future__ import annotations

import re
from dataclasses import dataclass

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("api_key_kv", re.compile(r"(?i)\bapi[_-]?key\s*[:=]\s*[\"']?[A-Za-z0-9._\-]{8,}")),
    (
        "token_kv",
        re.compile(r"(?i)\b(?:access|bearer|auth)[_-]?token\s*[:=]\s*[\"']?[A-Za-z0-9._\-]{8,}"),
    ),
    ("password_kv", re.compile(r"(?i)\b(password|passwd|pwd)\s*[:=]\s*[\"']?[^\s\"']{4,}")),
    ("authorization_header", re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+[A-Za-z0-9._\-]{8,}")),
    ("cookie_header", re.compile(r"(?i)\bcookie\s*:\s*[^\n]{8,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA|EC|OPENSSH|PRIVATE) KEY-----")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
)

@dataclass
class SecretMatch:
    name: str
    excerpt: str

def scan_for_secrets(text: str) -> list[SecretMatch]:
    
    out: list[SecretMatch] = []
    for name, pat in SECRET_PATTERNS:
        for m in pat.finditer(text):
            out.append(SecretMatch(name=name, excerpt=m.group(0)[:80]))
    return out

__all__ = ["SECRET_PATTERNS", "SecretMatch", "scan_for_secrets"]
