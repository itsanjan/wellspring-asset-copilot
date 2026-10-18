#!/usr/bin/env bash
# Build the database and the web UI, then start the server.
#
#   scripts/run.sh                 # sample dataset (data/sample-data)
#   scripts/run.sh --test          # organizers' test dataset (data/test-data/dataset)
#   scripts/run.sh --data DIR      # any dataset folder
#   PORT=9000 scripts/run.sh       # different port (default 8000)
set -euo pipefail
cd "$(dirname "$0")/.."

DATA=data/sample-data
DB=var/wellspring.db
case "${1:-}" in
  --test) DATA=data/test-data/dataset; DB=var/test.db ;;
  --data) DATA="$2"; DB=var/custom.db ;;
  "") ;;
  *) echo "usage: scripts/run.sh [--test | --data DIR]" >&2; exit 2 ;;
esac
PORT="${PORT:-8000}"

[ -x .venv/bin/python ] || { echo "No .venv: run  uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt" >&2; exit 1; }
[ -d "$DATA" ] || { echo "No dataset at $DATA (scripts/fetch_data.sh downloads the sample)" >&2; exit 1; }

mkdir -p var
echo "Loading $DATA into $DB"
.venv/bin/python -m wellspring.loader --data "$DATA" --db "$DB"

if [ ! -d web/dist ] || [ -n "$(find web/src -newer web/dist -print -quit)" ]; then
  echo "Building the web UI"
  (cd web && { [ -d node_modules ] || npm install; } && npm run build)
fi

echo "Starting on http://localhost:$PORT"
WELLSPRING_DB="$DB" exec .venv/bin/python -m uvicorn wellspring.server:app --port "$PORT"
