from quantlens import ads
from storage.hbase import HBase
from exceptions import ContractViolation


def target_holdings(strategy_id):
    rows=ads.query('portfolio_target','strategy_id=?',[strategy_id],limit=1000,order='as_of DESC,weight DESC')
    if not rows:return {'items':[],'total_rows':0,**ads.provenance('portfolio_target')}
    as_of=rows[0]['as_of'];rows=[r for r in rows if r['as_of']==as_of]
    client=HBase(timeout=.2)
    try:
        snapshots=client.scan('ql:holding_snapshot',strategy_id+'|'+str(as_of)+'|',1000)
        if snapshots:
            by_code={r['m:code']:r for r in snapshots}
            if all(r['code'] in by_code and by_code[r['code']].get('m:run_id')==r['run_id'] and by_code[r['code']].get('m:data_snapshot')==r['data_snapshot'] for r in rows):
                return {'items':[{**row,'weight':float(by_code[row['code']]['h:weight'])} for row in rows],'total_rows':len(rows),**{**ads.provenance('portfolio_target'),'layer':'hbase','source_uri':'hbase://ql:holding_snapshot'}}
    except (ContractViolation, OSError):
        return {'items':rows,'total_rows':len(rows),'point_lookup_status':'unavailable',**ads.provenance('portfolio_target')}
    finally:client.close()
    return {'items':rows,'total_rows':len(rows),'point_lookup_status':'not_published',**ads.provenance('portfolio_target')}
