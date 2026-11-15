import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from paths import workspace
from providers.exceptions import ProviderAuthError, ProviderNetworkError
from providers.markets.cn_a.csmar.client import CSMARClient


@pytest.mark.parametrize('state,valid', [('0', False), (0, False), (None, False),
                                         ('', False), ('1', True), (1, True)])
def test_cached_session_requires_logged_in_state(tmp_path, monkeypatch, state, valid):
    monkeypatch.setenv('QUANTLENS_HOME', str(tmp_path))
    cached = workspace().experience / '.csmar-session.json'
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps({'token': 'expired-token', 'cookies': {}}))
    cached.chmod(0o600)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'code': 0, 'data': state})

    client = CSMARClient()
    with httpx.Client(transport=httpx.MockTransport(respond)) as client._client:
        if valid:
            assert client.login() == 'expired-token'
        else:
            with pytest.raises(ProviderAuthError) as error:
                client.login()
            assert error.value.code == 'PROVIDER_SESSION_EXPIRED'
            assert client._token == ''
            assert 'Token' not in client._headers
    assert len(requests) == 1
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
