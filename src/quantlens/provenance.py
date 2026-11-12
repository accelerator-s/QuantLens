import hashlib
import importlib.util
import shutil
from pathlib import Path

from exceptions import ContractViolation
from quantlens import store


def capture(root):
    source=root/'source'
    source.mkdir(exist_ok=True)
    entries=[]
    for name in ['spark_features.py','spark_engine.py','selection.py','evaluation.py']:
        path=Path(__file__).parent/name
        content=path.read_bytes()
        shutil.copyfile(path,source/name)
        entries.append({'path':name,'sha256':hashlib.sha256(content).hexdigest()})
    store.write_json(source/'manifest.json',{'files':entries,'created_at':store.now()})
    return entries


def feature_builder(root):
    path=root/'source'/'spark_features.py'
    if not path.exists():
        from quantlens.spark_features import build
        return build
    manifest=__import__('json').loads((root/'source'/'manifest.json').read_text())
    expected=next(e['sha256'] for e in manifest['files'] if e['path']==path.name)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
        raise ContractViolation(code='QL_SOURCE_CHANGED',message='模型特征源码校验失败',suggested_action='从已核验的备份恢复模型')
    spec=importlib.util.spec_from_file_location('quantlens_frozen_features',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build
