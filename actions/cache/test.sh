#!/usr/bin/env bash
# Runs plan.sh and prune.sh as the action does, in a scratch repository
# with a cache block, and checks the slots plan.sh writes to $GITHUB_OUTPUT.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"
cat > pubkit.json <<'JSON'
{"kind": "utility", "cache": {
  "data": {"path": "data", "refresh": "week"},
  "packs": {"path": ".packcache", "unit": "data/{unit}/", "inputs": ["build.py"]}}}
JSON
failures=0
expect() {
  if grep -qE -- "$2" "$GITHUB_OUTPUT"; then echo "ok: $1"; else
    echo "FAIL: $1: no line matching $2"; cat "$GITHUB_OUTPUT"; failures=$((failures + 1)); fi
}
export GITHUB_RUN_ID=7 GITHUB_RUN_ATTEMPT=2 PUBKIT_TODAY=2026-10-29
export GITHUB_OUTPUT="$work/out"
STEP=restore "$here/plan.sh" > /dev/null
expect "the fetched entry first, by name" '^path-1=data$'
expect "a key for this week's data" '^key-1=pubkit-cache-data-[0-9a-f]{16}-2026-W44-[0-9a-f]{16}$'
expect "the data restored exactly" '^prefix-1=$'
expect "a key for this run's packs" '^key-2=pubkit-cache-packs-[0-9a-f]{16}-7-2$'
expect "the packs restored from the latest run" '^prefix-2=pubkit-cache-packs-[0-9a-f]{16}-$'
expect "no third entry" '^key-3=$'
export GITHUB_OUTPUT="$work/fresh"
STEP=restore FRESH=true "$here/plan.sh" > /dev/null
expect "fresh: the data not restored" '^restore-1=false$'
expect "fresh: the packs still restored" '^restore-2=true$'
export GITHUB_OUTPUT="$work/refresh"
STEP=restore REFRESH=true "$here/plan.sh" > /dev/null
expect "refresh: the data not restored" '^restore-1=false$'
expect "refresh: the packs not restored" '^restore-2=false$'
if STEP=other "$here/plan.sh" > /dev/null 2>&1; then
  echo "FAIL: a step that is neither restore nor save is taken"; failures=$((failures + 1))
else
  echo "ok: a step that is neither restore nor save is refused"
fi
if "$here/prune.sh" > /dev/null; then echo "ok: prune runs where nothing was made"; else
  echo "FAIL: prune fails where nothing was made"; failures=$((failures + 1)); fi
exit "$failures"
