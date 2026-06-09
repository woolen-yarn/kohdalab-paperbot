#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/.."

git pull --ff-only
docker compose -f docker-compose.nas.yml up -d --build paperbot
docker compose -f docker-compose.nas.yml ps
