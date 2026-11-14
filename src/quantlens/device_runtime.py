"""Fresh-process CUDA execution with bounded, observable CPU recovery."""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


def gpu_inventory():
    """Query the driver without initializing a CUDA context in the supervisor."""
    try:
        result = subprocess.run([
            'nvidia-smi', '--query-gpu=uuid,name,memory.free,memory.total',
            '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=3, check=True)
        devices = []
        visible = os.environ.get('CUDA_VISIBLE_DEVICES')
        allowed = None if visible is None else {value.strip() for value in visible.split(',') if value.strip()}
        for index, row in enumerate(csv.reader(io.StringIO(result.stdout))):
            ident, name, free, total = [value.strip() for value in row]
            if allowed is not None and str(index) not in allowed and ident not in allowed:
                continue
            if any(token in name.lower() for token in ('tegra', 'jetson', 'orin')):
                continue
            devices.append({'uuid': ident, 'name': name, 'free_mib': int(free), 'total_mib': int(total)})
        return sorted(devices, key=lambda item: (-item['free_mib'], item['uuid']))
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def device_error(error_type, message):
    """Only device failures qualify; input, math and application bugs stay errors."""
    text = message.lower()
    if error_type in {'ValueError', 'KeyError', 'TypeError'}:
        return False
    if any(token in text for token in ('device-side assert', 'illegal memory access',
                                       'index out of bounds', 'invalid argument')):
        return False
    return any(token in text for token in (
        'cuda out of memory', 'cuda is unavailable', 'cuda error', 'cuda driver',
        'cuda-capable device', 'no cuda gpus', 'not compiled with cuda',
        'no kernel image', 'invalid device ordinal', 'device has been lost',
        'device is lost', 'driver shutting down', 'cublas_status_alloc_failed',
        'cublas_status_not_initialized', 'cusolver_status_alloc_failed',
        'cusolver_status_not_initialized',
        'xgboost did not activate cuda', 'gpu disappeared', 'cuda initialization',
        'cuda runtime', 'cuda-capable devices are busy',
    ))


def _stop(process):
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        # A driver stuck in an uninterruptible kernel call must not block the
        # supervisor forever. Do not retry into shared outputs until it exits.
        process.wait(timeout=3)


def run_attempt(payload, directory, device, gpu=None):
    directory = Path(directory)
    token = uuid.uuid4().hex
    request_path = directory / f'attempt-{token}.json'
    result_path = directory / f'attempt-{token}-result.json'
    request_path.write_text(json.dumps({**payload, 'device': device}), encoding='utf-8')
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
               CUDA_VISIBLE_DEVICES=gpu['uuid'] if gpu else '')
    with (directory / 'attempts.log').open('a') as log:
        process = subprocess.Popen([sys.executable, '-m', 'quantlens.gpu_worker',
                                    '--attempt', str(request_path), str(result_path)],
                                   env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        started, checked = time.monotonic(), 0.0
        try:
            while process.poll() is None:
                if (directory / 'cancel.json').exists():
                    _stop(process)
                    return {'error': 'Training cancelled', 'device_error': False}
                if time.monotonic() - started > 7100:
                    _stop(process)
                    return {'error': 'Training attempt timed out', 'device_error': False}
                if gpu and time.monotonic() - checked >= 3:
                    checked = time.monotonic()
                    if gpu['uuid'] not in {item['uuid'] for item in gpu_inventory()}:
                        _stop(process)
                        return {'error': 'GPU disappeared during execution', 'device_error': True}
                time.sleep(.1)
            if result_path.exists():
                return json.loads(result_path.read_text())
            return {'error': f'Training process exited with code {process.returncode}',
                    'device_error': device == 'cuda' and process.returncode in {-6, -11}}
        finally:
            if process.poll() is None:
                _stop(process)
            request_path.unlink(missing_ok=True)
            result_path.unlink(missing_ok=True)


def execute_resilient(payload, directory, *, attempt=None, inventory=None):
    """Retry the same operation and immutable inputs once on a fresh CPU process."""
    attempt = attempt or run_attempt
    inventory = inventory or gpu_inventory
    policy = payload.get('device_policy', payload.get('device', 'auto'))
    if policy not in {'auto', 'cuda', 'cpu'}:
        raise ValueError('Invalid training device policy')
    devices = [] if policy == 'cpu' else inventory()
    device = 'cuda' if devices else 'cpu'
    gpu = devices[0] if devices else None
    started = time.monotonic()
    result = attempt(payload, directory, device, gpu)
    attempts = [{'device': device, 'error': result.get('error', '')}]
    reason = ''
    if result.get('error') and device == 'cuda' and result.get('device_error'):
        reason = result['error']
        if payload.get('operation') == 'predict_tree' and not payload.get('local'):
            # A device can disappear after export but before acknowledgement.
            # Remove only this request's unpublished output before retrying.
            ident = payload['id']
            import re
            expected = f'hdfs://nn-rm:9000/quantlens/_tmp/training/{ident}/output'
            if not re.fullmatch('[a-f0-9]{32}', ident) or payload.get('output') != expected:
                raise ValueError('Invalid prediction output path')
            from storage.hdfs import HDFS
            HDFS().rm(expected)
        # Results from the interrupted attempt were never acknowledged to callers.
        for name in ('model.ubj', 'predictions.parquet', 'standardized.parquet'):
            (Path(directory) / name).unlink(missing_ok=True)
        device = 'cpu'
        result = attempt(payload, directory, device, None)
        attempts.append({'device': device, 'error': result.get('error', '')})
    result.update(device=device, seconds=round(time.monotonic()-started, 3),
                  device_attempts=attempts, fallback_reason=reason,
                  gpu=gpu, device_policy=policy)
    return result
