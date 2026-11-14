#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
compose=(docker compose --env-file docker/versions.env -f docker/compose.yaml)
export QUANTLENS_UID="$(id -u)" QUANTLENS_GID="$(id -g)"
case ${1:-status} in
  build) "${compose[@]}" --profile build build base netty-build; "${compose[@]}" build nn-rm; "${compose[@]}" build hive hbase spark-client ;;
  up) mkdir -p data/landing data/ads; PYTHONPATH=src python3 -m storage.cluster_access; "${compose[@]}" up -d --wait ;;
  down) "${compose[@]}" down ;;
  status) "${compose[@]}" ps --all ;;
  *) "${compose[@]}" "$@" ;;
esac
