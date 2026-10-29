#!/usr/bin/env bash
# Runs plan.sh as the action does, in a scratch repository with cache
# rules, and checks the keys and paths it writes to $GITHUB_OUTPUT.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"
cat > pubkit.json <<'JSON'
{"kind": "utility", "cache": {
  "inputs": {"data": {"path": "data", "refresh": "week"}},
  "outputs": {"packs": {"dir": ".packcache", "unit": ["data/{area}/"]}}}}
JSON
failures=0
expect() {
  if grep -qE -- "$2" "$GITHUB_OUTPUT"; then echo "ok: $1"; else
    echo "FAIL: $1: no line matching $2"; cat "$GITHUB_OUTPUT"; failures=$((failures + 1)); fi
}
export GITHUB_OUTPUT="$work/out" GITHUB_RUN_ID=7 GITHUB_RUN_ATTEMPT=2 PUBKIT_TODAY=2026-10-29
STEP=restore "$here/plan.sh" > /dev/null
expect "the inputs' path" '^data$'
expect "a key for this week's inputs" '^pubkit-inputs-[0-9a-f]{16}$'
expect "a key for this run's outputs" '^pubkit-outputs-[0-9a-f]{16}-7-2$'
if STEP=other "$here/plan.sh" > /dev/null 2>&1; then
  echo "FAIL: a step that is neither restore nor save is taken"; failures=$((failures + 1))
else
  echo "ok: a step that is neither restore nor save is refused"
fi
exit "$failures"
