#!/usr/bin/env bash
# Writes each cache entry's path and key for pubrules.json's cache block to
# $GITHUB_OUTPUT (path-N, key-N, prefix-N, restore-N for N = 1..8), from
# the copy of pubrules this action came with: cache.py needs only Python's
# standard library, so nothing is installed.
set -euo pipefail

case "${STEP:-}" in
  restore|save) ;;
  *) echo "cache: step is restore or save, not '${STEP:-}'" >&2; exit 2 ;;
esac
args=(plan --github-output)
if [[ "${STEP}" == restore && "${FRESH:-false}" == true ]]; then
  args+=(--fresh)
fi
if [[ "${STEP}" == restore && "${REFRESH:-false}" == true ]]; then
  args+=(--refresh)
fi
src="$(cd "$(dirname "$0")/../../src" && pwd)"
# -P keeps the workspace off the import path, so only this pubrules is run.
PYTHONPATH="$src" python3 -P -m pubrules.cache "${args[@]}"
