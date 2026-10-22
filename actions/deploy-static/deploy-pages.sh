#!/usr/bin/env bash
# Deploys DIR to the Cloudflare Pages project PROJECT as its production
# branch, recording the commit SOURCE. wrangler reads the token and account
# from the environment. Pinned, so a deploy runs the wrangler it was tested
# with.
set -euo pipefail

npx --yes wrangler@4.147.0 pages deploy "$DIR" \
  --project-name "$PROJECT" --branch main --commit-hash "$SOURCE"
