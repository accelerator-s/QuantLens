#!/usr/bin/env bash
set -euo pipefail
hbase-daemon.sh start zookeeper
hbase-daemon.sh start master
hbase-daemon.sh start regionserver
for i in {1..60}; do
  if echo "list_namespace" | hbase shell -n > /tmp/namespaces 2>&1; then break; fi
  sleep 2
done
if ! grep -qx ql /tmp/namespaces; then echo "create_namespace 'ql'" | hbase shell -n; fi
hbase rest start -p 16050 &
wait -n
