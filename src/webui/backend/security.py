"""Local-only HTTP boundary and bounded request buffering."""
from __future__ import annotations

import asyncio
import ipaddress
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

MAX_BODY_BYTES = 8 * 1024 * 1024


class SecurityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'websocket':
            return await send({'type': 'websocket.close', 'code': 1008})
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        async def secure_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
                ]
                if scope.get("path", "").startswith("/api/"):
                    message["headers"].append((b"cache-control", b"no-store"))
            await send(message)

        async def reject(status, detail):
            return await JSONResponse({'detail': detail}, status_code=status)(scope, receive, secure_send)

        names = [key.lower() for key, _ in scope['headers']]
        if names.count(b'host') != 1 or any(names.count(name) > 1 for name in
                [b'origin', b'sec-fetch-site', b'content-length', b'transfer-encoding']):
            return await reject(400, 'Ambiguous request headers')
        if b'content-length' in names and b'transfer-encoding' in names:
            return await reject(400, 'Ambiguous request framing')
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope["headers"]}
        peer = (scope.get("client") or ("",))[0]
        try:
            local = ipaddress.ip_address(peer).is_loopback
        except ValueError:
            local = peer == "testclient"  # Starlette's in-process test transport.
        try:
            host = urlsplit('http://' + headers['host'])
            valid_host = (host.hostname in {'127.0.0.1', 'localhost', '::1'}
                          or (peer == 'testclient' and host.hostname == 'testserver'))
            valid_host = (valid_host and not host.username and not host.password
                          and not host.path and not host.query and not host.fragment
                          and (host.port is None or 1 <= host.port <= 65535))
        except ValueError:
            valid_host = False
        if not valid_host:
            return await reject(400, 'Invalid host header')
        origin = headers.get("origin")
        valid_origin = True
        if origin:
            try:
                parsed = urlsplit(origin)
                valid_origin = (parsed.scheme == scope.get("scheme", "http")
                                and parsed.netloc == headers.get("host")
                                and not parsed.username and not parsed.password
                                and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment)
            except ValueError:
                valid_origin = False
        if not local or not valid_origin or headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
            return await reject(403, {"code": "QL_ORIGIN_DENIED", "message": "请求来源不匹配"})
        try:
            length = int(headers.get("content-length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            return await reject(413, 'Request body too large')
        chunks = []
        size = 0
        try:
            async with asyncio.timeout(15):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    size += len(chunk)
                    if size > MAX_BODY_BYTES:
                        return await reject(413, 'Request body too large')
                    chunks.append(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            return await reject(408, 'Request body timeout')
        pending = True

        async def bounded_receive():
            nonlocal pending
            if pending:
                pending = False
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        return await self.app(scope, bounded_receive, secure_send)
