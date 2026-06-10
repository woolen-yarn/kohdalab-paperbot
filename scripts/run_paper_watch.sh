#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/.."

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.nas.yml}"
LOG_FILE="${PAPER_WATCH_LOG_FILE:-logs/paper_watch.log}"
RUN_FLAGS="--rm"

if [ "${COMPOSE_RUN_BUILD:-1}" = "1" ]; then
  RUN_FLAGS="--build $RUN_FLAGS"
fi

mkdir -p logs rag_poc/index

{
  date '+===== %Y-%m-%d %H:%M:%S paper-watch start ====='
  docker compose -f "$COMPOSE_FILE" run $RUN_FLAGS paper-watch python -m rag_poc.paper_watch "$@"
  date '+===== %Y-%m-%d %H:%M:%S paper-watch end ====='
} 2>&1 | tee -a "$LOG_FILE"
