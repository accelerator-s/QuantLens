#!/usr/bin/env bash
set -euo pipefail
if ! compgen -G '/hbase/zookeeper/version-2/snapshot.*' >/dev/null && \
   compgen -G '/hbase/zookeeper/version-2/log.*' >/dev/null; then
  # ZooKeeper 3.4 created valid transaction-only stores before the first
  # snapshot; replay those logs once when upgrading to the current runtime.
  export HBASE_ZOOKEEPER_OPTS="${HBASE_ZOOKEEPER_OPTS:-} -Dzookeeper.snapshot.trust.empty=true"
fi
hbase-daemon.sh start zookeeper
for i in {1..60}; do nc -z localhost 2181 && break; sleep 1; done
nc -z localhost 2181
hbase-daemon.sh start master
hbase-daemon.sh start regionserver
hbase rest start -p 16050 &
rest_pid=$!
ready=false
for i in {1..60}; do
  status=$(curl -sS --max-time 5 -o /dev/null -w '%{http_code}' http://localhost:16050/namespaces/ql || true)
  if [[ "$status" == 200 ]]; then ready=true; break; fi
  if [[ "$status" == 404 ]]; then
    curl -fsS --max-time 10 -X POST http://localhost:16050/namespaces/ql
    ready=true
    break
  fi
  sleep 2
done
if [[ "$ready" != true ]]; then echo 'HBase namespace initialization failed' >&2; exit 1; fi
wait "$rest_pid"
