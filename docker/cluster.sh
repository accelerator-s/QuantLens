#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
compose=(docker compose --env-file docker/versions.env -f docker/compose.yaml)
case ${1:-status} in
  build) "${compose[@]}" --profile build build base; "${compose[@]}" build nn-rm; "${compose[@]}" build hive hbase spark-client ;;
  up) mkdir -p data/landing data/ads; "${compose[@]}" up -d --wait ;;
  down) "${compose[@]}" down ;;
  status) "${compose[@]}" ps --all ;;
  *) "${compose[@]}" "$@" ;;
esac
