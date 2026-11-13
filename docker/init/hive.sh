#!/usr/bin/env bash
set -euo pipefail
export HADOOP_CLASSPATH="${HADOOP_CLASSPATH:-}:/opt/hive/lib/*:/opt/hbase/lib/*"
schematool -dbType postgres -info || schematool -dbType postgres -initSchema
hive --service metastore &
for i in {1..120}; do nc -z localhost 9083 && break; sleep 2; done
hive --service hiveserver2 &
wait -n
