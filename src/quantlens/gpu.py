"""Shared training requests: Spark prepares data, the host owns the CUDA device."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from exceptions import ContractViolation
from paths import workspace


def shared_root():
    if os.environ.get('SPARK_HOME') and Path('/data/ads').is_dir():
        return Path('/data/ads/_training')
    return workspace().data / 'ads/_training'


def atomic_json(path, payload):
    pending = path.with_suffix('.pending')
    pending.write_text(json.dumps(payload, ensure_ascii=False))
    pending.replace(path)


def worker_status():
    path = shared_root() / 'worker.json'
    try:
        doc = json.loads(path.read_text())
        if time.time() - doc['heartbeat'] >= 20:
            return {}
        # The shared heartbeat contains a host PID, not a container PID.
        if not (os.environ.get('SPARK_HOME') and Path('/data/ads').is_dir()):
            pid = int(doc['pid'])
            if pid <= 0:
                return {}
            os.kill(pid, 0)
        return doc
    except (OSError, ValueError, KeyError):
        return {}


def ensure_worker():
    if worker_status():
        return
    root = shared_root()
    root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    with (root / 'worker.log').open('a') as log:
        subprocess.Popen([sys.executable, '-m', 'quantlens.gpu_worker'], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(100):
        if worker_status():
            return
        time.sleep(.1)
    raise ContractViolation(code='TRAINING_WORKER_UNAVAILABLE', message='训练进程未能启动', suggested_action='检查 data/ads/_training/worker.log')


def use_gpu():
    preference = os.environ.get('QUANTLENS_TRAIN_DEVICE', 'auto')
    if preference not in {'auto', 'cuda', 'cpu'}:
        raise ValueError('QUANTLENS_TRAIN_DEVICE must be auto, cuda or cpu')
    available = bool(worker_status().get('cuda'))
    if os.environ.get('QUANTLENS_COMPUTE_LOCAL') == '1' and preference != 'cpu':
        try:
            import torch
            available = torch.cuda.is_available()
        except ImportError:
            available = False
    if preference == 'cuda' and not available:
        raise ContractViolation(code='CUDA_UNAVAILABLE', message='独立显卡不可用，已停止本次训练', suggested_action='检查训练进程与 CUDA 驱动')
    return preference != 'cpu' and available


def request(operation, *, train=None, features=(), label=None, predict=None, **parameters):
    root = shared_root()
    local = os.environ.get('QUANTLENS_COMPUTE_LOCAL') == '1'
    if not local and not worker_status():
        raise ContractViolation(code='TRAINING_WORKER_UNAVAILABLE', message='训练进程不可用', suggested_action='从前端或 quantlens.compute.submit 发起训练')
    ident = uuid.uuid4().hex
    directory = root / ident
    directory.mkdir(parents=True)
    if os.geteuid() == 0:
        owner = root.stat()
        os.chown(directory, owner.st_uid, owner.st_gid)
    uri = f'hdfs://nn-rm:9000/quantlens/_tmp/training/{ident}'
    payload = {'id': ident, 'operation': operation, 'features': list(features), 'label': label, 'device': 'cuda' if use_gpu() else 'cpu', 'parameters': parameters}
    if train is not None:
        columns = list(features) + ([label] if label else [])
        if local:
            (directory / 'train').mkdir()
            train.select(*columns).toPandas().to_parquet(directory / 'train/part.parquet', index=False)
        else:
            train.select(*columns).write.mode('errorifexists').parquet(uri + '/train')
        payload['train'] = uri + '/train'
    if predict is not None:
        if local:
            (directory / 'predict').mkdir()
            predict.select('code', 'date', *features).toPandas().to_parquet(directory / 'predict/part.parquet', index=False)
        else:
            predict.select('code', 'date', *features).write.mode('errorifexists').parquet(uri + '/predict')
        payload['predict'] = uri + '/predict'
    payload['output'] = uri + '/output'
    if local:
        from quantlens.gpu_worker import execute
        payload['local'] = True
        started = time.monotonic()
        result = execute(payload, directory)
        result.update(device=payload['device'], seconds=round(time.monotonic()-started,3))
        atomic_json(directory / 'response.json', result)
        return result
    atomic_json(directory / 'request.json', payload)
    print(f'QUANTLENS_TRAINING={operation} device={payload["device"]} request={ident}', flush=True)
    started = time.monotonic()
    while not (directory / 'response.json').exists():
        if not worker_status():
            raise ContractViolation(code='TRAINING_WORKER_LOST', message='训练进程中断', suggested_action='检查训练日志后重新发起任务')
        if time.monotonic() - started > 7200:
            atomic_json(directory / 'cancel.json', {'cancel': True})
            raise TimeoutError('GPU training exceeded two hours')
        time.sleep(.25)
    response = json.loads((directory / 'response.json').read_text())
    if response.get('error'):
        raise ContractViolation(code='TRAINING_FAILED', message=response['error'], suggested_action='检查训练进程日志；不会静默改用其他算法')
    print(f'QUANTLENS_TRAINED={operation} device={response["device"]} seconds={response["seconds"]}', flush=True)
    # Training exports are temporary inputs, not the immutable warehouse evidence.
    import shutil
    spark = (train if train is not None else predict).sparkSession
    for kind in ('train', 'predict'):
        if kind in payload:
            path = spark._jvm.org.apache.hadoop.fs.Path(payload[kind])
            path.getFileSystem(spark._jsc.hadoopConfiguration()).delete(path, True)
            shutil.rmtree(directory / kind)
    (directory / 'predictions.parquet').unlink(missing_ok=True)
    return response


class TreeModel:
    def __init__(self, model_id, features):
        self.model_id = model_id
        self.features = features

    def transform(self, frame):
        response = request('predict_tree', predict=frame, features=self.features, model_id=self.model_id)
        predictions = frame.sparkSession.read.parquet(response['output'])
        return frame.join(predictions, ['code', 'date'])


def fit_tree(frame, features, label='ret', seed=42):
    result = request('fit_tree', train=frame, features=features, label=label, seed=seed)
    return TreeModel(result['model_id'], features)


class ClusterModel:
    def __init__(self, result, features):
        import numpy as np
        from types import SimpleNamespace
        self.centers = np.asarray(result['centers'])
        self.features = features
        self.summary = SimpleNamespace(trainingCost=result['inertia'])

    def clusterCenters(self):
        return self.centers

    def transform(self, frame):
        from pyspark.sql import functions as F
        distances = [sum((F.col(name) - float(value)) ** 2 for name, value in zip(self.features, center)) for center in self.centers]
        array = F.array(*distances)
        return frame.withColumn('prediction', (F.array_position(array, F.array_min(array)) - 1).cast('int'))


class PCAModel:
    def __init__(self, result):
        import numpy as np
        from pyspark.ml.linalg import DenseMatrix, Vectors
        matrix = np.asarray(result['components'])
        self.pc = DenseMatrix(matrix.shape[0], matrix.shape[1], matrix.ravel(order='F'))
        self.explainedVariance = Vectors.dense(result['explained_variance'])
        self.names = result['features']

    def transform(self, frame):
        from pyspark.sql import functions as F
        from pyspark.ml.functions import array_to_vector
        coefficients = self.pc.toArray()
        columns = [sum(F.col(name) * float(coefficients[i, j]) for i, name in enumerate(self.names)) for j in range(coefficients.shape[1])]
        return frame.withColumn('pca', array_to_vector(F.array(*columns)))
