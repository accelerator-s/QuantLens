#!/usr/bin/env bash
set -euo pipefail
if [[ ${QL_ROLE:-} == namenode ]]; then
  test -f /hadoop/dfs/name/current/VERSION || hdfs namenode -format -nonInteractive
  namenode_args=()
  layout=$(sed -n 's/^layoutVersion=//p' /hadoop/dfs/name/current/VERSION)
  if [[ $layout =~ ^-[0-9]+$ ]] && (( layout > -67 )); then
    namenode_args=(-upgrade)
  fi
  hdfs namenode "${namenode_args[@]}" &
  yarn resourcemanager &
  for i in {1..120}; do
    if hdfs dfsadmin -safemode wait; then break; fi
    sleep 2
  done
  hdfs dfs -mkdir -p /quantlens/{ods,dwd,dws,ads,meta/schema,meta/quality,meta/runs,archive/ads} /tmp /user/hive/warehouse /spark-events
  hdfs dfs -chmod 1777 /tmp
  hdfs dfs -chmod -R 777 /user/hive /spark-events /quantlens
else
  hdfs datanode &
  yarn nodemanager &
fi
wait -n
