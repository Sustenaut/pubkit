#!/usr/bin/env bash
# Drops what no `pubrules cache run` used since the last save, from the copy
# of pubrules this action came with, so each made entry keeps one build's worth.
set -euo pipefail

src="$(cd "$(dirname "$0")/../../src" && pwd)"
PYTHONPATH="$src" python3 -P -m pubrules.cache prune
