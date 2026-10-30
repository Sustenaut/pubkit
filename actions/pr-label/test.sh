#!/usr/bin/env bash
# Runs label.sh as the action does, for each kind of event, against a stub
# curl that answers GitHub's API from files, and checks what it writes to
# $GITHUB_OUTPUT.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir "$work/bin" "$work/api"
# The stub answers a request for .../repos/o/r/<what> with api/<what>, its
# slashes made dashes, and fails as curl -f does when there is no such file.
cat > "$work/bin/curl" <<'SH'
#!/usr/bin/env bash
url="${*: -1}"
file="$API_DIR/$(sed -E 's#.*/repos/o/r/##; s#/#-#g' <<< "$url")"
[[ -f "$file" ]] || { echo "curl: (22) 404 $url" >&2; exit 22; }
cat "$file"
SH
chmod +x "$work/bin/curl"
export PATH="$work/bin:$PATH" API_DIR="$work/api" GITHUB_REPOSITORY=o/r GH_TOKEN=t GITHUB_SHA=abc123
echo '[{"merged_at": "2026-10-30T07:00:00Z", "labels": [{"name": "Refresh Cache"}]}]' > "$work/api/commits-abc123-pulls"
echo '[{"merged_at": "2026-10-30T07:00:00Z", "labels": [{"name": "docs"}]}]' > "$work/api/commits-def456-pulls"
echo '{"labels": [{"name": "refresh cache"}]}' > "$work/api/pulls-15"
echo '{"pull_request": {"labels": [{"name": "bug"}, {"name": "refresh cache"}]}}' > "$work/pr.json"
echo '{"pull_request": {"labels": []}}' > "$work/bare.json"
echo '{"merge_group": {"head_ref": "refs/heads/gh-readonly-queue/main/pr-15-0123abcd"}}' > "$work/queue.json"
failures=0
# check NAME WANT EVENT [EVENT_FILE] [VAR=VALUE...]: label.sh writes present=WANT.
check() {
  local name="$1" want="$2" event="$3" file="${4:-$work/pr.json}"
  shift 4 || shift $#
  : > "$work/out"
  if ! env GITHUB_EVENT_NAME="$event" GITHUB_EVENT_PATH="$file" GITHUB_OUTPUT="$work/out" LABEL="refresh cache" "$@" \
      "$here/label.sh" > /dev/null; then
    echo "FAIL: $name: label.sh failed"; failures=$((failures + 1)); return
  fi
  if grep -qx "present=$want" "$work/out"; then echo "ok: $name"; else
    echo "FAIL: $name: wanted present=$want, got $(cat "$work/out")"; failures=$((failures + 1)); fi
}
check "a pull request with the label" true pull_request "$work/pr.json"
check "a pull request without it" false pull_request "$work/bare.json"
check "a merge queue group, by its pull request" true merge_group "$work/queue.json"
check "a push of a merged pull request with the label, in another case" true push "$work/pr.json"
check "a push of one without it" false push "$work/pr.json" GITHUB_SHA=def456
check "a scheduled run has no pull request" false schedule "$work/pr.json"
check "another label asked for" true pull_request "$work/pr.json" LABEL=bug
if env GITHUB_EVENT_NAME=push GITHUB_EVENT_PATH="$work/pr.json" GITHUB_OUTPUT="$work/out" LABEL="refresh cache" \
    GITHUB_SHA=missing "$here/label.sh" > /dev/null 2>&1; then
  echo "FAIL: a pull request list that cannot be read passes"; failures=$((failures + 1))
else
  echo "ok: a pull request list that cannot be read fails the step"
fi
exit "$failures"
