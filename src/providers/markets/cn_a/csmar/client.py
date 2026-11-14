from __future__ import annotations

import io
import base64
import hashlib
import os
import json
import math
from collections.abc import Callable
import re
import time
import zipfile
from typing import Any

import httpx
import pandas as pd

from providers.exceptions import (
    ProviderAuthError,
    ProviderDataError,
    ProviderNetworkError,
    ProviderVerificationBlockedError,
)

# CSMAR API 常量

BASE_MAIN = "https://data.csmar.com/api/csmar-main"
BASE_SINGLE = "https://data.csmar.com/api/csmar-single"
FILE_HOST = "https://file.csmar.com"
SINGLE_SOCKET = "wss://data.csmar.com/ws"

SIGN_CODE = "f28c0a79-798c-4f9d-83fc-f62a813dacdb"

DEFAULT_HEADERS = {
    "Content-Type": "application/json;charse=UTF-8",  
    "accept": "application/json, text/plain, */*",
    "lang": "0",
    "belong": "0",
    "signcode": SIGN_CODE,
    "Origin": "https://data.csmar.com",
    "Referer": "https://data.csmar.com/csmar.html",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
}

DEFAULT_COOKIES = {
    "signCode": SIGN_CODE,
}

BROWSER_OBJ = {
    "resolvingPower": "2560X1600",
    "browser": "Chrome 148.0",
    "website": "data.csmar.com",
    "clientType": "0",
}

TRD_DALYR_FIELDS = [
    {"id": 58, "field": "Stkcd", "title": "证券代码", "type": "Nvarchar", "sign": "2"},
    {"id": 59, "field": "Trddt", "title": "交易日期", "type": "Datetime", "sign": "1"},
    {"id": 60, "field": "Opnprc", "title": "日开盘价", "type": "decimal", "sign": "0"},
    {"id": 61, "field": "Hiprc", "title": "日最高价", "type": "decimal", "sign": "0"},
    {"id": 62, "field": "Loprc", "title": "日最低价", "type": "decimal", "sign": "0"},
    {"id": 63, "field": "Clsprc", "title": "日收盘价", "type": "decimal", "sign": "0"},
    {"id": 64, "field": "Dnshrtrd", "title": "日个股交易股数", "type": "decimal", "sign": "0"},
    {"id": 65, "field": "Dnvaltrd", "title": "日个股交易金额", "type": "decimal", "sign": "0"},
]

FILE_OUT_TXT = "0"
FILE_OUT_XLS_MATLAB = "1"
FILE_OUT_XLSX = "2"

# frequency 取值
FREQ_ALL = "0"  # 不限
FREQ_DAILY = "0"
FREQ_WEEKLY = "1"
FREQ_MONTHLY = "2"

# 打包等待时限：默认 30 分钟
POLL_INTERVAL = 5.0  # 秒
POLL_MAX_ATTEMPTS = 360  # 最多等 30 分钟

PHONE_VERIFICATION_CODES = frozenset({-3510})

_EMPTY_RESULT_CODES = frozenset({-5501, "-5501"})
_EMPTY_RESULT_MESSAGE = "当前查询结果为0条"

_AF_BENCH_COLUMNS = (
    "Stkcd",
    "ReportID",
    "Accper",
    "Rptdt",
    "Fenddt",
    "Ananm",
    "DeclareDate",
    "AnanmID",
    "InstitutionID",
    "Brokern",
    "Investrank",
    "Stdrank",
    "Rankchg",
    "Rankbench",
    "ForecastAccuracy",
    "ForecastOptimism",
    "RivisionHorizon",
    "ForecastHorizon",
)
_AF_BENCH_PREFIX_FIELD_COUNT = 13
_AF_BENCH_METRIC_FIELD_COUNT = 4
_STOCK_CODE_PATTERN = re.compile(r"\d{1,6}")
_NUMBER_PATTERN = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?")
_DATE_PATTERN = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T].*)?")

_VERIFICATION_SUGGESTED_ACTION = (
    "CSMAR 要求手机验证，本次下载已停止且不会自动重试。"
    "请在当前 CSMAR 会话中完成机构登录验证，再继续更新。"
    "验证码仅用于当次校验；已验证会话保存在仅当前用户可读的本地凭据文件中。"
)

