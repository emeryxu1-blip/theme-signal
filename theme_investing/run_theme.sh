#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
set -a
. ./.env.upload
set +a

echo "[theme-runner] progress is streaming below and being saved to run.log" >&2
result_tmp="$(mktemp "./result.json.tmp.XXXXXX")"
cleanup() {
  if [[ -n "${result_tmp:-}" && -f "$result_tmp" ]]; then
    rm -f -- "$result_tmp"
  fi
}
trap cleanup EXIT

if python3 -u cli.py --target local --input my_input.json "$@" \
    > "$result_tmp" 2> >(tee run.log >&2); then
  if [[ ! -s "$result_tmp" ]]; then
    echo "[theme-runner] failed: CLI returned empty JSON output; prior result.json preserved" >&2
    exit 5
  fi
  mv -f -- "$result_tmp" result.json
  result_tmp=""
  echo "[theme-runner] complete: result.json" >&2
else
  status=$?
  echo "[theme-runner] failed with status $status; inspect run.log" >&2
  exit "$status"
fi
