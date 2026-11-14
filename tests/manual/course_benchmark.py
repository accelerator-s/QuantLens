"""Record fresh-client Hive/MR and Spark/YARN timings on the same DWD partition."""
import json,re,subprocess,time
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2]
COMPOSE=['docker','compose','--env-file','docker/versions.env','-f','docker/compose.yaml']
QUERY='SELECT year, count(*) AS n, sum(volume) AS volume FROM dwd.prices_clean WHERE year=2025 GROUP BY year'
EVIDENCE=ROOT/'docs/course/evidence';EVIDENCE.mkdir(parents=True,exist_ok=True)

def timed(arguments,name):
    started=time.perf_counter()
    result=subprocess.run(COMPOSE+arguments,cwd=ROOT,text=True,capture_output=True,timeout=1800)
    elapsed=time.perf_counter()-started
    (EVIDENCE/(name+'.log')).write_text(result.stdout+'\n'+result.stderr)
    if result.returncode:raise RuntimeError(f'{name} failed: '+result.stderr[-1500:])
    return result.stdout,elapsed

sql='SET hive.fetch.task.conversion=none; SET hive.compute.query.using.stats=false; SET hive.exec.mode.local.auto=false; '+QUERY
hive,hive_seconds=timed(['exec','--interactive=false','--no-TTY=false','hive','beeline','-u','jdbc:hive2://localhost:10000','-n','root','--silent=true','--showHeader=false','--outputformat=tsv2','-e',sql],'hive-mr')
rows=[{'year':int(a),'n':int(b),'volume':int(c)} for a,b,c in re.findall(r'(?m)^(\d{4})\t(\d+)\t(\d+)\s*$',hive)]
spark,spark_seconds=timed(['exec','-T','spark-client','spark-submit','--master','yarn','--deploy-mode','client','/workspace/tests/manual/spark_benchmark.py'],'spark-sql')
record=json.loads(next(line.split('=',1)[1] for line in spark.splitlines() if line.startswith('QUANTLENS_BENCH=')))
assert rows and rows==record['rows'],(rows,record['rows'])
result={'recorded_at':datetime.now(timezone.utc).isoformat(),'query':QUERY,'rows':rows,'equal_results':True,'hive_mr_wall_seconds':hive_seconds,'spark_yarn_wall_seconds':spark_seconds,'spark_query_seconds':record['query_seconds'],'measurement':'Fresh command wall time includes client startup and YARN scheduling. Shared cluster and filesystem cache; no cold-cache claim.','spark':record}
(EVIDENCE/'mr-spark.json').write_text(json.dumps(result,indent=2,ensure_ascii=False))
print(json.dumps(result,ensure_ascii=False))
