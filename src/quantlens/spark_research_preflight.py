"""Check training and validation inputs without opening the locked test."""
import argparse
import json

from quantlens.research_preflight import check_warehouse_style
from quantlens.warehouse import job


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    args = parser.parse_args()
    with job('research_preflight') as (spark, context):
        result = check_warehouse_style(spark, args.start, args.end)
        print('QUANTLENS_RESEARCH_PREFLIGHT=' + json.dumps({**result, **context}), flush=True)
