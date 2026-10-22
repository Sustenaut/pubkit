#!/usr/bin/env bash
# deploy-static's inputs, checked before anything is deployed. The action
# passes them as environment variables, the secrets only as whether they
# are set.
set -euo pipefail

fail() {
  echo "::error::$1"
  exit 1
}

[ -d "$DIR" ] || fail "dir: $DIR is not a directory"
if [ "$DEPLOY_KEY_SET" != true ] && [ "$CLOUDFLARE_SET" != true ]; then
  fail "nothing to deploy to: set deploy-key, cloudflare-api-token or both"
fi
if [ "$DEPLOY_KEY_SET" = true ] && [ -z "$TARGET" ]; then
  fail "deploy-key needs deploy-target"
fi
if [ "$CLOUDFLARE_SET" = true ] && { [ -z "$ACCOUNT" ] || [ -z "$PROJECT" ]; }; then
  fail "cloudflare-api-token needs cloudflare-account-id and cloudflare-project"
fi
