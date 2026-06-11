#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/.."

./scripts/run_paper_watch.sh --mode collect "$@"

rag_after_collect="${PAPER_WATCH_RAG_AFTER_COLLECT:-}"
if [ -z "$rag_after_collect" ] && [ -f .env ]; then
  rag_after_collect="$(
    awk -F= '/^PAPER_WATCH_RAG_AFTER_COLLECT=/ {print $2; exit}' .env \
      | tr -d '"'\''[:space:]'
  )"
fi

case "${rag_after_collect:-true}" in
  1|true|TRUE|yes|YES|on|ON)
    COMPOSE_RUN_BUILD=0 ./scripts/run_paper_watch.sh --mode rag "$@"
    ;;
esac
