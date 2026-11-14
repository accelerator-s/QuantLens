"""Exercise the running backend and cluster boundaries without changing business data."""
from pathlib import Path
import socket
import stat
import sys

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from storage.cluster_access import socket_path
from storage.hbase import HBase


def verify():
    for port in [9000, 9083, 10000, 16050]:
        with socket.socket() as connection:
            connection.settimeout(1)
            assert connection.connect_ex(('127.0.0.1', port)) != 0, port
    path = socket_path()
    assert stat.S_ISSOCK(path.stat().st_mode)
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    with httpx.Client(trust_env=False, timeout=10) as client:
        for port in [9870, 8088, 16010, 16030, 18080]:
            url = f'http://127.0.0.1:{port}'
            for headers in [{'Origin': 'http://evil.example'}, {'Sec-Fetch-Site': 'cross-site'},
                            {'Host': f'evil.example:{port}'},
                            {'Sec-Fetch-Site': 'same-site', 'Sec-Fetch-Mode': 'cors'}]:
                assert client.get(url, headers=headers).status_code == 403, (port, headers)
            assert client.post(url, json={}).status_code == 403, port
            assert client.get(url + '/webhdfs/v1/?op=LISTSTATUS').status_code == 403, port
            assert client.get(url).status_code in {200, 301, 302}, port
        url = 'http://127.0.0.1:8610'
        for path in ['/api/backtests', '/api/backtests/x', '/api/portfolio/x/candidates',
                     '/api/portfolio/x/attribution', '/api/lifecycle/factor-versions/x/promote']:
            for method in ['GET', 'POST', 'PUT', 'DELETE']:
                assert client.request(method, url + path).status_code == 404, (method, path)
        assert client.get(url + '/api/backtest/runs').status_code == 200
        for path in ['/api/strategies/chat', '/api/strategies/x/delete', '/api/strategy-evolution',
                     '/api/operations/strategies/x/bootstrap-champion',
                     '/api/operations/champion-decisions/x/approve']:
            assert client.post(url + path, json={}).status_code == 404, path
        assert client.get(url + '/api/status', headers={'Origin': 'http://evil.example'}).status_code == 403
        assert client.get(url + '/api/status', headers={'Host': 'evil.example'}).status_code == 400
    hbase = HBase()
    try:
        assert hbase._request('GET', '/version/cluster').status_code == 200
    finally:
        hbase.close()
    print('Backend, read-only cluster UI, private HBase transport and retired routes: passed')


if __name__ == '__main__':
    verify()
