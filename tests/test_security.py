"""Executable security regressions; no external services or real attacks."""
import asyncio
import io
import os
from pathlib import Path
import socket
import sys
from unittest.mock import Mock

import httpx
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from exceptions import ContractViolation, FactorExecutionError, ModelRouteError
from factors.sandbox import execute_factor
from factors.plugins import FactorPlugin
from paths import Workspace
from ai.tools import public_http
from webui.backend.security import MAX_BODY_BYTES, SecurityMiddleware


@pytest.fixture
def panel():
    return {"close": pd.DataFrame([[1., 2.], [3., 4.]], index=pd.date_range("2024-01-01", periods=2))}


def test_generated_factor_runs_without_manual_approval(panel):
    result = execute_factor('import numpy as np\ndef compute(p):\n print("factor log")\n return np.sqrt(p["close"])', panel)
    pd.testing.assert_frame_equal(result, panel["close"] ** .5)


@pytest.mark.parametrize("attack", [
    'import socket; socket.socket()',
    'import subprocess; subprocess.run(["/bin/true"])',
    'import os; os.fork()',
    'import os; os.posix_spawn("/bin/true", ["true"], {})',
    'import os; os.kill(os.getppid(), 0)',
    'import ctypes; assert ctypes.CDLL(None).socket(2, 1, 0) >= 0',
    'import ctypes; assert ctypes.CDLL(None).syscall(435, 0, 0) != -1',
    'import ctypes; assert ctypes.CDLL(None).prctl(38, 0, 0, 0, 0) == 0',
    'import resource; resource.setrlimit(resource.RLIMIT_CPU, (1000, 1000))',
    'open("/proc/self/environ").read()',
    'open("/etc/passwd").read()',
    'import os; os.chmod("/etc/passwd", 0o777)',
])
def test_system_access_is_blocked(panel, attack):
    with pytest.raises(FactorExecutionError):
        execute_factor(attack + '\ndef compute(p): return p["close"]', panel)


def test_host_file_read_write_and_thread_escape_are_denied(panel, tmp_path):
    target = tmp_path / "host-secret"
    target.write_text("sentinel")
    for attack in [f'open({str(target)!r}).read()', f'open({str(target)!r}, "w").write("changed")']:
        with pytest.raises(FactorExecutionError):
            execute_factor(attack + '\ndef compute(p): return p["close"]', panel)
    source = f'''import threading
def compute(p):
    denied = []
    def child():
        try: open({str(target)!r}).read()
        except PermissionError: denied.append(True)
    worker = threading.Thread(target=child)
    worker.start()
    worker.join()
    assert denied == [True]
    return p["close"]
'''
    pd.testing.assert_frame_equal(execute_factor(source, panel), panel["close"])
    assert target.read_text() == "sentinel"


def test_secret_environment_is_not_inherited(panel, monkeypatch):
    monkeypatch.setenv("QUANT_API_KEY_SECURITY_TEST", "private-sentinel")
    result = execute_factor('import os\nassert "QUANT_API_KEY_SECURITY_TEST" not in os.environ\ndef compute(p): return p["close"]', panel)
    pd.testing.assert_frame_equal(result, panel["close"])


def test_infinite_loop_is_terminated(panel):
    with pytest.raises(FactorExecutionError) as error:
        execute_factor("while True: pass", panel, timeout=1)
    assert error.value.code == "FACTOR_TIMEOUT"


def test_missing_sandbox_fails_closed(panel, monkeypatch, tmp_path):
    import factors.sandbox as sandbox
    worker = tmp_path / "worker.py"
    worker.write_text("import sys; sys.exit(78)")
    monkeypatch.setattr(sandbox, "_WORKER", worker)
    with pytest.raises(FactorExecutionError) as error:
        execute_factor('def compute(p): return p["close"]', panel)
    assert error.value.code == "FACTOR_SANDBOX_UNAVAILABLE"


