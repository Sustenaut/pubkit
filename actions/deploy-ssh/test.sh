#!/usr/bin/env bash
# Runs the action's scripts with ssh replaced by a stub that records its
# arguments and what it was sent, so CI checks what it would run without a
# server.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin" "$work/tmp" "$work/repo/deploy"
cat > "$work/bin/ssh" <<STUB
#!/usr/bin/env bash
printf 'ssh %s\n' "\$*" >> "$work/calls"
cat > "$work/stdin"
STUB
chmod +x "$work/bin/ssh"
export PATH="$work/bin:$PATH" RUNNER_TEMP="$work/tmp"
echo hello > "$work/repo/deploy/file.txt"

failures=0
fail() { echo "FAIL: $1"; failures=$((failures + 1)); }
check() {
  if grep -qF -- "$2" "$work/calls"; then echo "ok: $1"; else fail "$1: no call with: $2"; cat "$work/calls"; fi
}
refuses() {
  if (env "${@:2}" "$here/check-inputs.sh") > /dev/null 2>&1; then fail "$1"; else echo "ok: $1"; fi
}

# A bundle goes on standard input, the command as the ssh command.
: > "$work/calls"
(cd "$work/repo" && COMMAND=share-edge BUNDLE=deploy DEPLOY_KEY=secret TARGET=deploy@example.eu PORT=22 \
  KNOWN_HOSTS="example.eu ssh-ed25519 AAAA" "$here/deploy.sh")
check "ssh pins the host key" "-o StrictHostKeyChecking=yes deploy@example.eu share-edge"
check "ssh uses the key alone" "-o IdentitiesOnly=yes -o BatchMode=yes"
if tar -tzf "$work/stdin" | grep -qx deploy/file.txt; then echo "ok: the bundle is a tar of its paths"; else fail "the bundle"; fi
[[ ! -e "$work/tmp/deploy-ssh" ]] || fail "key left behind"

# Without a bundle, nothing is sent (ssh -n).
: > "$work/calls"
COMMAND="check all" BUNDLE='' DEPLOY_KEY=secret TARGET=deploy@example.eu PORT=2222 \
  KNOWN_HOSTS="example.eu ssh-ed25519 AAAA" "$here/deploy.sh" < /dev/null
check "no bundle: ssh -n, on the port given" "ssh -n -p 2222"
check "the command's words go as they are" "deploy@example.eu check all"

# A failing command fails the step.
printf '#!/usr/bin/env bash\ncat > /dev/null; exit 7\n' > "$work/bin/ssh"
if COMMAND=x BUNDLE='' DEPLOY_KEY=k TARGET=d@h PORT=22 KNOWN_HOSTS=h "$here/deploy.sh" < /dev/null; then
  fail "a failing command passed"
else
  echo "ok: a failing command fails the step"
fi

# The inputs.
base=(COMMAND=share-edge BUNDLE='' DEPLOY_KEY_SET=true TARGET=deploy@example.eu PORT=22
  KNOWN_HOSTS="example.eu ssh-ed25519 AAAA" TAILSCALE_ID_SET=false TAILSCALE_SECRET_SET=false)
if env "${base[@]}" "$here/check-inputs.sh"; then echo "ok: good inputs pass"; else fail "good inputs refused"; fi
refuses "a command with shell characters" "${base[@]}" "COMMAND=edge; rm -rf /"
refuses "an empty command" "${base[@]}" COMMAND=
refuses "no deploy key" "${base[@]}" DEPLOY_KEY_SET=false
refuses "a target without a user" "${base[@]}" TARGET=example.eu
refuses "a port that is not a number" "${base[@]}" PORT=ssh
refuses "no host key" "${base[@]}" KNOWN_HOSTS=
refuses "a Tailscale client without its secret" "${base[@]}" TAILSCALE_ID_SET=true
refuses "a bundle path that is not there" "${base[@]}" BUNDLE="$work/none"

[[ "$failures" -eq 0 ]]
