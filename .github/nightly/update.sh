#!/usr/bin/env bash
# One governed repository's nightly pull request; run in its clone by the
# nightly workflow. See .github/workflows/nightly.yml.
#
# Env: REPO (owner/name), PUBREL and PUB (their latest releases), GH_TOKEN
# (may push and open pull requests there), RUN_URL, and optionally
# SLACK_TOKEN and SLACK_USER (the Sustenaut bot's token, and whom it DMs).
set -euo pipefail

branch=pubkit-nightly
body=$(mktemp)
summary=${GITHUB_STEP_SUMMARY:-/dev/null}

notify() {
  if [[ -z "${SLACK_TOKEN:-}" ]] || [[ -z "${SLACK_USER:-}" ]]; then
    echo "No Slack secrets: not sending \"$1\""; return 0
  fi
  curl -sS -X POST https://slack.com/api/chat.postMessage \
    -H "Authorization: Bearer $SLACK_TOKEN" -H 'Content-Type: application/json; charset=utf-8' \
    --data "$(jq -n --arg c "$SLACK_USER" --arg t "$1" '{channel: $c, text: $t}')" \
    | jq -e .ok >/dev/null || echo "::warning::the Slack DM failed: $1"
}

# Every workflow run on commit $1 finished, and each succeeded or was
# skipped. Sets $failed to the failing runs' links.
wait_ci() {
  local runs i
  failed=""
  sleep 60
  for i in $(seq 330); do
    runs=$(gh run list --repo "$REPO" --commit "$1" --json status,conclusion,url)
    if [[ "$(jq length <<<"$runs")" -gt 0 ]] \
        && [[ "$(jq '[.[] | select(.status != "completed")] | length' <<<"$runs")" = 0 ]]; then
      failed=$(jq -r '[.[] | select(.conclusion | IN("success", "skipped", "neutral") | not) | .url] | join(" ")' <<<"$runs")
      [[ -z "$failed" ]]; return
    fi
    sleep 60
  done
  failed="CI did not finish within 5.5 hours"
  return 1
}

git checkout -q -B "$branch" origin/main
made=$(pubkit nightly --pubrel "$PUBREL" --pub "$PUB" --body "$body" | tail -n 1)
pr=$(gh pr list --repo "$REPO" --head "$branch" --state open --json number --jq '.[0].number // empty')

if [[ -z "$made" ]]; then
  echo "$REPO: nothing to update." >> "$summary"
  if [[ -n "$pr" ]]; then
    gh pr close "$pr" --repo "$REPO" --delete-branch \
      --comment "Closed by pubkit's nightly: main has everything this pull request carried."
  fi
  exit 0
fi

# The same change as the open pull request's: leave it, its CI and the DM
# already sent alone.
tree=$(git rev-parse 'HEAD^{tree}')
printf '\n<!-- pubkit-nightly tree=%s -->\n' "$tree" >> "$body"
if [[ -n "$pr" ]] && gh pr view "$pr" --repo "$REPO" --json body --jq .body | grep -q "tree=$tree"; then
  echo "$REPO: #$pr already carries tonight's update." >> "$summary"
  exit 0
fi

head=$(git rev-parse HEAD)
git push -q --force origin "$branch"
if [[ -n "$pr" ]]; then
  gh pr edit "$pr" --repo "$REPO" --body-file "$body"
else
  url=$(gh pr create --repo "$REPO" --head "$branch" --base main \
    --title "Nightly: latest pub* releases and audited crate updates" --body-file "$body")
  pr=${url##*/}
fi
link="https://github.com/$REPO/pull/$pr"
echo "$REPO: [#$pr]($link), commits: $made." >> "$summary"

if wait_ci "$head"; then
  notify "$REPO nightly <$link|#$pr> is green ($made). Merge it when you are ready."
  exit 0
fi
with_crates=$failed

# Re-test the pins alone: if they pass, the crate updates caused the failure
# and stay out.
if [[ "$made" = "pins crates" ]]; then
  git push -q --force origin HEAD~1:"$branch"
  if wait_ci "$(git rev-parse HEAD~1)"; then
    { cat "$body"; printf '\n## Crate updates dropped\n\nWith them CI failed (%s); without them it passes, so this pull request carries the pins alone.\n' "$with_crates"; } > "$body.new"
    gh pr edit "$pr" --repo "$REPO" --body-file "$body.new"
    notify "$REPO nightly <$link|#$pr> is green with the pub* pins alone: the crate updates failed CI ($with_crates) and were dropped. Merge it when you are ready."
    exit 0
  fi
  git push -q --force origin "$head:$branch"
  notify "$REPO nightly <$link|#$pr> fails, with the crate updates ($with_crates) and without them ($failed)."
  exit 1
fi
notify "$REPO nightly <$link|#$pr> fails: $with_crates"
exit 1
