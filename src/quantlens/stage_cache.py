"""Reuse completed warehouse stages only while inputs and published outputs match."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import unquote

from paths import workspace


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     default=str).encode()).hexdigest()


def configuration():
    from factors.registry import FactorRegistry
    fields = ('name', 'version', 'parameters', 'dependencies', 'eligibility',
              'standardization', 'missing_handling', 'direction', 'min_history',
              'window_days', 'engine', 'plugin_path', 'scope', 'output_frequency')
    factors = [{key: meta.model_dump(mode='json').get(key) for key in fields}
               for meta in FactorRegistry().list()]
    root = workspace().root
    files = (sorted((root / 'config').rglob('*.yaml'))
             + sorted((root / 'factors').rglob('*.py'))
             + sorted(root.glob('requirements*.txt')))
    return digest({'factors': factors, 'files': {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files if path != root / 'config/factors.yaml'}})


def table(name, *, factors=None, where=None, content=False, year_min=None, year_max=None):
    return {'table': name, 'factors': factors, 'where': where, 'content': content,
            'year_min': year_min, 'year_max': year_max}


def cleaning_code(stage):
    """Cleaning does not depend on models, portfolios or unrelated web routes."""
    module = 'spark_dwd_prices' if stage == 'dwd_prices_year' else 'spark_' + stage
    names = {'stage_cache', 'warehouse', 'spark_dwd_prices', 'spark_quality', module}
    if stage in {'dwd_fundamentals', 'dwd_analyst'}:
        names.add('spark_derived')
    directory = Path(__file__).parent
    return digest({name: hashlib.sha256((directory / (name + '.py')).read_bytes()).hexdigest()
                   for name in sorted(names)})


def specification(stage, parameters):
    if stage == 'dwd_prices_year':
        year = int(parameters['year'])
        # Earlier dates determine the trading calendar and first listing day.
        # A historical correction invalidates its year and every following year.
        return ([table('ods.' + name, year_max=year) for name in ('prices_daily', 'returns_daily')],
                [table('dwd.' + name, year_min=year, year_max=year) for name in ('prices_clean', 'universe')])
    from factors.registry import FactorRegistry
    raw = [meta.name for meta in FactorRegistry().list() if meta.name != 'model_gbt']
    dwd = [table('dwd.' + name) for name in
           ('prices_clean', 'universe', 'fundamentals_pit', 'analyst_pit', 'industry_pit')]
    values = table('dws.factor_values', factors=raw)
    labels = table('dwd.label_forward')
    training = [values, labels, table('dwd.universe')]
    ods = {
        'dwd_prices': (['prices_daily', 'returns_daily'], ['prices_clean', 'universe']),
        'dwd_fundamentals': (['fs_balance', 'fs_income', 'fs_cashflow', 'top_float_shareholders'], ['fundamentals_pit']),
        'dwd_analyst': (['analyst_forecast'], ['analyst_pit']),
        'dwd_industry': (['industry_classification'], ['industry_pit']),
    }
    if stage in ods:
        sources, targets = ods[stage]
        return [table('ods.' + name) for name in sources], [table('dwd.' + name) for name in targets]
    if stage == 'dwd_label':
        return dwd[:2], [labels]
    if stage == 'factor_values':
        return dwd, [values]
    if stage in {'factor_eval', 'model_eval'}:
        model = stage == 'model_eval'
        clause = "factor = 'model_gbt'" if model else "factor <> 'model_gbt'"
        correlations = "factor_a = 'model_gbt' OR factor_b = 'model_gbt'" if model else "factor_a <> 'model_gbt' AND factor_b <> 'model_gbt'"
        inputs = dwd + [labels, table('dws.factor_values') if model else values]
        outputs = [table('dws.factor_eval_daily', factors=['model_gbt'] if model else raw),
                   table('dws.factor_eval', where=clause, content=True),
                   table('ads.factor_eval', where=clause, content=True),
                   table('dws.factor_corr', where=correlations, content=True)]
        return inputs, outputs
    if stage == 'weight_fit':
        return training, [table('dws.factor_weights'), table('ads.weighting_compare', content=True)]
    if stage == 'kmeans':
        return [values], [table('dws.style_cluster')]
    if stage == 'fpgrowth':
        return [values], [table('dws.factor_itemset')]
    if stage == 'pca':
        return training, [table('dws.pca_scores'), table('dws.pca_loadings'),
                          table('ads.model_comparison', where="experiment = 'pca'", content=True)]
    if stage == 'engine':
        return training, [table('dws.factor_values', factors=['model_gbt']),
                          table('ads.model_comparison', where="experiment = 'gbt'", content=True)]
    return None


class StageCache:
    def __init__(self, spark, context):
        self.spark = spark
        self.context = context
        self.enabled = bool(context.get('pipeline_code_hash') and
                            context.get('data_snapshot') not in (None, '', 'unpublished'))
        self._configuration = configuration() if self.enabled else None
        self._file_status = {}

    def fingerprint(self, spec):
        name = spec['table']
        if not self.spark.catalog.tableExists(name):
            return None
        self.spark.catalog.refreshTable(name)
        frame = self.spark.table(name)
        schema = frame.schema.json()
        if spec['content']:
            from pyspark.sql import functions as F
            if spec['where']:
                frame = frame.where(spec['where'])
            # Shared small reports may be republished alongside another experiment.
            # Compare their rows, rather than invalidating both experiments' caches.
            columns = sorted(set(frame.columns) - {'run_id', 'data_snapshot', 'as_of'})
            hashed = frame.select(F.sha2(F.to_json(F.struct(*[F.col(c) for c in columns])), 256).alias('hash'))
            hashes = sorted(row.hash for row in hashed.collect())
            return {'schema': schema, 'rows': len(hashes), 'hash': digest(hashes)}
        files = []
        for uri in sorted(frame.inputFiles()):
            parts = {part.split('=', 1)[0]: unquote(part.split('=', 1)[1])
                     for part in uri.split('/') if '=' in part}
            if spec.get('year_min') is not None or spec.get('year_max') is not None:
                try:
                    year = int(parts['year'])
                except (KeyError, ValueError):
                    return None
                if spec.get('year_min') is not None and year < spec['year_min']:
                    continue
                if spec.get('year_max') is not None and year > spec['year_max']:
                    continue
            if spec['factors'] is not None:
                if parts.get('factor') not in spec['factors']:
                    continue
            if uri not in self._file_status:
                path = self.spark._jvm.org.apache.hadoop.fs.Path(uri)
                fs = path.getFileSystem(self.spark._jsc.hadoopConfiguration())
                if not fs.exists(path):
                    return None
                status = fs.getFileStatus(path)
                self._file_status[uri] = (status.getLen(), status.getModificationTime())
            files.append((uri, *self._file_status[uri]))
        return {'schema': schema, 'files': files} if files else None

    def fingerprints(self, specs):
        return [self.fingerprint(spec) for spec in specs]

    def run(self, stage, action, **parameters):
        spec = specification(stage, parameters) if self.enabled else None
        if spec is None:
            action()
            return False
        from quantlens.gpu import atomic_json, shared_root, use_gpu, worker_status
        inputs, outputs = spec
        source = self.fingerprints(inputs)
        worker = worker_status()
        runtime = {'spark': self.spark.version if isinstance(self.spark.version, str) else '',
                   'torch': worker.get('torch'), 'device_name': worker.get('name')}
        cleaning = stage.startswith('dwd_')
        identity = {'format': 2 if stage.startswith('dwd_') else 1, 'stage': stage, 'parameters': parameters,
                    'code': cleaning_code(stage) if cleaning else self.context['pipeline_code_hash'],
                    'config': None if cleaning else self._configuration,
                    'device': 'cpu' if cleaning else ('cuda' if use_gpu() else 'cpu'),
                    'runtime': {'spark': runtime['spark']} if cleaning else runtime, 'inputs': source}
        # ODS file identities already describe every input to cleaning. A change
        # in an unrelated table must not invalidate unchanged source tables.
        if not stage.startswith('dwd_'):
            identity['data_snapshot'] = self.context['data_snapshot']
        key = digest(identity)
        directory = shared_root() / 'stage_cache'
        path = directory / (key + '.json')
        try:
            saved = json.loads(path.read_text())
        except (OSError, ValueError):
            saved = None
        if (isinstance(saved, dict) and saved.get('key') == key
                and isinstance(saved.get('run_id'), str)
                and all(item is not None for item in source)):
            current = self.fingerprints(outputs)
            if all(item is not None for item in current) and digest(current) == saved.get('outputs'):
                print('QUANTLENS_CACHE_HIT=' + json.dumps({'stage': stage,
                      'source_run_id': saved['run_id']}, ensure_ascii=False), flush=True)
                return True
        # A failed or interrupted stage never receives a completion record.
        action()
        current = self.fingerprints(outputs)
        if all(item is not None for item in source + current):
            directory.mkdir(parents=True, exist_ok=True)
            if os.geteuid() == 0:
                owner = shared_root().stat()
                os.chown(directory, owner.st_uid, owner.st_gid)
            atomic_json(path, {'key': key, 'identity': identity,
                               'outputs': digest(current), 'run_id': self.context['run_id']})
            if os.geteuid() == 0:
                os.chown(path, owner.st_uid, owner.st_gid)
        return False
