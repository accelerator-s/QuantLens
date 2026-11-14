import os
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


@pytest.fixture(scope='module')
def spark(tmp_path_factory):
    from unittest.mock import patch
    from quantlens.warehouse import session
    home = tmp_path_factory.mktemp('style-test')
    with patch.dict(os.environ, QUANTLENS_HOME=str(home), QUANTLENS_COMPUTE_LOCAL='1',
                    SPARK_LOCAL_IP='127.0.0.1', PYSPARK_PYTHON=sys.executable,
                    QUANTLENS_TRAIN_DEVICE='cpu'):
        value = session('market-style-contract')
        value.sparkContext.setLogLevel('ERROR')
        yield value
        value.stop()


def prices(spark, future=False):
    rows = []
    for day in range(90 + (1 if future else 0)):
        for stock in range(12):
            # No financial/factor inputs; stock 0 lacks market cap as well.
            cap = None if stock == 0 else float((stock + 1) * 100 + day)
            amount = 10_000_000. + stock * 1_000_000 + day * 1000
            adj = 10. + stock + day * .02
            if day == 90:
                cap, amount, adj = 1e20, 1e20, 1e20
            rows.append((f'{stock:06d}', date(2020, 1, 1) + timedelta(days=day),
                         adj, .001 * ((day + stock) % 7 - 3), amount, cap, 500, adj))
    return spark.createDataFrame(rows, 'code string,date date,adj_close double,ret_wd double,'
                                 'amount double,total_mv double,listed_days int,close double')


def test_classifies_without_profitable_company_or_financial_screen(spark):
    from quantlens.style_features import market_style_features, STYLE_FEATURES
    frame = market_style_features(prices(spark)).toPandas()
    assert len(frame) == 90 * 12
    assert frame.code.nunique() == 12
    assert np.isfinite(frame[STYLE_FEATURES].to_numpy()).all()
    assert frame[STYLE_FEATURES].min().min() >= 0
    assert frame[STYLE_FEATURES].max().max() <= 1
    assert (frame.loc[frame.code == '000000', 'size_rank'] == .5).all()
    assert frame._observed_features.min() >= 1


def test_market_style_history_is_unchanged_when_future_prices_are_appended(spark):
    from quantlens.style_features import market_style_features, STYLE_FEATURES
    import pandas as pd
    a = market_style_features(prices(spark)).toPandas().sort_values(['date', 'code']).reset_index(drop=True)
    b = market_style_features(prices(spark, future=True)).where("date<'2020-03-31'")
    b = b.toPandas().sort_values(['date', 'code']).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-12, atol=1e-12)


def test_market_source_bounds_before_computing_daily_styles(spark):
    from quantlens.style_features import market_source
    source = SimpleNamespace(table=lambda _: prices(spark, future=True))
    result = market_source(source, start='2020-01-01', end='2020-03-30')
    assert result.agg({'date': 'max'}).first()[0] == date(2020, 3, 30)
    assert result.count() == 90 * 12


def test_default_kmeans_publishes_market_method_with_past_only_training(spark):
    from unittest.mock import patch
    from pyspark.sql import functions as F
    from quantlens import spark_kmeans
    from quantlens.style_features import market_style_features, STYLE_METHOD_VERSION
    source = prices(spark)
    history = source
    for years in (1, 2, 3):
        history = history.unionByName(source.withColumn('date', F.add_months('date', 12 * years)))
    features = market_style_features(history)
    output = {}
    def capture(_spark, frame, table, partitions, *, run):
        output.update(table=table, rows=frame.collect())
    with patch('quantlens.style_features.market_source', return_value=features), \
            patch('quantlens.spark_kmeans.publish', side_effect=capture), \
            patch('quantlens.warehouse.write_json') as metadata, \
            patch('quantlens.gpu.use_gpu', return_value=False):
        spark_kmeans.run(spark, {'run_id': 'style-integration', 'data_snapshot': 'fixture', 'version': 'v1'},
                         start='2020-01-01', end='2023-12-31')
    assert output['table'] == 'dws.style_cluster'
    assert len(output['rows']) == 90 * 12
    assert all(row.date.year == 2023 and row.train_end == '2022-12-31'
               and row.version == STYLE_METHOD_VERSION for row in output['rows'])
    assert metadata.call_args.args[2]['method_version'] == STYLE_METHOD_VERSION


def test_prediction_carry_is_causal_across_quote_gaps_and_year_boundary(spark):
    import pandas as pd
    from quantlens.style_features import carry_prediction_features, STYLE_FEATURES
    rows = [
        ('b', date(2022, 12, 28), .2, .3, .4, .5, 4),
        ('a', date(2022, 12, 29), .1, .2, .3, .4, 4),
        ('b', date(2022, 12, 30), .3, .4, .5, .6, 4),
        ('b', date(2023, 1, 2), .4, .5, .6, .7, 4),
        ('a', date(2023, 1, 3), .9, .9, .9, .9, 4),
    ]
    observed = spark.createDataFrame(rows, 'code string,date date,' +
        ','.join(name + ' double' for name in STYLE_FEATURES) + ',_observed_features int')
    full = carry_prediction_features(observed).toPandas().sort_values(['date', 'code']).reset_index(drop=True)
    earlier = carry_prediction_features(observed.where("date<'2023-01-03'"))
    earlier = earlier.toPandas().sort_values(['date', 'code']).reset_index(drop=True)
    pd.testing.assert_frame_equal(earlier, full.loc[full.date < date(2023, 1, 3)].reset_index(drop=True))
    carried = full.loc[(full.code == 'a') & (full.date == date(2023, 1, 2))].iloc[0]
    assert carried.feature_asof == date(2022, 12, 29) and carried.size_rank == .1
    assert (full.feature_asof <= full.date).all()
    assert not ((full.code == 'a') & (full.date < date(2022, 12, 29))).any()


def test_security_code_change_transfers_only_visible_industry_and_style(spark):
    from quantlens.security_master import normalize_industry_identity, normalize_style_identity
    source=spark.createDataFrame([
        ('000022',date(2012,10,26),'P0221','G55','port'),
        ('000022',date(2018,12,26),'P0221','G55','port'),
        ('000022',date(2020,1,1),'P0221','G56','transport'),
    ],'code string,effective_date date,classification_id string,industry_code string,industry_name string')
    result=normalize_industry_identity(source).orderBy('effective_date').collect()
    assert [(r.code,r.industry_code) for r in result] == [('000022','G55'),('001872','G55'),('001872','G56')]
    styles=spark.createDataFrame([
        ('000022',date(2018,12,25),1),('000022',date(2018,12,26),2),
        ('000022',date(2019,6,1),3),('001872',date(2019,6,1),4),
    ],'code string,date date,cluster_id int')
    rows=normalize_style_identity(styles).where("code='001872'").orderBy('date').collect()
    assert [(r.date,r.cluster_id) for r in rows] == [(date(2018,12,26),2),(date(2019,6,1),4)]
