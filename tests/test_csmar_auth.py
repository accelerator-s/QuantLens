import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from paths import workspace
from providers.exceptions import ProviderAuthError, ProviderNetworkError, ProviderVerificationBlockedError
from providers.markets.cn_a.csmar.client import CSMARClient


def test_institution_ip_failure_explains_campus_route(tmp_path, monkeypatch):
    monkeypatch.setenv('QUANTLENS_HOME', str(tmp_path))
    client = CSMARClient()
    def respond(request):
        return httpx.Response(200, json={'code': -1, 'msg': '当前IP没有可用机构账号'})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client._client:
        with pytest.raises(ProviderAuthError) as error:
            client.login()
    assert error.value.code == 'PROVIDER_CAMPUS_NETWORK_REQUIRED'
    assert 'data.csmar.com' in error.value.suggested_action


@pytest.mark.parametrize('state,valid', [('0', False), (0, False), (None, False),
                                         ('', False), ('1', True), (1, True)])
def test_cached_session_requires_logged_in_state(tmp_path, monkeypatch, state, valid):
    monkeypatch.setenv('QUANTLENS_HOME', str(tmp_path))
    cached = workspace().experience / '.csmar-session.json'
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps({'token': 'expired-token', 'cookies': {'U': 'stale-user', 'D': 'stale-domain'}}))
    cached.chmod(0o600)
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path.endswith('/isLoggedIn'):
            return httpx.Response(200, json={'code': 0, 'data': state},
                                  headers={'set-cookie': 'U=stale-jar; Path=/'})
        if request.url.path.endswith('/automaticLogin'):
            assert 'token' not in request.headers
            assert 'stale' not in request.headers.get('cookie', '')
            return httpx.Response(200, json={'code': 0, 'data': {'token': 'fresh-token', 'id': 1}})
        return httpx.Response(200)

    client = CSMARClient()
    with httpx.Client(transport=httpx.MockTransport(respond)) as client._client:
        if valid:
            assert client.login() == 'expired-token'
        else:
            assert client.login() == 'fresh-token'
            assert client._headers['Token'] == 'fresh-token'
    assert len(requests) == (1 if valid else 3)
    assert requests[0].url.path.endswith('/isLoggedIn')
    assert json.loads(cached.read_text())['token'] == 'expired-token'


@pytest.mark.parametrize('status,exception,code', [
    (401, ProviderAuthError, 'PROVIDER_SESSION_EXPIRED'),
    (503, ProviderNetworkError, 'PROVIDER_HTTP_TRANSPORT_ERROR'),
])
def test_download_distinguishes_expired_session_from_transport_failure(status, exception, code):
    client = CSMARClient()
    client._token = 'test-session'
    with httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(status, json={'code': -3004}))) as client._client:
        with pytest.raises(exception) as error:
            client.download([], '2026-11-05', '2026-11-13')
    assert error.value.code == code


@pytest.mark.parametrize('cached_status', [200, 401])
def test_expired_session_stops_when_automatic_login_requires_verification(tmp_path, monkeypatch, cached_status):
    monkeypatch.setenv('QUANTLENS_HOME', str(tmp_path))
    client = CSMARClient()
    client._token = 'old-token'
    client.save_verified_session()
    calls = []

    def respond(request):
        calls.append(request.url.path)
        if request.url.path.endswith('/isLoggedIn'):
            return httpx.Response(cached_status, json={'code': 0, 'data': '0'})
        if request.url.path.endswith('/automaticLogin'):
            return httpx.Response(200, json={'code': -3510})
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client._client:
        with pytest.raises(ProviderVerificationBlockedError) as error:
            client.login()
    assert error.value.code == 'PROVIDER_PHONE_VERIFICATION_REQUIRED'
    assert sum(path.endswith('/automaticLogin') for path in calls) == 1
    assert json.loads((workspace().experience / '.csmar-session.json').read_text())['token'] == 'old-token'
