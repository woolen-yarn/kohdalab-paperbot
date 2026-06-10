#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/.."

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.nas.yml}"
LOG_FILE="${PAPER_WATCH_LOG_FILE:-logs/paper_watch.log}"

mkdir -p logs rag_poc/index

{
  date '+===== %Y-%m-%d %H:%M:%S paper-watch start ====='
  docker compose -f "$COMPOSE_FILE" run --rm paper-watch "$@"
  date '+===== %Y-%m-%d %H:%M:%S paper-watch end ====='
} 2>&1 | tee -a "$LOG_FILE"
