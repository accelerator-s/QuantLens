from __future__ import annotations

import os
from typing import Any
from pathlib import Path
from urllib.parse import urlsplit

_PROXY_ENV_PRIORITY = (
    "https_proxy",
    "HTTPS_PROXY",
    "http_proxy",
    "HTTP_PROXY",
    "all_proxy",
    "ALL_PROXY",
)

_LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")

_SOCKS_SCHEMES = ("socks", "socks4", "socks5", "socks5h")

def _socks_supported() -> bool:
    try:
        import socksio  
    except ImportError:
        return False
    return True

def resolve_proxy_url() -> str | None:
    
    for var in _PROXY_ENV_PRIORITY:
        raw = (os.environ.get(var) or "").strip()
        if not raw or "://" not in raw:
            continue
        scheme = raw.split("://", 1)[0].lower()
        if scheme in ("http", "https"):
            return raw
        if scheme in _SOCKS_SCHEMES:
            if not _socks_supported():
                continue  
            if scheme == "socks":
                return "socks5://" + raw.split("://", 1)[1]
            return raw
        # 未知 scheme：跳过
    return None

def _system_hosts_path() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/drivers/etc/hosts"
    return Path("/etc/hosts")


def _mapped_system_host(url: str) -> str | None:
    host = urlsplit(url).hostname
    if not host:
        return None
    try:
        lines = _system_hosts_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        fields = line.split("#", 1)[0].split()
        if len(fields) > 1 and host.rstrip(".").lower() in {name.rstrip(".").lower() for name in fields[1:]}:
            return host
    return None


def create_http_client(**kwargs: Any):
    
    import httpx  

    target_url = kwargs.pop("target_url", "")
    kwargs.setdefault("trust_env", False)
    if "mounts" not in kwargs:
        proxy_url = resolve_proxy_url()
        if proxy_url is not None:
            mounts: dict[str, httpx.HTTPTransport] = {
                f"all://{host}": httpx.HTTPTransport() for host in _LOCAL_HOSTS
            }
            mapped_host = _mapped_system_host(target_url)
            if mapped_host:
                mounts[f"all://{mapped_host}"] = httpx.HTTPTransport()
            mounts["all://"] = httpx.HTTPTransport(proxy=proxy_url)
            kwargs["mounts"] = mounts
    return httpx.Client(**kwargs)

__all__ = ["create_http_client", "resolve_proxy_url"]
