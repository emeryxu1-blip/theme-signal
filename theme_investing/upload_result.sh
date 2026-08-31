#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
if [[ ! -f .env.upload ]]; then
  echo "error: .env.upload is missing" >&2
  exit 2
fi
set -a
. ./.env.upload
set +a

python3 -u upload_existing.py "${1:-result.json}"
