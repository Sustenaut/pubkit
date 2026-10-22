#!/usr/bin/env bash
# Runs the action's scripts with rsync, ssh-keyscan and npx replaced by
# stubs that record their arguments, so CI checks what each would run
# without a server or a Cloudflare account.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin" "$work/site" "$work/tmp"
for tool in rsync ssh-keyscan npx; do
  printf '#!/usr/bin/env bash\nprintf "%%s\\n" "%s $*" >> "%s/calls"\n' "$tool" "$work" > "$work/bin/$tool"
  chmod +x "$work/bin/$tool"
done
export PATH="$work/bin:$PATH" RUNNER_TEMP="$work/tmp"

failures=0
check() {
  if grep -qF -- "$2" "$work/calls"; then
    echo "ok: $1"
  else
    echo "FAIL: $1: no call with: $2"
    cat "$work/calls"
    failures=$((failures + 1))
  fi
}
refuses() {
  if (env "${@:2}" "$here/check-inputs.sh") > /dev/null 2>&1; then
    echo "FAIL: $1"
    failures=$((failures + 1))
  else
    echo "ok: $1"
  fi
}

# The server, with a pinned host key: no keyscan, and the key gone after.
: > "$work/calls"
DIR="$work/site/" DEPLOY_KEY=secret TARGET=deploy@example.eu:./ KNOWN_HOSTS="example.eu ssh-ed25519 AAAA" \
  "$here/deploy-server.sh"
check "rsync copies the directory's contents" "rsync -rlvz --checksum --delete -e"
check "rsync goes to the target" "$work/site/ deploy@example.eu:./"
if grep -q ssh-keyscan "$work/calls"; then echo "FAIL: keyscan despite known-hosts"; failures=$((failures + 1)); fi
if [ -e "$work/tmp/deploy-static-ssh" ]; then echo "FAIL: key left behind"; failures=$((failures + 1)); fi

# The server, without a host key: it is scanned from the target's host.
: > "$work/calls"
DIR="$work/site" DEPLOY_KEY=secret TARGET=deploy@example.eu:./ KNOWN_HOSTS='' "$here/deploy-server.sh"
check "the host key is scanned from the target" "ssh-keyscan -t ed25519 example.eu"

# Cloudflare Pages.
: > "$work/calls"
DIR="$work/site" PROJECT=example-site SOURCE=abc123 "$here/deploy-pages.sh"
check "wrangler deploys to the project's main branch" \
  "npx --yes wrangler@4.147.0 pages deploy $work/site --project-name example-site --branch main --commit-hash abc123"

# The inputs.
base=(DIR="$work/site" DEPLOY_KEY_SET=false TARGET='' CLOUDFLARE_SET=false ACCOUNT='' PROJECT='')
refuses "nothing to deploy to" "${base[@]}"
refuses "deploy-key without deploy-target" "${base[@]}" DEPLOY_KEY_SET=true
refuses "cloudflare-api-token without a project" "${base[@]}" CLOUDFLARE_SET=true ACCOUNT=a
refuses "a missing dir" "${base[@]}" DIR="$work/none" CLOUDFLARE_SET=true ACCOUNT=a PROJECT=p
if env "${base[@]}" DEPLOY_KEY_SET=true TARGET=d@h:./ CLOUDFLARE_SET=true ACCOUNT=a PROJECT=p "$here/check-inputs.sh"; then
  echo "ok: both targets accepted"
else
  echo "FAIL: both targets refused"
  failures=$((failures + 1))
fi

[ "$failures" -eq 0 ]
