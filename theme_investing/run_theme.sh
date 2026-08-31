#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
set -a
. ./.env.upload
set +a

echo "[theme-runner] progress is streaming below and being saved to run.log" >&2
if python3 -u cli.py --target local --input my_input.json "$@" \
    > result.json 2> >(tee run.log >&2); then
  echo "[theme-runner] complete: result.json" >&2
else
  status=$?
  echo "[theme-runner] failed with status $status; inspect run.log" >&2
  exit "$status"
fi
