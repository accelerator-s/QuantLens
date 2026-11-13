from __future__ import annotations

import base64
import hashlib
from urllib.parse import quote

import httpx

from exceptions import ContractViolation


def factor_rowkey(code: str, date: str) -> str:
    salt = int(hashlib.sha256(code.encode()).hexdigest()[:8], 16) % 16
    return f'{salt:02x}|{code}|{date}'


def _encode(value) -> str:
    return base64.b64encode(str(value).encode()).decode()


class HBase:
    def __init__(self, url='http://127.0.0.1:16050', *, client=None, timeout=10):
        self.client = client or httpx.Client(base_url=url, timeout=timeout, trust_env=False,
                                            headers={'Accept': 'application/json'})

    def _request(self, method, path, **kwargs):
        try:
            response = self.client.request(method, path, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            raise ContractViolation(code='HBASE_UNAVAILABLE', message='HBase 点查失败',
                                    suggested_action='检查 hbase REST 服务及已发布的快照') from exc

    def create(self, table, families, ttl=1095*86400):
        columns = [{'name': name, **({'TTL': ttl} if ttl else {})} for name in families]
        self._request('PUT', f'/{quote(table, safe="")}/schema', json={'name': table, 'ColumnSchema': columns})

    def put(self, table, rowkey, cells: dict):
        self._request('PUT', f'/{quote(table, safe="")}/{quote(rowkey, safe="")}', json={
            'Row': [{'key': _encode(rowkey), 'Cell': [{'column': _encode(k), '$': _encode(v)}
                                                     for k, v in sorted(cells.items()) if v is not None]}]})

    def get(self, table, rowkey):
        try:response = self.client.get(f'/{quote(table, safe="")}/{quote(rowkey, safe="")}')
        except httpx.HTTPError as exc:
            raise ContractViolation(code='HBASE_UNAVAILABLE',message='HBase 点查失败',suggested_action='检查 HBase REST 服务') from exc
        if response.status_code == 404:
            return None
        if response.is_error:
            raise ContractViolation(code='HBASE_UNAVAILABLE',message='HBase 点查失败',suggested_action='检查 HBase REST 服务')
        rows = self._decode(response.json())
        return rows[0] if rows else None

    @staticmethod
    def _decode(payload):
        return [{'rowkey': base64.b64decode(row['key']).decode(),
                 **{base64.b64decode(c['column']).decode(): base64.b64decode(c['$']).decode()
                    for c in row.get('Cell', [])}} for row in payload.get('Row', [])]

    def scan(self, table, prefix='', limit=100):
        if not 1 <= limit <= 1000:
            raise ValueError('limit must be between 1 and 1000')
        # REST wildcard scan. Every user key component is URL encoded.
        response = self._request('GET', f'/{quote(table, safe="")}/{quote(prefix, safe="")}*', params={'limit': limit})
        return self._decode(response.json())

    def delete(self, table, rowkey):
        self._request('DELETE', f'/{quote(table, safe="")}/{quote(rowkey, safe="")}')

    def close(self):
        self.client.close()
