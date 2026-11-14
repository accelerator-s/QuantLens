"""Explicit market membership and dated security-code events."""
from datetime import date
import re

A_SHARE_PATTERN = r'^(?:00\d{4}|30\d{4}|60\d{4}|68\d{4}|(?:43|83|87|88|92)\d{4})$'
CODE_CHANGES = ({'old':'000022','new':'001872','effective':date(2018,12,26),
    'announced':date(2018,12,14),
    'source':'https://static.cninfo.com.cn/finalpage/2018-12-21/1205681867.PDF'},)


def is_a_share(code):
    return bool(re.fullmatch(A_SHARE_PATTERN,str(code)))


def normalize_industry_identity(frame):
    """Map source industry records at the dated code change, without backfill."""
    from pyspark.sql import functions as F, Window
    for event in CODE_CHANGES:
        old=frame.where(F.col('code')==event['old'])
        before=old.where(F.col('effective_date')<F.lit(event['effective']))
        latest=Window.partitionBy('classification_id').orderBy(F.col('effective_date').desc())
        seed=(before.withColumn('_rank',F.row_number().over(latest)).where('_rank=1').drop('_rank')
              .withColumn('code',F.lit(event['new'])).withColumn('effective_date',F.lit(event['effective'])))
        # The feed sometimes retains the old code on later industry updates.
        frame=frame.withColumn('code',F.when((F.col('code')==event['old']) &
            (F.col('effective_date')>=F.lit(event['effective'])),event['new']).otherwise(F.col('code')))
        existing=frame.select('code','classification_id','effective_date')
        frame=frame.unionByName(seed.join(existing,['code','classification_id','effective_date'],'left_anti'))
    return frame


def normalize_style_identity(frame):
    """A renamed security retains its observable classification history.

    Quote-gap predictions for the old code already use the current year's
    centroids. Transfer those dated records to the successor until it has its
    own feature observations; this is a security-master join, not an unknown
    cluster or a different benchmark.
    """
    from pyspark.sql import functions as F
    for event in CODE_CHANGES:
        carried=frame.where((F.col('code')==event['old']) &
            (F.col('date')>=F.lit(event['effective']))).withColumn('code',F.lit(event['new']))
        frame=frame.unionByName(carried.join(frame.select('code','date'),['code','date'],'left_anti'))
    return frame
