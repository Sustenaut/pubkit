#!/usr/bin/env bash
# Runs COMMAND on TARGET over SSH as DEPLOY_KEY, with BUNDLE's paths as a
# gzipped tar on its standard input (nothing when BUNDLE is empty). The key
# and known_hosts are written to the runner's temporary directory and used
# by this ssh alone, so nothing in ~/.ssh is touched. The command's exit
# status is the step's.
set -euo pipefail

ssh_dir="${RUNNER_TEMP:?}/deploy-ssh"
install -m 700 -d "$ssh_dir"
trap 'rm -rf "$ssh_dir"' EXIT
printf '%s\n' "$DEPLOY_KEY" > "$ssh_dir/key"
chmod 600 "$ssh_dir/key"
printf '%s\n' "$KNOWN_HOSTS" > "$ssh_dir/known_hosts"

ssh_opts=(-p "${PORT:-22}" -i "$ssh_dir/key" -o IdentitiesOnly=yes -o BatchMode=yes
  -o UserKnownHostsFile="$ssh_dir/known_hosts" -o StrictHostKeyChecking=yes)

# COMMAND is meant for the server, which reads it as SSH_ORIGINAL_COMMAND.
# shellcheck disable=SC2029
if [[ -n "${BUNDLE:-}" ]]; then
  read -r -a paths <<<"$BUNDLE"
  tar -czf - -- "${paths[@]}" | ssh "${ssh_opts[@]}" "$TARGET" "$COMMAND"
else
  ssh -n "${ssh_opts[@]}" "$TARGET" "$COMMAND"
fi
