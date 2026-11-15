"""Exercise production Spark screening with synthetic data, no warehouse writes."""
from datetime import date, timedelta

from pyspark.sql import SparkSession, functions as F

from factor_evolution.prediction import screen, spark_prediction


spark = SparkSession.builder.appName('strategy-prediction-screen').getOrCreate()
spark.sparkContext.setLogLevel('ERROR')
try:
    values = [(date(2020, 1, 1) + timedelta(days=d), float(i), i / 100)
              for d in range(65) for i in range(10)]
    frame = spark.createDataFrame(values, 'date date, score double, ret double')
    positive = spark_prediction(frame)
    assert positive['valid_ic_days'] == 65
    assert abs(positive['rank_ic_mean'] - 1) < 1e-9
    assert abs(positive['top_minus_bottom'] - .08) < 1e-9
    assert screen([positive])['passed']
    negative = spark_prediction(frame.withColumn('score', -F.col('score')))
    assert not screen([negative])['passed']
    constant = spark_prediction(frame.withColumn('score', F.lit(1.)))
    assert constant['valid_ic_days'] == 0
    assert constant['rank_ic_mean'] is None
    assert not screen([constant])['passed']
    print('STRATEGY_PREDICTION_SCREEN_PASS', flush=True)
finally:
    spark.stop()
