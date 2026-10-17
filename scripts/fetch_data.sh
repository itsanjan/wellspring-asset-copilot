#!/usr/bin/env bash
# Fetch the organizer's sample dataset into data/sample-data (not committed to this repo).
# Usage: scripts/fetch_data.sh
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"
git clone --depth 1 https://github.com/FlytBaseAILabs/flytbase-ahc-swe-asset-intelligence-dataset.git "$tmp/ds"
rm -rf data/sample-data
cp -R "$tmp/ds/sample-data" data/sample-data
rm -rf "$tmp"
echo "Sample data ready in data/sample-data"
