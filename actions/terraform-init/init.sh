#!/usr/bin/env bash
# terraform init in $DIR with $ARGS, tried $TRIES times through the retry
# action's script, then once more with every provider from pubkit.json's
# mirror.terraform, when it names one. Exits 1 when init never passed,
# and 2, as retry does, when TRIES or PAUSE is not a number.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
export TF="${TF:-terraform}" DIR="${DIR:-.}" ARGS="${ARGS:--input=false}"
# The script splits ARGS on spaces and globs nothing.
# shellcheck disable=SC2016 # expanded by the bash that runs each try
init='set -f; "$TF" -chdir="$DIR" init $ARGS'
WHAT="terraform init" RUN="$init" "$here/../retry/retry.sh"
tried=$?
# 2 is a TRIES or PAUSE that is not a number: no mirror mends that.
[ "$tried" = 1 ] || exit "$tried"
mirror="$(jq -r '.mirror.terraform // empty' pubkit.json 2>/dev/null || true)"
[ -n "$mirror" ] || exit 1
echo "::warning::terraform init: trying the providers from $mirror"
config="${RUNNER_TEMP:?}/provider-mirror.tfrc"
printf 'provider_installation {\n  network_mirror { url = "%s/" }\n}\n' "${mirror%/}" > "$config"
TF_CLI_CONFIG_FILE="$config" bash -ec "$init" || exit 1
