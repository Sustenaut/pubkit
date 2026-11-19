#!/usr/bin/env bash
# Writes the cache key and directory of the apt packages $PACKAGES names to
# $GITHUB_OUTPUT: key=apt-<os>-<version>-<arch>-<sha256>, the sha256 of the
# sorted, deduplicated package list and the no-install-recommends flag, and
# dir=$RUNNER_TEMP/apt-install, which holds the package lists and the .deb
# files. <os> and <version> are the hosted runner's ImageOS and
# ImageVersion; where they are unset, as on a self-hosted runner, they are
# the release's codename (lsb_release -cs) and "self-hosted". Exits 2 when
# a package name or the flag is not one apt-install takes.
set -euo pipefail

flag="${NO_INSTALL_RECOMMENDS:-true}"
if [[ "$flag" != true && "$flag" != false ]]; then
  echo "::error::apt-install: no-install-recommends is true or false, not '$flag'"
  exit 2
fi
read -ra given <<< "${PACKAGES:-}"
if [ "${#given[@]}" = 0 ]; then
  echo "::error::apt-install: no packages to install"
  exit 2
fi
# A package, with an optional :arch, =version or /release: never an option
# or anything the shell would read.
for package in "${given[@]}"; do
  if ! [[ "$package" =~ ^[a-z0-9][a-z0-9+.:=~/-]*$ ]]; then
    echo "::error::apt-install: '$package' is not a package name"
    exit 2
  fi
done
if [ -n "${ImageOS:-}" ] && [ -n "${ImageVersion:-}" ]; then
  os="$ImageOS" version="$ImageVersion"
else
  # shellcheck disable=SC1091 # the runner's own file
  os="$(lsb_release -cs 2>/dev/null)" || os="$(. /etc/os-release && echo "${VERSION_CODENAME:-unknown}")"
  version=self-hosted
fi
arch="$(dpkg --print-architecture)"
digest="$({ printf '%s\n' "${given[@]}" | LC_ALL=C sort -u; echo "no-install-recommends=$flag"; } \
  | sha256sum | cut -d' ' -f1)"
{
  echo "key=apt-$os-$version-$arch-$digest"
  echo "dir=${RUNNER_TEMP:?}/apt-install"
} >> "${GITHUB_OUTPUT:?}"