@pytest.mark.parametrize("name", ["../outside", "/tmp/outside", "a/b", "a\\b", "_meta", ".."]) 
def test_plugin_paths_cannot_escape_workspace(tmp_path, name):
    plugin = FactorPlugin(Workspace(tmp_path))
    with pytest.raises(ContractViolation):
        plugin.write(name, "bad")


def test_plugin_symlinks_cannot_escape_workspace(tmp_path):
    root = tmp_path / "workspace"
    (root / "factors").mkdir(parents=True)
    target = tmp_path / "external.py"
    target.write_text("sentinel")
    (root / "factors/test.py").symlink_to(target)
    with pytest.raises(ContractViolation):
        FactorPlugin(Workspace(root)).write("test", "bad", override=True)
    assert target.read_text() == "sentinel"


def answer(address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return (family, socket.SOCK_STREAM, 6, "", (address, 80))


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "100.64.0.1", "0.0.0.0", "224.0.0.1", "::1", "::ffff:127.0.0.1", "fe80::1"])
def test_dns_private_and_special_destinations_denied(monkeypatch, address):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [answer(address)])
    assert not public_http.is_public_url("http://attacker.example/path")


def test_mixed_dns_answers_fail_closed(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [answer("93.184.216.34"), answer("127.0.0.1")])
    assert not public_http.is_public_url("http://attacker.example")


def test_fake_ip_proxy_uses_independent_public_dns_and_never_synthetic_ip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [answer("198.18.0.1"), answer("fdfe::1")])
    monkeypatch.setattr(public_http, "_public_dns", lambda host: ["93.184.216.34"])
    assert public_http.public_addresses("https://public.example")[2] == ["93.184.216.34"]
    assert not public_http.is_public_url("http://198.18.0.1")
    monkeypatch.setattr(public_http, "_public_dns", lambda host: ["127.0.0.1"])
    assert not public_http.is_public_url("https://attacker.example")


def test_redirect_to_internal_destination_is_denied(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: [answer("127.0.0.1" if host == "internal.example" else "93.184.216.34")])
    connection = Mock()
    response = Mock(status=302)
    response.getheader.return_value = "http://internal.example/metadata"
    connection.getresponse.return_value = response
    monkeypatch.setattr(public_http.http.client, "HTTPConnection", lambda *a, **k: connection)
    connector = Mock(return_value=Mock())
    monkeypatch.setattr(socket, "create_connection", connector)
    with pytest.raises(ModelRouteError):
        public_http.fetch_public("http://public.example")
    assert connector.call_count == 1
    assert connector.call_args.args[0][0] == "93.184.216.34"


def test_public_http_pins_dns_and_bounds_response(monkeypatch):
    resolver = Mock(return_value=[answer("93.184.216.34")])
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    response = Mock(status=200)
    response.getheader.return_value = "identity"
    response.headers.get_content_charset.return_value = "utf-8"
    response.read1.side_effect = [b"public page", b""]
    connection = Mock()
    connection.getresponse.return_value = response
    monkeypatch.setattr(public_http.http.client, "HTTPConnection", lambda *a, **k: connection)
    connector = Mock(return_value=Mock())
    monkeypatch.setattr(socket, "create_connection", connector)
    assert public_http.fetch_public("http://public.example")[1] == "public page"
    assert resolver.call_count == 1
    assert connector.call_args.args[0] == ("93.184.216.34", 80)
    response.read1.side_effect = [b"x" * (public_http.MAX_BYTES + 1)]
    with pytest.raises(ModelRouteError):
        public_http.fetch_public("http://public.example")


