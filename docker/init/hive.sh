#!/usr/bin/env bash
set -euo pipefail
export HADOOP_CLASSPATH="${HADOOP_CLASSPATH:-}:/opt/hive/lib/*:/opt/hbase/lib/*"
export HIVE_AUX_JARS_PATH=/opt/hbase/lib:/opt/hive/lib/commons-collections-3.2.2.jar
export HIVESERVER2_PID_DIR
HIVESERVER2_PID_DIR=$(mktemp -d /tmp/quantlens-hs2.XXXXXXXX)
trap 'rm -rf "$HIVESERVER2_PID_DIR"' EXIT
if schematool -dbType postgres -info; then
  schematool -dbType postgres -upgradeSchema
else
  schematool -dbType postgres -initSchema
fi
hive --service metastore &
for i in {1..120}; do nc -z localhost 9083 && break; sleep 2; done
hive --service hiveserver2 &
wait -n
