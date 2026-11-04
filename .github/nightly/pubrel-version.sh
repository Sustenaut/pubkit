#!/usr/bin/env bash
# The pubrel the nightly installs, and pins each repository's release workflow to: its latest
# GitHub release, if crates.io has that version, else the newest crates.io has, with a warning.
# A release is tagged before its crate is published, and a repository's release workflow installs
# pubrel from crates.io too, so a version only on GitHub would break both.
#
#   pubrel-version.sh vX.Y.Z    # prints X.Y.Z, or the version to use instead
#
# CRATES_API points it elsewhere (the tests).
set -euo pipefail

tag=${1#v}
api=${CRATES_API:-https://crates.io/api/v1}
# crates.io refuses requests without a User-Agent that says who is asking.
agent="pubkit-nightly (https://github.com/Sustenaut/pubkit)"

if found=$(curl -fsS -A "$agent" "$api/crates/pubrel/$tag" 2>/dev/null) &&
  [ "$(jq -r '.version.yanked' <<<"$found")" = false ]; then
  echo "$tag"
  exit 0
fi
if newest=$(curl -fsS -A "$agent" "$api/crates/pubrel" 2>/dev/null | jq -er '.crate.max_stable_version // empty'); then
  echo "::warning::pubrel $tag is tagged but not on crates.io: using $newest, the newest there, until it is." >&2
  echo "$newest"
  exit 0
fi
echo "::warning::crates.io didn't answer for pubrel: trying $tag, its latest release." >&2
echo "$tag"
