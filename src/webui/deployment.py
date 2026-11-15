"""Explicit HTTPS origin for a reverse proxy on the same host."""
from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

from exceptions import ContractViolation


def public_origin() -> str | None:
    value = os.environ.get('QUANTLENS_PUBLIC_ORIGIN', '').strip()
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ''
        port = parsed.port
        valid = (parsed.scheme == 'https' and parsed.username is None and parsed.password is None
                 and parsed.path in {'', '/'} and not parsed.query and not parsed.fragment
                 and not any(char.isspace() for char in value) and '\\' not in value
                 and '?' not in value and '#' not in value and not parsed.netloc.endswith(':')
                 and re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+'
                                  r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', hostname)
                 and (port is None or 1 <= port <= 65535))
    except ValueError:
        valid = False
    if not valid:
        raise ContractViolation(
            code='QL_PUBLIC_ORIGIN_INVALID', message='公网访问地址必须是完整的 HTTPS 域名地址',
            suggested_action='将 QUANTLENS_PUBLIC_ORIGIN 设为 https://你的域名，不要包含路径、参数或账号密码')
    authority = hostname + (f':{port}' if port not in {None, 443} else '')
    return f'https://{authority}'


def trusted_hosts() -> list[str]:
    hosts = ['127.0.0.1', 'localhost', '::1', 'testserver']
    origin = public_origin()
    if origin:
        hosts.append(urlsplit(origin).hostname)
    return hosts
