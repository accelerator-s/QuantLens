"""One host worker serializes CUDA training for all Spark submissions."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import threading
import time
import traceback
import sys
from importlib.metadata import version as package_version

import numpy as np

from quantlens.gpu import atomic_json, shared_root


def tensor_data(frame, features, device):
    import torch
    values = np.ascontiguousarray(frame[features].to_numpy(dtype=np.float64))
    if not np.isfinite(values).all():
        raise ValueError('Training inputs contain non-finite values')
    return torch.as_tensor(values, device=device, dtype=torch.float64)


def fit_linear(x, y):
    import torch
    # The small normal matrix keeps memory proportional to rows × features.
    return torch.linalg.pinv(x.T @ x, hermitian=True) @ (x.T @ y)


def fit_ridge(x, y, penalty=.01):
    import torch
    covariance = x.T @ x / len(x)
    identity = torch.eye(x.shape[1], dtype=x.dtype, device=x.device)
    return torch.linalg.solve(covariance + penalty * identity, x.T @ y / len(x))


def fit_logistic(x, y, reg=.01, max_iter=100):
    import torch
    scale = x.std(dim=0, correction=1)
    scale = torch.where(scale > 0, scale, torch.ones_like(scale))
    x = x / scale
    coefficients = torch.zeros(x.shape[1], dtype=x.dtype, device=x.device, requires_grad=True)
    intercept = torch.zeros((), dtype=x.dtype, device=x.device, requires_grad=True)
    optimizer = torch.optim.LBFGS([coefficients, intercept], max_iter=max_iter, line_search_fn='strong_wolfe', tolerance_grad=1e-7, tolerance_change=1e-10)
    def closure():
        optimizer.zero_grad()
        loss = torch.nn.functional.binary_cross_entropy_with_logits(x @ coefficients + intercept, y) + reg * coefficients.square().sum() / 2
        loss.backward()
        return loss
    optimizer.step(closure)
    return coefficients.detach() / scale


def fit_pca(x, threshold=.8):
    import torch
    centered = x - x.mean(dim=0)
    covariance = centered.T @ centered / max(1, len(x) - 1)
    values, vectors = torch.linalg.eigh(covariance)
    order = torch.argsort(values, descending=True)
    values = values[order].clamp_min(0)
    vectors = vectors[:, order]
    total = values.sum()
    if not float(total) > 0:
        raise ValueError('PCA training variance is zero')
    variance = values / total
    k = min(len(values), int(torch.searchsorted(variance.cumsum(0), threshold)) + 1)
    # Fix the otherwise arbitrary eigenvector signs.
    signs = vectors[vectors.abs().argmax(dim=0), torch.arange(vectors.shape[1], device=x.device)].sign()
    vectors *= signs
    return vectors[:, :k], variance[:k]


def fit_kmeans(x, k, seed=42, max_iter=50):
    import torch
    # Share the same seeded initialization across CPU and CUDA.
    generator = torch.Generator(device='cpu').manual_seed(seed)
    n = len(x)
    centers = [x[int(torch.randint(n, (), generator=generator))]]
    minimum = (x - centers[0]).square().sum(dim=1)
    for _ in range(1, k):
        if float(minimum.sum()) == 0:
            raise ValueError('Not enough distinct observations for KMeans')
        index = int(torch.multinomial(minimum.cpu(), 1, generator=generator)[0])
        centers.append(x[index])
        minimum = torch.minimum(minimum, (x - centers[-1]).square().sum(dim=1))
    centers = torch.stack(centers)
    inertia = 0.
    for _ in range(max_iter):
        sums = torch.zeros_like(centers)
        counts = torch.zeros(k, device=x.device, dtype=x.dtype)
        for chunk in x.split(131072):
            distances = torch.cdist(chunk, centers).square()
            labels = distances.argmin(dim=1)
            sums.index_add_(0, labels, chunk)
            counts.index_add_(0, labels, torch.ones(len(chunk), device=x.device, dtype=x.dtype))
        updated = torch.where((counts > 0)[:, None], sums / counts.clamp_min(1)[:, None], centers)
        shift = (updated - centers).square().sum()
        centers = updated
        if float(shift) < 1e-8:
            break
    for chunk in x.split(131072):
        inertia += float(torch.cdist(chunk, centers).square().min(dim=1).values.sum())
    return centers, inertia


def read_frame(payload, kind, directory):
    import pyarrow.parquet as pq
    from storage.hdfs import HDFS
    uri = payload[kind]
    prefix = f'hdfs://nn-rm:9000/quantlens/_tmp/training/{payload["id"]}/{kind}'
    if uri != prefix:
        raise ValueError('Invalid training input path')
    ready = directory / f'{kind}.ready'
    if not payload.get('local') and not ready.exists():
        import shutil
        if (directory / kind).exists():
            shutil.rmtree(directory / kind)
        HDFS().get(uri, f'/data/ads/_training/{payload["id"]}/{kind}')
        ready.touch()
    return pq.read_table(directory / kind).to_pandas()


def execute(payload, directory):
    operation = payload['operation']
    device = payload['device']
    features = payload['features']
    if not features or len(features) > (10000 if operation == 'standardize' else 256):
        raise ValueError('Invalid training features')
    if device == 'cuda':
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA is unavailable')
    if operation == 'predict_tree':
        import xgboost as xgb
        import pyarrow as pa
        import pyarrow.parquet as pq
        from storage.hdfs import HDFS
        model_id = payload['parameters']['model_id']
        if not re.fullmatch('[a-f0-9]{32}', model_id):
            raise ValueError('Invalid model id')
        model = xgb.Booster()
        model.load_model(directory.parent / model_id / 'model.ubj')
        model.set_param({'device': device})
        frame = read_frame(payload, 'predict', directory)
        predictions = model.predict(xgb.DMatrix(frame[features].to_numpy(dtype=np.float32)))
        actual = json.loads(model.save_config())['learner']['generic_param']['device']
        if device == 'cuda' and not actual.startswith('cuda'):
            raise RuntimeError('XGBoost did not activate CUDA')
        table = pa.Table.from_pandas(frame[['code','date']], preserve_index=False).append_column('prediction', pa.array(predictions))
        target = directory / 'predictions.parquet'
        pq.write_table(table, target)
        if payload.get('local'):
            return {'output': str(target)}
        HDFS().mkdir(payload['output'])
        HDFS().put(f'/data/ads/_training/{payload["id"]}/predictions.parquet', payload['output'] + '/predictions.parquet')
        return {'output': payload['output']}
    frame = read_frame(payload, 'train', directory)
    if operation == 'standardize':
        import pandas as pd
        import torch
        # Match the factor pipeline: rounded raw values, linear quantiles, ddof=0.
        raw = frame[features].replace([np.inf, -np.inf], np.nan).round(6)
        x = torch.as_tensor(raw.to_numpy(copy=True), dtype=torch.float64, device=device)
        lower = torch.nanquantile(x, .01, dim=1, keepdim=True)
        upper = torch.nanquantile(x, .99, dim=1, keepdim=True)
        clipped = torch.minimum(torch.maximum(x, lower), upper)
        centered = clipped - torch.nanmean(clipped, dim=1, keepdim=True)
        std = torch.nanmean(centered.square(), dim=1, keepdim=True).sqrt()
        score = torch.where(std > 0, centered / std, clipped * 0)
        output = pd.DataFrame(score.cpu().numpy(), index=raw.index, columns=features)
        target = directory / 'standardized.parquet'
        output.to_parquet(target)
        return {'output': str(target), 'implementation': 'winsor_zscore_float64_v1'}
    if operation == 'fit_tree':
        import xgboost as xgb
        model = xgb.XGBRegressor(n_estimators=30, max_depth=3, learning_rate=.1, reg_lambda=0., objective='reg:squarederror', random_state=payload['parameters'].get('seed',42), tree_method='hist', device=device, n_jobs=4)
        model.fit(frame[features].to_numpy(dtype=np.float32), frame[payload['label']].to_numpy(dtype=np.float32))
        actual = json.loads(model.get_booster().save_config())['learner']['generic_param']['device']
        if device == 'cuda' and not actual.startswith('cuda'):
            raise RuntimeError('XGBoost did not activate CUDA')
        if device == 'cuda':
            import torch
            torch.cuda.synchronize()
        model.save_model(directory / 'model.ubj')
        return {'model_id': payload['id'], 'implementation': 'xgboost_hist_v1', 'actual_device': actual}
    import torch
    x = tensor_data(frame, features, device)
    if operation in {'linear','logistic','ridge'}:
        y = torch.as_tensor(frame[payload['label']].to_numpy(dtype=np.float64, copy=True), device=device)
        if not torch.isfinite(y).all():
            raise ValueError('Training labels contain non-finite values')
        coefficients = {'linear': fit_linear, 'logistic': fit_logistic, 'ridge': fit_ridge}[operation](x,y)
        return {'coefficients': coefficients.cpu().tolist()}
    if operation == 'pca':
        components, variance = fit_pca(x)
        return {'components': components.cpu().tolist(), 'explained_variance': variance.cpu().tolist(), 'features': features}
    if operation == 'kmeans':
        candidates = []
        for k in range(5,9):
            if len(x) < k: continue
            centers, inertia = fit_kmeans(x,k,seed=payload['parameters'].get('seed',42))
            candidates.append({'k':k, 'centers':centers.cpu().tolist(), 'inertia':inertia})
        return {'candidates': candidates}
    raise ValueError('Unsupported training operation: ' + operation)


def attempt_main(request_path, result_path):
    from quantlens.device_runtime import device_error
    payload = json.loads(Path(request_path).read_text())
    try:
        result = execute(payload, Path(request_path).parent)
        if payload['device'] == 'cuda':
            import torch
            torch.cuda.synchronize()
    except Exception as exc:
        traceback.print_exc()
        result = {'error': f'{type(exc).__name__}: {exc}',
                  'device_error': payload['device'] == 'cuda' and device_error(type(exc).__name__, str(exc))}
    atomic_json(Path(result_path), result)


def main():
    from quantlens.device_runtime import execute_resilient, gpu_inventory
    root = shared_root()
    root.mkdir(parents=True,exist_ok=True)
    lock = (root/'worker.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    info = {'pid': os.getpid(), 'torch': package_version('torch'), 'engine_version': 2}
    def heartbeat():
        while True:
            devices = gpu_inventory()
            atomic_json(root/'worker.json', {**info, 'cuda': bool(devices),
                        'name': devices[0]['name'] if devices else '', 'devices': devices,
                        'heartbeat':time.time()})
            time.sleep(3)
    threading.Thread(target=heartbeat,daemon=True).start()
    print(json.dumps(info),flush=True)
    while True:
        for request_path in sorted(root.glob('*/request.json')):
            directory = request_path.parent
            if (directory/'response.json').exists() or (directory/'cancel.json').exists():
                continue
            payload = json.loads(request_path.read_text())
            if payload.get('id') != directory.name or not re.fullmatch('[a-f0-9]{32}',directory.name):
                continue
            start = time.monotonic()
            try:
                result = execute_resilient(payload,directory)
                print(f'{payload["id"]} {payload["operation"]} {result["device"]} {result["seconds"]}s',flush=True)
            except Exception as exc:
                traceback.print_exc()
                result = {'error':f'{type(exc).__name__}: {exc}'}
            atomic_json(directory/'response.json',result)
        time.sleep(.2)


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--attempt':
        attempt_main(sys.argv[2], sys.argv[3])
    else:
        main()
