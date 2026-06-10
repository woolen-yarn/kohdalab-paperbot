#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/.."

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.nas.yml}"

if [ "${COMPOSE_RUN_BUILD:-1}" = "1" ]; then
  echo "==> Build runtime image"
  docker compose -f "$COMPOSE_FILE" build --quiet paper-watch
fi

docker compose -f "$COMPOSE_FILE" run --rm paper-watch \
  python scripts/cleanup_slack_channel.py "$@"
