#!/usr/bin/env bash
# Runs $RUN with `bash -ec` up to $TRIES times (3), until a try passes.
# After each failed try it warns "$WHAT failed (attempt N of T)" and sleeps
# $PAUSE seconds (15), except after the last. Exits 0 when a try passed,
# 1 when every try failed, and 2 when TRIES or PAUSE is not a number.
set -uo pipefail

what="${WHAT:-setup}" tries="${TRIES:-3}" pause="${PAUSE:-15}"
if ! [[ "$tries" =~ ^[1-9][0-9]*$ ]]; then
  echo "::error::retry: tries must be a whole number above 0, not '$tries'"
  exit 2
fi
if ! [[ "$pause" =~ ^[0-9]+$ ]]; then
  echo "::error::retry: pause must be a whole number of seconds, not '$pause'"
  exit 2
fi
for ((attempt = 1; attempt <= tries; attempt++)); do
  bash -ec "${RUN:?retry: no script to run}" && exit 0
  echo "::warning::$what failed (attempt $attempt of $tries)"
  [ "$attempt" = "$tries" ] || sleep "$pause"
done
exit 1