class CSMARClient:

    def __init__(
        self,
        timeout: float = 60.0,
        poll_interval: float = POLL_INTERVAL,
        poll_max_attempts: int = POLL_MAX_ATTEMPTS,
        on_progress: Callable[[dict], None] | None = None,
    ) -> None:
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._poll_max_attempts = poll_max_attempts
        self._on_progress = on_progress

        self._client: httpx.Client | None = None
        self._token: str = ""
        self._headers: dict[str, str] = dict(DEFAULT_HEADERS)
        self._cookies: dict[str, str] = dict(DEFAULT_COOKIES)

    def __enter__(self) -> CSMARClient:
        from net import create_http_client

        self._client = create_http_client(timeout=self._timeout, follow_redirects=True)
        return self

    def __exit__(self, *_: Any) -> None:
        if self._client:
            self._client.close()
            self._client = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            raise ProviderNetworkError(
                code="PROVIDER_NOT_CONNECTED",
                message="CSMARClient 未在 context manager 中使用。",
                blocking=True,
            )
        return self._client

    def _raise_if_verification_blocked(self, result: dict, stage: str) -> None:
        
        csmar_code = result.get("code")
        if csmar_code in PHONE_VERIFICATION_CODES:
            raise ProviderVerificationBlockedError(
                code="PROVIDER_PHONE_VERIFICATION_REQUIRED",
                message=(
                    f"CSMAR 在 {stage} 阶段返回风控码 {csmar_code}：需要手机验证，下载已阻断。"
                ),
                context={"stage": stage, "csmar_code": csmar_code},
                blocking=True,
                suggested_action=_VERIFICATION_SUGGESTED_ACTION,
            )

    def check_network(self) -> bool:
        
        try:
            resp = self.client.head(
                "https://data.csmar.com",
                headers={"User-Agent": DEFAULT_HEADERS["User-Agent"]},
                timeout=10,
            )
            return resp.status_code < 500
        except (httpx.ConnectError, httpx.TimeoutException):
            return False

    def login(self) -> str:
        from paths import workspace
        cached = workspace().experience / '.csmar-session.json'
        if cached.exists():
            if cached.is_symlink() or cached.stat().st_mode & 0o077:
                raise ProviderAuthError(code='PROVIDER_SESSION_PERMISSIONS', message='CSMAR 会话文件必须仅当前用户可读写。', blocking=True)
            try:
                saved = json.loads(cached.read_text())
                if not isinstance(saved['token'], str) or not saved['token'] or not isinstance(saved['cookies'], dict):
                    raise ValueError('invalid session')
            except (ValueError, KeyError, TypeError):
                raise ProviderAuthError(code='PROVIDER_SESSION_INVALID', message='CSMAR 本地会话无效，须重新验证。', blocking=True) from None
            self._token = saved['token']
            self._headers['Token'] = self._token
            self._cookies = saved['cookies']
            response = self.client.get(f'{BASE_MAIN}/isLoggedIn', headers=self._headers, cookies=self._cookies)
            state = response.json() if response.status_code == 200 else {}
            if state.get('code') == 0 and state.get('data'):
                return self._token
            raise ProviderAuthError(code='PROVIDER_SESSION_EXPIRED', message='CSMAR 已验证会话已过期，须重新完成手机号验证。', blocking=True)
        if not self.check_network():
            raise ProviderNetworkError(
                code="PROVIDER_NETWORK_UNAVAILABLE",
                message="CSMAR 不可达。请确认当前在校园网环境下（含 VPN）。",
                blocking=True,
                suggested_action="连接校园网或启动 VPN 后重试。",
            )

        t = str(int(time.time() * 1000))
        resp = self.client.post(
            f"{BASE_MAIN}/automaticLogin",
            params={"t": t},
            headers=self._headers,
            cookies=self._cookies,
            json=BROWSER_OBJ,
        )

        if resp.status_code != 200:
            raise ProviderAuthError(
                code="PROVIDER_AUTH_HTTP_ERROR",
                message=f"登录请求失败：HTTP {resp.status_code}",
                context={"status_code": resp.status_code},
                blocking=True,
            )

        data = resp.json()
        self._raise_if_verification_blocked(data, "automaticLogin")
        if data.get("code") != 0:
            raise ProviderAuthError(
                code="PROVIDER_AUTH_FAILED",
                message=f"CSMAR 登录失败：{data.get('msg', '未知错误')}",
                context={"response": data},
                blocking=True,
            )

        self._token = data["data"]["token"]
        self._headers["Token"] = self._token

        # The official web client binds its domain cookie to the token and user.
        # Persist credentials only after a verified download or phone verification.
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        identity = str(data['data']['id'])
        key = hashlib.sha256((self._token + '|csmar.domain-cookie.v1').encode()).digest()[:16]
        nonce = os.urandom(12)
        encrypted = AESGCM(key).encrypt(nonce, f'v1|data.csmar.com|{identity}'.encode(), None)
        self._cookies['D'] = base64.urlsafe_b64encode(b'\x01' + nonce + encrypted).decode().rstrip('=')

        for sc in resp.headers.get_list("set-cookie"):
            if sc.startswith("U="):
                self._cookies["U"] = sc.split(";")[0].split("=", 1)[1]

        return self._token

    def save_verified_session(self) -> None:
        from paths import workspace
        path = workspace().experience / '.csmar-session.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        import uuid
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.pending')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump({'token': self._token, 'cookies': self._cookies}, stream)
        os.chmod(temporary, 0o600)
        temporary.replace(path)

    def verify_phone(self, phone: str, code: str) -> None:
        if not re.fullmatch(r'1[3-9]\d{9}', phone) or not re.fullmatch(r'\d{6}', code):
            raise ProviderAuthError(code='PROVIDER_VERIFICATION_FORMAT', message='手机号或验证码格式不正确。')
        response = self.client.post(f'{BASE_MAIN}/home/orgPhone/verify',
            json={'phone': phone, 'verifyCode': code, 'type': '0'}, headers=self._headers, cookies=self._cookies)
        response.raise_for_status()
        result = response.json()
        if result.get('code') != 0:
            raise ProviderAuthError(code='PROVIDER_PHONE_VERIFICATION_FAILED', message=result.get('msg', '验证失败。'))
        self.save_verified_session()

    def cancel(self, outline_id: str) -> bool:
        
        resp = self.client.get(
            f"{BASE_MAIN}/single/cancel/{outline_id}",
            params={"t": self._ts()},
            headers=self._headers,
            cookies=self._cookies,
        )
        result = resp.json()
        return result.get("code") == 0

    def list_records(self) -> list[dict[str, Any]]:
        
        resp = self.client.get(
            f"{BASE_MAIN}/userCenter/getRecordList",
            params={"t": self._ts()},
            headers=self._headers,
            cookies=self._cookies,
        )
        result = resp.json()
        self._raise_if_verification_blocked(result, "getRecordList")
        if result.get("code") != 0:
            raise ProviderDataError(
                code="PROVIDER_RECORD_LIST_FAILED",
                message=f"获取下载记录失败：{result.get('msg', '未知错误')}",
                context={"response": result},
                blocking=False,
            )
        return result.get("data", [])

    def get_outline(self, outline_id: str) -> dict[str, Any]:
        
        resp = self.client.get(
            f"{BASE_MAIN}/single/getOutline/{outline_id}",
            params={"t": self._ts()},
            headers=self._headers,
            cookies=self._cookies,
        )
        result = resp.json()
        if result.get("code") != 0:
            raise ProviderDataError(
                code="PROVIDER_OUTLINE_QUERY_FAILED",
                message=f"查询 outline 失败：{result.get('msg', '未知错误')}",
                context={"response": result, "outline_id": outline_id},
                blocking=False,
            )
        return result.get("data", {})

    def download(
        self,
        stock_codes: list[str],
        start_date: str,
        end_date: str,
        *,
        table_name: str = "日个股回报率文件",
        table_name_phy: str = "TRD_Dalyr",
        table_id: int = 4,
        fields: list[dict] | None = None,
        frequency: str = FREQ_ALL,
        code_set_field: str = "Stkcd",
        time_set_field: str = "Trddt",
    ) -> pd.DataFrame:
        
        try:
            return self._download(
                stock_codes,
                start_date,
                end_date,
                table_name=table_name,
                table_name_phy=table_name_phy,
                table_id=table_id,
                fields=fields,
                frequency=frequency,
                code_set_field=code_set_field,
                time_set_field=time_set_field,
            )
        except httpx.HTTPError as e:
            raise ProviderNetworkError(
                code="PROVIDER_HTTP_TRANSPORT_ERROR",
                message=f"与 CSMAR 通信失败：{e.__class__.__name__}: {e}",
                context={"error_type": e.__class__.__name__},
                blocking=True,
                suggested_action="检查校园网/VPN 连接稳定性后重试。",
            ) from e

    def _download(
        self,
        stock_codes: list[str],
        start_date: str,
        end_date: str,
        *,
        table_name: str = "日个股回报率文件",
        table_name_phy: str = "TRD_Dalyr",
        table_id: int = 4,
        fields: list[dict] | None = None,
        frequency: str = FREQ_ALL,
        code_set_field: str = "Stkcd",
        time_set_field: str = "Trddt",
    ) -> pd.DataFrame:
        
        if not self._token:
            self.login()

        if fields is None:
            fields = TRD_DALYR_FIELDS

        field_str = ",".join(str(f["id"]) for f in fields)
        code_str = ",".join(stock_codes)

        outline_id = self._save_outline(
            code_str=code_str,
            field_str=field_str,
            start_date=start_date,
            end_date=end_date,
            table_name=table_name,
            table_name_phy=table_name_phy,
            table_id=table_id,
            frequency=frequency,
            code_set_field=code_set_field,
            time_set_field=time_set_field,
        )

        record_id = self._pack(
            outline_id,
            code_set_field=code_set_field,
            time_set_field=time_set_field,
        )
        if record_id:
            file_path = self._record_download_path(record_id)
            self._emit_pack(outline_id, 100, "ready", 0)
        else:
            file_path = self._wait_pack_result(outline_id)
        self._emit_pack(outline_id, 100, "transferring", 0)
        raw_bytes = self._download_file(file_path)
        self._emit_pack(outline_id, 100, "parsing", 0)
        return self._parse_zip(raw_bytes, table_name_phy)

    def _ts(self) -> str:
        return str(int(time.time() * 1000))

    def _save_outline(
        self,
        *,
        code_str: str,
        field_str: str,
        start_date: str,
        end_date: str,
        table_name: str,
        table_name_phy: str,
        table_id: int,
        frequency: str,
        code_set_field: str = "Stkcd",
        time_set_field: str = "Trddt",
    ) -> str:
        # The website copies estimateTime from table metadata into the outline.
        # Leaving it empty loses the estimate shown on its download page.
        metadata_response = self.client.get(
            f"{BASE_MAIN}/single/getSingleTableInit/{table_id}",
            headers=self._headers, cookies=self._cookies,
        )
        metadata_response.raise_for_status()
        metadata_result = metadata_response.json()
        self._raise_if_verification_blocked(metadata_result, "getSingleTableInit")
        if metadata_result.get("code") != 0:
            raise ProviderDataError(code="PROVIDER_TABLE_METADATA_FAILED",
                                    message="CSMAR 数据表信息读取失败，尚未提交打包请求。", blocking=True)
        metadata = metadata_result.get("data") or {}
        body = {
            "codeId": "",
            "codeStr": code_str,
            "fieldStr": field_str,
            "fileOutType": FILE_OUT_TXT,
            "tableName": table_name,
            "tableNamePhy": table_name_phy,
            "planName": "",
            "mail": "",
            "codeSetField": code_set_field,
            "timeSetField": time_set_field,
            "conditionDtos": [],
            "startTime": start_date,
            "endTime": end_date,
            "estimateTime": metadata.get("estimateTime", ""),
            "tableId": table_id,
            "downloadType": "0",
            "frequency": frequency,
        }

        resp = self.client.post(
            f"{BASE_MAIN}/single/saveOutline",
            params={"t": self._ts()},
            headers=self._headers,
            cookies=self._cookies,
            json=body,
        )
        result = resp.json()

        self._raise_if_verification_blocked(result, "saveOutline")
        if result.get("code") != 0:
            raise ProviderDataError(
                code="PROVIDER_SAVE_OUTLINE_FAILED",
                message=f"saveOutline 失败：{result.get('msg', '未知错误')}",
                context={"response": result, "body": body},
                blocking=True,
            )

        return str(result["data"])

    def _pack(
        self,
        outline_id: str,
        *,
        code_set_field: str = "Stkcd",
        time_set_field: str = "Trddt",
    ) -> str | None:
        
        body = {
            "codeSetField": code_set_field,
            "mail": "",
            "outlineId": outline_id,
            "timeSetField": time_set_field,
            "planName": "",
        }

        resp = self.client.post(
            f"{BASE_MAIN}/singleData/pack",
            params={"t": self._ts()},
            headers=self._headers,
            cookies=self._cookies,
            json=body,
        )
        result = resp.json()

        self._raise_if_verification_blocked(result, "pack")
        if result.get("code") != 0:
            raise ProviderDataError(
                code="PROVIDER_PACK_FAILED",
                message=f"pack 失败：{result.get('msg', '未知错误')}",
                context={"response": result, "outline_id": outline_id},
                blocking=True,
            )

        record_id = result.get("data")
        self.save_verified_session()
        return str(record_id) if record_id else None

    def _find_record_file_path(self, outline_id: str) -> str | None:
        
        try:
            records = self.list_records()
        except ProviderDataError:
            return None
        for rec in records:
            if str(rec.get("outlineId")) == outline_id and rec.get("filePath"):
                return rec["filePath"]
        return None

    def _record_download_path(self, record_id: str) -> str:
        response = self.client.get(
            f"{BASE_MAIN}/userCenter/recordDownload/{record_id}",
            params={"t": self._ts()}, headers=self._headers, cookies=self._cookies,
        )
        response.raise_for_status()
        result = response.json()
        self._raise_if_verification_blocked(result, "recordDownload")
        data = result.get("data") or {}
        if result.get("code") == 0 and data.get("filePath"):
            return data["filePath"]
        raise ProviderDataError(code="PROVIDER_RECORD_DOWNLOAD_FAILED",
                                message="CSMAR 历史下载记录未返回文件地址。", blocking=True)

    def _emit_pack(self, outline_id, percentage, stage, elapsed, estimate=None):
        if self._on_progress:
            self._on_progress({
                "state": "packaging" if stage in {"packing", "queued", "waiting"} else stage,
                "pack_percent": percentage, "pack_stage": stage,
                "pack_elapsed_seconds": round(elapsed), "pack_estimate_minutes": estimate,
                "outline_id": outline_id,
            })

    def _wait_pack_result(self, outline_id: str) -> str:
        # CSMAR's sdownload page sends this start message; HTTP polling alone
        # does not start the current packaging protocol.
        import websocket

        started = time.monotonic()
        deadline = started + self._poll_interval * self._poll_max_attempts
        percentage = None
        estimate = None
        try:
            outline = self.get_outline(outline_id)
            value = outline.get("estimateTime")
            if value not in (None, ""):
                estimate = float(value)
                if not math.isfinite(estimate) or estimate <= 0:
                    estimate = None
        except (ProviderDataError, httpx.HTTPError, ValueError, TypeError):
            pass
        self._emit_pack(outline_id, percentage, "waiting", 0, estimate)
        try:
            connection = websocket.create_connection(
                SINGLE_SOCKET, timeout=min(self._timeout, 30),
                origin="https://data.csmar.com",
                header={"User-Agent": DEFAULT_HEADERS["User-Agent"]},
                cookie="; ".join(f"{key}={value}" for key, value in self._cookies.items()),
            )
            try:
                connection.send(json.dumps({"outlineId": outline_id, "status": "start",
                                            "token": self._token, "signCode": SIGN_CODE}))
                while time.monotonic() < deadline:
                    connection.settimeout(max(.1, min(15, deadline - time.monotonic())))
                    try:
                        message = connection.recv()
                    except websocket.WebSocketTimeoutException:
                        self._emit_pack(outline_id, percentage, "waiting", time.monotonic() - started, estimate)
                        continue
                    if not message:
                        raise websocket.WebSocketConnectionClosedException("packaging connection closed")
                    try:
                        result = json.loads(message)
                    except (ValueError, TypeError) as exc:
                        raise ProviderDataError(code="PROVIDER_PACK_RESPONSE_INVALID",
                                                message="CSMAR 打包通道返回了无法解析的消息。", blocking=True) from exc
                    if not isinstance(result, dict):
                        raise ProviderDataError(code="PROVIDER_PACK_RESPONSE_INVALID",
                                                message="CSMAR 打包消息格式不符合预期。", blocking=True)
                    self._raise_if_verification_blocked(result, "packSocket")
                    code = result.get("code")
                    if code in _EMPTY_RESULT_CODES:
                        raise ProviderDataError(code="PROVIDER_EMPTY_RESULT",
                                                message="CSMAR 当前查询结果为 0 条。",
                                                context={"outline_id": outline_id, "csmar_code": code}, blocking=True)
                    if code not in (0, "0"):
                        raise ProviderDataError(code="PROVIDER_PACK_ERROR",
                                                message=f"CSMAR 打包失败：{result.get('msg') or code}",
                                                context={"outline_id": outline_id, "csmar_code": code}, blocking=True)
                    data = result.get("data") or {}
                    if not isinstance(data, dict):
                        raise ProviderDataError(code="PROVIDER_PACK_RESPONSE_INVALID",
                                                message="CSMAR 打包数据格式不符合预期。", blocking=True)
                    raw = data.get("percentage")
                    if raw not in (None, ""):
                        try:
                            parsed = float(str(raw).rstrip("%"))
                            if math.isfinite(parsed):
                                percentage = max(0, min(100, parsed))
                        except (ValueError, TypeError):
                            pass
                    status = str(data.get("status", ""))
                    ready = bool(data.get("filePath")) and (status == "1" or percentage == 100)
                    stage = "ready" if ready else "queued" if status == "3" else "packing"
                    self._emit_pack(outline_id, percentage, stage, time.monotonic() - started, estimate)
                    if ready:
                        return data["filePath"]
            finally:
                connection.close()
        except (websocket.WebSocketException, OSError) as exc:
            # A lost connection may occur after completion. Check the existing
            # record without submitting another packaging job.
            path = self._find_record_file_path(outline_id)
            if path:
                self._emit_pack(outline_id, 100, "ready", time.monotonic() - started, estimate)
                return path
            raise ProviderNetworkError(
                code="PROVIDER_PACK_CONNECTION_LOST",
                message="CSMAR 打包进度连接中断，尚未取得下载文件。",
                context={"outline_id": outline_id, "last_percentage": percentage,
                         "error_type": type(exc).__name__}, blocking=True,
                suggested_action="检查网络及 CSMAR 下载记录后重试。当前批次未写入，也未自动重复提交。",
            ) from exc
        path = self._find_record_file_path(outline_id)
        if path:
            return path
        self.cancel(outline_id)
        raise ProviderDataError(
            code="PROVIDER_PACK_TIMEOUT", message="等待 CSMAR 打包超过时限，已取消本次请求。",
            context={"outline_id": outline_id, "last_percentage": percentage}, blocking=True,
            suggested_action="在 CSMAR 下载记录中核对该请求的状态后重试。",
        )

    def _download_file(self, file_path: str) -> bytes:
        
        url = f"{FILE_HOST}/{file_path}"
        resp = self.client.get(
            url,
            headers={
                "User-Agent": DEFAULT_HEADERS["User-Agent"],
                "Referer": "https://data.csmar.com/",
            },
        )

        if resp.status_code != 200:
            raise ProviderDataError(
                code="PROVIDER_DOWNLOAD_FAILED",
                message=f"文件下载失败：HTTP {resp.status_code}",
                context={"url": url, "status_code": resp.status_code},
                blocking=True,
            )

        if len(resp.content) == 0:
            raise ProviderDataError(
                code="PROVIDER_DOWNLOAD_EMPTY",
                message="下载的文件为空。",
                context={"url": url},
                blocking=True,
            )

        return resp.content

    def _parse_zip(self, raw_bytes: bytes, table_name_phy: str) -> pd.DataFrame:
        
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
        except zipfile.BadZipFile as e:
            raise ProviderDataError(
                code="PROVIDER_INVALID_ZIP",
                message=f"下载的文件不是有效的 ZIP：{e}",
                blocking=True,
            ) from e

        data_files = [name for name in zf.namelist()
                      if name.startswith(table_name_phy) and name.endswith(".txt")
                      and not name.endswith("[DES][txt].txt")]
        data_file = data_files[0] if data_files else None

        if data_file is None:
            has_des = any(
                name.startswith(table_name_phy) and name.endswith("[DES][txt].txt")
                for name in zf.namelist()
            )
            if has_des:

                raise ProviderDataError(
                    code="PROVIDER_EMPTY_RESULT",
                    message="ZIP 中仅含字段说明文件，无数据文件（0 行结果）。",
                    context={"zip_contents": zf.namelist()},
                    blocking=True,
                )
            raise ProviderDataError(
                code="PROVIDER_DATA_FILE_NOT_FOUND",
                message=f"ZIP 中未找到数据文件（期望 {table_name_phy}.txt）。",
                context={"zip_contents": zf.namelist()},
                blocking=True,
            )

        if table_name_phy in {"IDX_Idxinfo", "IDX_Idxtrd"}:
            frames = []
            for name in data_files:
                content = zf.read(name)
                for encoding in ("utf-8-sig", "gb18030"):
                    try:
                        decoded = content.decode(encoding)
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    raise ProviderDataError(code="PROVIDER_ENCODING_ERROR", message=f"无法解码 {name}。")
                frames.append(pd.read_csv(io.StringIO(decoded), sep="\t", dtype={"Indexcd": "string"}))
            return pd.concat(frames, ignore_index=True)

        content = zf.read(data_file)
        for enc in ("utf-8-sig", "gbk", "utf-8", "gb18030"):
            try:
                text = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ProviderDataError(
                code="PROVIDER_ENCODING_ERROR",
                message="无法解码数据文件（尝试 utf-8-sig/gbk/utf-8/gb18030 均失败）。",
                blocking=True,
            )

        if table_name_phy == "AF_Bench":
            df = self._parse_af_bench_tsv(text, data_file)
        else:
            try:
                df = pd.read_csv(io.StringIO(text), sep="\t")
            except pd.errors.ParserError as e:
                raise ProviderDataError(
                    code="PROVIDER_TSV_PARSE_ERROR",
                    message=f"CSMAR Tab 分隔文件解析失败：{e}",
                    context={"file": data_file, "table_name_phy": table_name_phy},
                    blocking=True,
                    suggested_action="核对原始文件字段数与表配置，不要使用 on_bad_lines=skip。",
                ) from e

        if df.empty:
            raise ProviderDataError(
                code="PROVIDER_EMPTY_RESULT",
                message="下载的数据为空（0 行）。",
                context={"file": data_file},
                blocking=True,
            )

        return df

    def _parse_af_bench_tsv(self, text: str, data_file: str) -> pd.DataFrame:
        
        lines = text.splitlines()
        if not lines:
            raise ProviderDataError(
                code="PROVIDER_EMPTY_RESULT",
                message="AF_Bench 数据文件为空（0 行）。",
                context={"file": data_file},
                blocking=True,
                suggested_action="交由上层按表级空批次规则判定是否推进水位。",
            )

        columns = tuple(part.strip() for part in lines[0].split("\t"))
        if columns != _AF_BENCH_COLUMNS:
            raise ProviderDataError(
                code="PROVIDER_AF_BENCH_HEADER_MISMATCH",
                message=("AF_Bench 表头与已核对的 18 字段契约不一致，已停止多行文本还原。"),
                context={"file": data_file, "columns": list(columns)},
                blocking=True,
                suggested_action="登录 CSMAR 核对 AF_Bench 字段顺序后更新解析契约。",
            )

        physical_records: list[list[str]] = []
        current: list[str] = []
        for physical_line_number, line in enumerate(lines[1:], start=2):
            if self._is_af_bench_record_start(line):
                if current:
                    physical_records.append(current)
                current = [line]
                continue
            if not current:
                if not line.strip():
                    continue
                raise ProviderDataError(
                    code="PROVIDER_AF_BENCH_RECORD_BOUNDARY_ERROR",
                    message=f"AF_Bench 第 {physical_line_number} 行不属于任何逻辑记录。",
                    context={"file": data_file, "physical_line": physical_line_number},
                    blocking=True,
                    suggested_action="核对该物理行前后的证券代码与固定字段边界。",
                )
            current.append(line)
        if current:
            physical_records.append(current)

        rows = [
            self._restore_af_bench_record(record, index, data_file)
            for index, record in enumerate(physical_records, start=1)
        ]
        return pd.DataFrame(rows, columns=_AF_BENCH_COLUMNS)

    @staticmethod
    def _is_af_bench_record_start(line: str) -> bool:
        
        prefix = line.split("\t", maxsplit=_AF_BENCH_PREFIX_FIELD_COUNT)
        if len(prefix) != _AF_BENCH_PREFIX_FIELD_COUNT + 1:
            return False

        def blank_or_matches(value: str, pattern: re.Pattern[str]) -> bool:
            cleaned = value.strip()
            return not cleaned or bool(pattern.fullmatch(cleaned))

        code = prefix[0].strip()
        if code:
            code_evidence = bool(_STOCK_CODE_PATTERN.fullmatch(code)) and blank_or_matches(
                prefix[1], _NUMBER_PATTERN
            )
        else:
            
            code_evidence = bool(_NUMBER_PATTERN.fullmatch(prefix[1].strip())) and bool(
                _DATE_PATTERN.fullmatch(prefix[3].strip())
            )
        return (
            code_evidence

            and blank_or_matches(prefix[3], _DATE_PATTERN)
            and blank_or_matches(prefix[8], _NUMBER_PATTERN)
        )

    @staticmethod
    def _restore_af_bench_record(
        physical_lines: list[str], logical_record_number: int, data_file: str
    ) -> list[str]:
        
        prefix = physical_lines[0].split("\t", maxsplit=_AF_BENCH_PREFIX_FIELD_COUNT)
        if len(prefix) != _AF_BENCH_PREFIX_FIELD_COUNT + 1:
            raise ProviderDataError(
                code="PROVIDER_AF_BENCH_FIELD_COUNT_ERROR",
                message=f"AF_Bench 第 {logical_record_number} 条记录的前置字段不足。",
                context={"file": data_file, "logical_record": logical_record_number},
                blocking=True,
                suggested_action="核对原始记录的证券代码与前 13 个固定字段。",
            )

        rankbench_and_metrics = "\n".join([prefix[-1], *physical_lines[1:]]).rstrip("\r\n")
        tail = rankbench_and_metrics.rsplit("\t", maxsplit=_AF_BENCH_METRIC_FIELD_COUNT)
        if len(tail) != _AF_BENCH_METRIC_FIELD_COUNT + 1:
            raise ProviderDataError(
                code="PROVIDER_AF_BENCH_FIELD_COUNT_ERROR",
                message=(
                    f"AF_Bench 第 {logical_record_number} 条记录无法定位 "
                    f"{_AF_BENCH_METRIC_FIELD_COUNT} 个末尾度量字段。"
                ),
                context={"file": data_file, "logical_record": logical_record_number},
                blocking=True,
                suggested_action="核对 Rankbench 文本与末尾四个度量字段的边界。",
            )

        metrics = [value.strip() for value in tail[1:]]
        invalid_metrics = [
            index
            for index, value in enumerate(metrics, start=1)
            if value and not _NUMBER_PATTERN.fullmatch(value)
        ]
        if invalid_metrics:
            raise ProviderDataError(
                code="PROVIDER_AF_BENCH_METRIC_ERROR",
                message=(
                    f"AF_Bench 第 {logical_record_number} 条记录的末尾度量字段"
                    f" {invalid_metrics} 不是数值或空值。"
                ),
                context={
                    "file": data_file,
                    "logical_record": logical_record_number,
                    "invalid_metric_positions": invalid_metrics,
                },
                blocking=True,
                suggested_action="检查是否存在未识别的记录边界或源端字段变更。",
            )

        fields = [*prefix[:_AF_BENCH_PREFIX_FIELD_COUNT], tail[0], *metrics]
        if len(fields) != len(_AF_BENCH_COLUMNS):
            raise ProviderDataError(
                code="PROVIDER_AF_BENCH_FIELD_COUNT_ERROR",
                message=(
                    f"AF_Bench 第 {logical_record_number} 条记录还原后为 "
                    f"{len(fields)} 字段，预期 18 字段。"
                ),
                context={"file": data_file, "logical_record": logical_record_number},
                blocking=True,
                suggested_action="检查源端字段顺序与多行文本边界。",
            )
        return fields
