"""Bounded public HTTP reads with DNS pinning and per-hop URL validation.

Intentionally bypass environment proxies: a proxy would resolve the original
hostname again and invalidate the checked destination address.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
import time
from urllib.parse import urlencode, urljoin, urlsplit

from exceptions import ModelRouteError

MAX_BYTES = 2 * 1024 * 1024


def _public_dns(host: str) -> list[str]:
    """Resolve fake-IP proxy answers via a fixed, TLS-verified public resolver.

    The proxy's synthetic 198.18/15 address is never used as a destination.
    Resolver responses and the final pinned connection undergo the same public
    address checks, so this does not permit private network access.
    """
    connection = http.client.HTTPConnection("cloudflare-dns.com", timeout=5)
    try:
        connection.sock = ssl.create_default_context().wrap_socket(
            socket.create_connection(("1.1.1.1", 443), timeout=5), server_hostname="cloudflare-dns.com")
        connection.request("GET", "/dns-query?" + urlencode({"name": host, "type": "A"}),
                           headers={"Accept": "application/dns-json"})
        response = connection.getresponse()
        body = response.read(65537)
        if response.status != 200 or len(body) > 65536:
            _deny("无法安全解析目标域名。")
        result = json.loads(body)
        if result.get("Status") != 0:
            _deny("无法安全解析目标域名。")
        return list(dict.fromkeys(item["data"] for item in result.get("Answer", []) if item.get("type") == 1))
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise ModelRouteError(code="AI_TOOL_URL_FORBIDDEN", message="无法安全解析目标域名。", blocking=True) from exc
    finally:
        connection.close()


def _deny(message="仅允许访问 HTTP/HTTPS 公网地址。"):
    raise ModelRouteError(code="AI_TOOL_URL_FORBIDDEN", message=message, blocking=True)


def public_addresses(url: str) -> tuple[str, int, list[str]]:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if (parsed.scheme not in {"http", "https"} or not host
                or parsed.username is not None or parsed.password is not None
                or any(ord(char) < 33 for char in url) or "\\" in url):
            _deny()
        normalized = host.rstrip(".").lower()
        if normalized == "localhost" or normalized.endswith((".localhost", ".local", ".internal")):
            _deny()
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
        # Some local TUN proxies replace all DNS replies with synthetic IPs.
        # Numeric URLs never receive this fallback, including benchmark IPs.
        try:
            ipaddress.ip_address(host)
            numeric_host = True
        except ValueError:
            numeric_host = False
        benchmark = ipaddress.ip_network("198.18.0.0/15")
        synthetic = any(ipaddress.ip_address(address) in benchmark for address in addresses)
        if not numeric_host and synthetic:
            if any(ipaddress.ip_address(address).version == 4 and ipaddress.ip_address(address) not in benchmark
                   for address in addresses):
                _deny()
            addresses = _public_dns(host)
        if not addresses:
            _deny()
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if (not ip.is_global or ip.is_multicast
                    or (getattr(ip, "ipv4_mapped", None) is not None and not ip.ipv4_mapped.is_global)):
                _deny()
        return host, port, addresses
    except (OSError, ValueError) as exc:
        raise ModelRouteError(code="AI_TOOL_URL_FORBIDDEN",
                              message="目标地址无效或无法确认其为公网地址。", blocking=True) from exc


def is_public_url(url: str) -> bool:
    try:
        public_addresses(url)
        return True
    except ModelRouteError:
        return False


def fetch_public(url: str, *, headers: dict[str, str] | None = None,
                 timeout: float = 15.0) -> tuple[int, str, str]:
    deadline = time.monotonic() + timeout
    for hop in range(6):
        host, port, addresses = public_addresses(url)
        parsed = urlsplit(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Public HTTP deadline exceeded")
        # Connect only to an already validated numeric address. HTTPS still
        # verifies the original hostname via SNI and the default trust store.
        connection = http.client.HTTPConnection(host, port, timeout=remaining)
        response = None
        try:
            sock = socket.create_connection((addresses[0], port), timeout=remaining)
            connection.sock = sock
            if parsed.scheme == "https":
                connection.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            target = parsed.path or "/"
            if parsed.query:
                target += "?" + parsed.query
            connection.request("GET", target, headers={**(headers or {}), "Accept-Encoding": "identity"})
            active_socket = connection.sock
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location or hop == 5:
                    _deny("网页重定向无效或次数过多。")
                url = urljoin(url, location)
                continue
            encoding = response.getheader("Content-Encoding", "identity").lower()
            if encoding != "identity":
                _deny("网页未遵循无压缩读取要求。")
            chunks = []
            size = 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Public HTTP deadline exceeded")
                active_socket.settimeout(remaining)
                chunk = response.read1(min(65536, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BYTES:
                    _deny("网页内容超过 2 MiB 读取上限。")
            charset = response.headers.get_content_charset() or "utf-8"
            try:
                body = b"".join(chunks).decode(charset, errors="replace")
            except LookupError:
                body = b"".join(chunks).decode("utf-8", errors="replace")
            return response.status, body, url
        finally:
            if response is not None:
                response.close()
            connection.close()
    _deny("网页重定向次数过多。")