async def dummy_app(scope, receive, send):
    await receive()
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def http_request(*, headers=None, client="127.0.0.1", body=None):
    async def run():
        transport = httpx.ASGITransport(app=SecurityMiddleware(dummy_app), client=(client, 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8610") as session:
            return await session.request("POST" if body is not None else "GET", "/api/test", headers=headers, content=body)
    return asyncio.run(run())


@pytest.mark.parametrize("headers", [{"origin": "http://evil.example"}, {"origin": "null"}, {"origin": "https://localhost:8610"}, {"sec-fetch-site": "cross-site"}])
def test_cross_site_reads_and_writes_are_denied(headers):
    assert http_request(headers=headers).status_code == 403
    assert http_request(headers=headers, body=b"{}").status_code == 403


def test_non_loopback_peer_and_forged_forwarded_header_are_denied():
    assert http_request(client="192.0.2.1", headers={"x-forwarded-for": "127.0.0.1"}).status_code == 403


@pytest.mark.parametrize('host', ['evil.example:8610', 'localhost.evil.example',
                                '127.0.0.1@evil.example', 'localhost/path', 'localhost:bad', 'testserver'])
def test_dns_rebinding_and_malformed_hosts_are_denied(host):
    assert http_request(headers={'host': host}).status_code == 400


@pytest.mark.parametrize('headers', [
    [('origin', 'http://evil.example'), ('origin', 'http://localhost:8610')],
    [('content-length', '0'), ('content-length', '0')],
    [('content-length', '0'), ('transfer-encoding', 'chunked')],
    [('sec-fetch-site', 'cross-site'), ('sec-fetch-site', 'same-origin')],
])
def test_ambiguous_security_headers_are_denied(headers):
    response = http_request(headers=headers)
    assert response.status_code == 400
    assert response.headers['x-content-type-options'] == 'nosniff'


def test_private_cluster_socket_directory_rejects_symlinks(tmp_path, monkeypatch):
    from storage.cluster_access import prepare_access, socket_path
    monkeypatch.setenv('QUANTLENS_HOME', str(tmp_path))
    prepare_access()
    directory = socket_path().parent
    assert directory.stat().st_mode & 0o777 == 0o700
    directory.rmdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    directory.symlink_to(outside, target_is_directory=True)
    with pytest.raises(PermissionError):
        prepare_access()


def test_host_hbase_uses_private_socket_and_cluster_hbase_uses_internal_dns(monkeypatch):
    from storage import hbase
    transport = Mock()
    client = Mock()
    monkeypatch.setattr(hbase.httpx, 'HTTPTransport', transport)
    monkeypatch.setattr(hbase.httpx, 'Client', client)
    hbase.HBase()
    assert transport.call_args.kwargs['uds'].endswith('/cluster-access/hbase.sock')
    assert client.call_args.kwargs['transport'] is transport.return_value
    transport.reset_mock()
    hbase.HBase('http://hbase:16050')
    transport.assert_not_called()
    assert client.call_args.kwargs['transport'] is None


def test_local_requests_and_security_headers():
    response = http_request(headers={"origin": "http://localhost:8610"}, body=b"{}")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"
    assert "script-src 'self'" in response.headers["content-security-policy"]


def test_large_and_streamed_request_bodies_are_rejected():
    assert http_request(headers={"content-length": str(MAX_BODY_BYTES + 1)}).status_code == 413
    async def chunks():
        for _ in range(9):
            yield b"a" * (1024 * 1024)
    assert http_request(body=chunks()).status_code == 413


def test_secret_newline_injection_and_permissions(tmp_path, monkeypatch):
    from ai import secrets
    monkeypatch.setattr(secrets, "_SECRETS_DIR", tmp_path / "private")
    monkeypatch.setattr(secrets, "_SECRETS_FILE", tmp_path / "private/secrets.env")
    with pytest.raises(ValueError):
        secrets.set_secret("QUANT_API_KEY_TEST", "key\nPYTHONPATH=/tmp/bad")
    secrets.set_secret("QUANT_API_KEY_TEST", "key")
    assert secrets.secrets_path().stat().st_mode & 0o777 == 0o600
    assert secrets._SECRETS_DIR.stat().st_mode & 0o777 == 0o700
    monkeypatch.delenv("QUANT_API_KEY_TEST")
