#!/usr/bin/env bash
# Runs `pubkit site check` on DIR before anything is deployed, from the copy
# of pubkit this action came with: site.py needs only Python's standard
# library, so nothing is installed. SITE_URL, when set, is the site's
# address, which lets the check find og:image files in DIR.
set -euo pipefail

src="$(cd "$(dirname "$0")/../../src" && pwd)"
args=(check "$DIR")
if [[ -n "${SITE_URL:-}" ]]; then
  args+=(--url "$SITE_URL")
fi
# -P keeps the workspace off the import path, so only this pubkit is run.
PYTHONPATH="$src" python3 -P -m pubkit.site "${args[@]}"
