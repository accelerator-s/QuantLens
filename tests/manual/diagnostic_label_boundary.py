"""Spark probe: no warehouse writes; only synthetic in-memory relations."""
from datetime import date
from types import SimpleNamespace

from pyspark.sql import SparkSession

from factor_evolution.signal_diagnostics import extract_daily
from factor_evolution.diagnostics import signal_summary
import quantlens.signals

spark = SparkSession.builder.appName('diagnostic-label-boundary').getOrCreate()
spark.sparkContext.setLogLevel('ERROR')
try:
    dates = [date(2020, 1, 2), date(2020, 12, 31)]
    scores = spark.createDataFrame([(d, str(i), float(i)) for d in dates for i in range(10)],
                                    'date date, code string, score double')
    labels = spark.createDataFrame([
        (d, str(i), 1, 'v1', True, date(2020, 1, 6) if d == dates[0] else date(2021, 1, 5),
         i / 100 if d == dates[0] else 999.) for d in dates for i in range(10)],
        'date date, code string, horizon int, label_version string, valid boolean, valid_until date, ret double')
    universe = spark.createDataFrame([(d, str(i), True) for d in dates for i in range(10)],
                                     'date date, code string, in_universe boolean')
    proxy = SimpleNamespace(table=lambda name: {'dwd.label_forward': labels, 'dwd.universe': universe}[name])
    quantlens.signals.composite = lambda *args: (scores, {'fixture': True})
    daily, _ = extract_daily(proxy, None, '2020-01-01', '2020-12-31', [1])
    pdf = daily.toPandas()
    assert len(pdf) == 1 and str(pdf.iloc[0]['date']) == '2020-01-02'
    result = signal_summary(pdf, start='2020-01-01', end='2020-12-31', horizons=[1])
    assert abs(result['horizons'][0]['rank_ic_mean'] - 1) < 1e-9
    assert result['horizons'][0]['mean_coverage'] == 1
    print('DIAGNOSTIC_LABEL_BOUNDARY_PASS', flush=True)
finally:
    spark.stop()
