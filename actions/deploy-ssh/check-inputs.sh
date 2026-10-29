#!/usr/bin/env bash
# deploy-ssh's inputs, checked before anything runs. The action passes them
# as environment variables, the secrets only as whether they are set.
set -euo pipefail

fail() {
  echo "::error::$1"
  exit 1
}

[[ "$COMMAND" =~ ^[A-Za-z0-9._-]+([ ][A-Za-z0-9._-]+)*$ ]] ||
  fail "command: '$COMMAND' is not words of letters, digits and . _ - separated by single spaces"
[[ "$DEPLOY_KEY_SET" = true ]] || fail "deploy-key is empty: is the secret set for this repository?"
[[ "$TARGET" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+$ ]] || fail "target: '$TARGET' is not user@host"
[[ "$PORT" =~ ^[0-9]+$ ]] || fail "port: '$PORT' is not a number"
[[ -n "$KNOWN_HOSTS" ]] || fail "known-hosts is empty: give the server's host key as a known_hosts line"
if [[ "$TAILSCALE_ID_SET" != "$TAILSCALE_SECRET_SET" ]]; then
  fail "tailscale-oauth-client-id and tailscale-oauth-secret go together"
fi
if [[ -n "$BUNDLE" ]]; then
  read -r -a paths <<<"$BUNDLE"
  for path in "${paths[@]}"; do
    [[ -e "$path" ]] || fail "bundle: $path is not in the workspace"
  done
fi
