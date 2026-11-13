"""Export measured warehouse contract checks as a small JSON record."""
import json
from quantlens.warehouse import job
with job('warehouse_evidence') as (spark,context):
    result={'tables':{},'checks':{}}
    for name in ['dwd.prices_clean','dwd.universe','dwd.fundamentals_pit','dwd.analyst_pit','dwd.industry_pit','dwd.label_forward','dwd.quality_report','dws.factor_values','dws.factor_eval','dws.factor_weights','ads.portfolio_target']:
        if not spark.catalog.tableExists(name):continue
        frame=spark.table(name)
        result['tables'][name]={'rows':frame.count(),'columns':frame.columns}
    if spark.catalog.tableExists('dwd.label_forward'):
        result['checks']['labels']=[row.asDict() for row in spark.sql('SELECT horizon,count(*) AS rows,sum(CASE WHEN valid THEN 1 ELSE 0 END) AS valid_rows FROM dwd.label_forward GROUP BY horizon ORDER BY horizon').collect()]
        result['checks']['mature_universe_label_coverage']=[row.asDict() for row in spark.sql('SELECT l.horizon,avg(CASE WHEN l.valid THEN 1.0 ELSE 0.0 END) AS coverage FROM dwd.label_forward l JOIN dwd.universe u ON l.code=u.code AND l.date=u.date WHERE u.in_universe AND l.valid_until IS NOT NULL GROUP BY l.horizon ORDER BY l.horizon').collect()]
    print('QUANTLENS_WAREHOUSE='+json.dumps({**result,**context},default=str),flush=True)
