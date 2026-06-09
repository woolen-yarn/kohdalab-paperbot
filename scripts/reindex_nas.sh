#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/.."

docker compose -f docker-compose.nas.yml run --rm ingest
docker compose -f docker-compose.nas.yml restart paperbot
docker compose -f docker-compose.nas.yml ps
