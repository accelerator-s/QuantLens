from __future__ import annotations

import secrets
from datetime import UTC, datetime

_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"  # 去掉易混字符

def _short_token(n: int = 4) -> str:
    return "".join(_ALPHABET[secrets.randbelow(len(_ALPHABET))] for _ in range(n))

def generate_issue_id(now: datetime | None = None) -> str:
    
    moment = now or datetime.now(tz=UTC).astimezone()
    return f"ISS-{moment:%Y%m%d}-{_short_token()}"
