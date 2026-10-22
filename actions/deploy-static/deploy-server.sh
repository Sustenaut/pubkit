#!/usr/bin/env bash
# Copies DIR's contents to TARGET with rsync over SSH, as DEPLOY_KEY. The
# key and known_hosts are written to the runner's temporary directory and
# used by this ssh alone, so nothing in ~/.ssh is touched.
set -euo pipefail

ssh_dir="${RUNNER_TEMP:?}/deploy-static-ssh"
install -m 700 -d "$ssh_dir"
trap 'rm -rf "$ssh_dir"' EXIT
printf '%s\n' "$DEPLOY_KEY" > "$ssh_dir/key"
chmod 600 "$ssh_dir/key"

if [ -n "${KNOWN_HOSTS:-}" ]; then
  printf '%s\n' "$KNOWN_HOSTS" > "$ssh_dir/known_hosts"
else
  host="${TARGET#*@}"
  host="${host%%:*}"
  ssh-keyscan -t ed25519 "$host" > "$ssh_dir/known_hosts"
fi

# --checksum, since a fresh checkout's files all have new times; --delete
# clears anything an earlier deploy left behind.
rsync -rlvz --checksum --delete \
  -e "ssh -i $ssh_dir/key -o IdentitiesOnly=yes -o UserKnownHostsFile=$ssh_dir/known_hosts -o StrictHostKeyChecking=yes" \
  "${DIR%/}/" "$TARGET"
