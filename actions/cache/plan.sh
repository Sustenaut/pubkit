#!/usr/bin/env bash
# Writes the cache keys and paths for pubkit.json's cache rules to
# $GITHUB_OUTPUT, from the copy of pubkit this action came with: cache.py
# needs only Python's standard library, so nothing is installed.
set -euo pipefail

case "${STEP:-}" in
  restore|save) ;;
  *) echo "cache: step is restore or save, not '${STEP:-}'" >&2; exit 2 ;;
esac
src="$(cd "$(dirname "$0")/../../src" && pwd)"
# -P keeps the workspace off the import path, so only this pubkit is run.
PYTHONPATH="$src" python3 -P -m pubkit.cache plan --github-output
