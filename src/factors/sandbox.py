"""Automatic process isolation for every factor, including Spark workers.

Only Arrow IPC crosses the process boundary; never unpickle a factor result.
Linux Landlock and seccomp are mandatory, with no in-process fallback.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from threading import BoundedSemaphore

from exceptions import ContractViolation, FactorExecutionError

_SLOTS = BoundedSemaphore(2)
_WORKER = Path(__file__).with_name("_sandbox_worker.py")
MAX_OUTPUT_BYTES = 128 * 1024 * 1024
DEFAULT_TIMEOUT = 30.0


def execute_factor(source, panel, *, timeout=DEFAULT_TIMEOUT, check_inputs=False):
    import pandas as pd
    import pyarrow as pa
    import pyarrow.ipc as ipc

    if not isinstance(source, str) or len(source.encode("utf-8")) > 512 * 1024:
        raise ContractViolation(code="FACTOR_SOURCE_INVALID", message="因子源码超过执行限制。")
    if not panel or any(not isinstance(value, pd.DataFrame) for value in panel.values()):
        raise ContractViolation(code="FACTOR_INPUT_INVALID", message="因子输入必须为非空 DataFrame 面板。")
    if not _SLOTS.acquire(timeout=timeout):
        raise FactorExecutionError(code="FACTOR_SANDBOX_BUSY", message="因子执行资源繁忙。")
    try:
        with tempfile.TemporaryDirectory(prefix="quantlens-factor-") as directory:
            root = Path(directory)
            fields = list(panel)
            for index, field in enumerate(fields):
                table = pa.Table.from_pandas(panel[field], preserve_index=True)
                with ipc.new_file(root / f"{index}.arrow", table.schema) as writer:
                    writer.write_table(table)
            frequencies = [getattr(panel[name].index, "freqstr", None) for name in fields]
            (root / "request.json").write_text(json.dumps({"fields": fields, "frequencies": frequencies, "source": source,
                                                          "check_inputs": check_inputs}), encoding="utf-8")
            env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8",
                   "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
                   "NUMEXPR_NUM_THREADS": "1", "ARROW_NUM_THREADS": "1", "MALLOC_ARENA_MAX": "2"}
            with (root / "result.arrow").open("wb") as output, (root / "error.txt").open("wb") as errors:
                process = subprocess.Popen(
                    [sys.executable, "-I", "-B", str(_WORKER), str(root)],
                    cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                    close_fds=True, start_new_session=True,
                )
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired as exc:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise FactorExecutionError(code="FACTOR_TIMEOUT", message="因子执行超时，已终止计算进程。") from exc
            if process.returncode:
                # Error data is untrusted and may contain deliberately forged text.
                with (root / "error.txt").open("rb") as stream:
                    detail = stream.read(4096).decode("utf-8", errors="replace")
                if process.returncode == 79:
                    raise ContractViolation(code="FACTOR_INPUT_MODIFIED", message="因子修改了输入面板。")
                if process.returncode == 80:
                    raise ContractViolation(code="FACTOR_OUTPUT_INVALID", message="因子应返回 DataFrame。")
                if process.returncode == 81:
                    raise ContractViolation(code="FACTOR_ENTRY_MISSING", message="因子缺少 compute(panel) 入口。")
                code = "FACTOR_SANDBOX_UNAVAILABLE" if process.returncode == 78 else "FACTOR_EXECUTION_FAILED"
                raise FactorExecutionError(code=code, message=f"隔离执行失败：{detail[-1000:] or process.returncode}")
            if not 0 < (root / "result.arrow").stat().st_size <= MAX_OUTPUT_BYTES:
                raise FactorExecutionError(code="FACTOR_OUTPUT_INVALID", message="因子输出超过大小限制或为空。")
            try:
                result = ipc.open_file(root / "result.arrow").read_all().to_pandas()
            except Exception as exc:
                raise FactorExecutionError(code="FACTOR_OUTPUT_INVALID", message="因子输出格式无效。") from exc
            if not isinstance(result, pd.DataFrame):
                raise ContractViolation(code="FACTOR_OUTPUT_INVALID", message="因子应返回 DataFrame。")
            for frame in panel.values():
                if isinstance(result.index, (pd.DatetimeIndex, pd.TimedeltaIndex)) and result.index.equals(frame.index):
                    result.index.freq = frame.index.freq
                    break
            return result
    finally:
        _SLOTS.release()
