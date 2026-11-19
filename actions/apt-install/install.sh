#!/usr/bin/env bash
# Installs $PACKAGES with apt-get, keeping the package lists and the .deb
# files in $DIR (lists/ and archives/) rather than in /var, so a cache can
# hold them. With HIT=true, $DIR came from the cache: the packages are
# installed from it with --no-download, so nothing is fetched. When that
# fails (lists or packages the cache lacks), or with no hit, the lists are
# updated and the packages installed through the retry action's script
# ($TRIES tries, $PAUSE seconds apart). Then $DIR is made the runner's own,
# without apt's lock files and partial downloads, so actions/cache/save
# can read it; save=true goes to $GITHUB_OUTPUT when there was no hit, so
# the new key is saved once. Exits 1 when no install passed.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
dir="${DIR:?apt-install: no directory}"
mkdir -p "$dir/lists/partial" "$dir/archives/partial"
# apt-get reads this after its own configuration: the package lists and
# archives in $dir, no package cache written to /var/cache/apt, and every
# download kept, whatever apt.conf.d says.
export APT_CONF="$dir.conf"
cat > "$APT_CONF" <<CONF
Dir::State::Lists "$dir/lists";
Dir::Cache::Archives "$dir/archives";
Dir::Cache::pkgcache "";
Dir::Cache::srcpkgcache "";
APT::Keep-Downloaded-Packages "true";
CONF
export APT_FLAGS=-y
[ "${NO_INSTALL_RECOMMENDS:-true}" = true ] && APT_FLAGS="-y --no-install-recommends"
# plan.sh has checked every package name, so splitting PACKAGES on spaces
# is safe; set -f globs nothing.
# shellcheck disable=SC2016 # expanded by the bash that runs each try
install='set -f; sudo apt-get -c "$APT_CONF" install $APT_FLAGS'
installed=false
if [ "${HIT:-false}" = true ]; then
  if bash -ec "$install --no-download \$PACKAGES"; then
    installed=true
  else
    echo "::warning::apt-install: the cached lists or packages did not install; updating them"
  fi
fi
if [ "$installed" = false ]; then
  # shellcheck disable=SC2016 # expanded by the bash that runs each try
  WHAT="apt-get install" RUN="set -f; sudo apt-get -c \"\$APT_CONF\" update; $install \$PACKAGES" \
    "$here/../retry/retry.sh" || exit 1
fi
sudo rm -rf "$dir/lists/lock" "$dir/lists/partial" "$dir/archives/lock" "$dir/archives/partial"
sudo chown -R "$(id -u):$(id -g)" "$dir"
if [ "${HIT:-false}" != true ]; then
  echo "save=true" >> "${GITHUB_OUTPUT:?}"
fi
