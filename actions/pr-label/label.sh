#!/usr/bin/env bash
# Writes present=true or present=false to $GITHUB_OUTPUT: whether the pull
# request this run is for carries $LABEL (action.yml says which pull
# request that is). Reads the event from $GITHUB_EVENT_PATH and, for a push
# or a merge queue group, the pull requests from GitHub's API.
set -euo pipefail

api="${GITHUB_API_URL:-https://api.github.com}"
want="$(printf '%s' "${LABEL:?}" | tr '[:upper:]' '[:lower:]')"

get() {
  curl -fsS -H "Authorization: Bearer ${GH_TOKEN:?}" -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" "$api/repos/${GITHUB_REPOSITORY:?}/$1"
}

# The label names of the pull request(s) this run is for, one per line.
names() {
  case "${GITHUB_EVENT_NAME:-}" in
    pull_request|pull_request_target)
      jq -r '.pull_request.labels[]?.name' "${GITHUB_EVENT_PATH:?}" ;;
    merge_group)
      # The queue's branch names the pull request: gh-readonly-queue/main/pr-15-<sha>.
      local ref number
      ref="$(jq -r '.merge_group.head_ref // ""' "${GITHUB_EVENT_PATH:?}")"
      number="$(sed -nE 's#.*/pr-([0-9]+)-[0-9a-f]+$#\1#p' <<< "$ref")"
      if [[ -n "$number" ]]; then get "pulls/$number" | jq -r '.labels[]?.name'; fi ;;
    push)
      get "commits/${GITHUB_SHA:?}/pulls" | jq -r '.[] | select(.merged_at != null) | .labels[]?.name' ;;
  esac
}

# A failed request fails the step: a missing label must not pass for one
# that could not be read.
labels="$(names)"
present=false
while IFS= read -r name; do
  if [[ -n "$name" && "$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]')" == "$want" ]]; then present=true; fi
done <<< "$labels"
if [[ "$present" == true ]]; then
  echo "pr-label: the pull request this run is for carries \"$LABEL\""
else
  echo "pr-label: no pull request this run is for carries \"$LABEL\""
fi
echo "present=$present" >> "${GITHUB_OUTPUT:?}"
