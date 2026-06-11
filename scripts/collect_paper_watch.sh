#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/.."

exec ./scripts/run_paper_watch.sh --mode collect "$@"
