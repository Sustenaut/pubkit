"""The releases of the packages a repository's pins name, where it pins none yet.

pubrules holds no other package's version. A repository pins each one
itself: pubrel in ci.release.pubrel (or the release workflow sync last
wrote), pubsec and pubdeploy in deploy.providers (or the launcher sync last
wrote), pubtest in tests/requirements.txt. The nightly moves each pin to the
package's latest release. Only a pin a repository does not have yet, as when
`pubrules new` or `init` starts one, or sync writes a launcher for the first
time, is read here: from the release-state file PUBRULES_RELEASES names, when
its `releases` block lists the package, otherwise from the package's latest
GitHub release.

    {"releases": {"pubrel": "0.8.0", "pubtest": "0.2.2",
                  "pubsec": {"version": "0.3.0", "sha256": "..."}}}

A version alone, or with the sha256 of its wheel (without it, the wheel is
fetched and hashed).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from .refs import OWNER

# The variable naming the release-state file, and the block it is read from.
STATE = "PUBRULES_RELEASES"
BLOCK = "releases"
# The packages a pin of which pubrules writes, where a repository has none.
PACKAGES = ("pubrel", "pubsec", "pubdeploy", "pubtest")
WHEEL = "https://github.com/{owner}/{name}/releases/download/v{version}/{name}-{version}-py3-none-any.whl"
RELEASE = re.compile(r"v?(?P<version>[0-9]+\.[0-9]+\.[0-9]+)")


def stated(name: str) -> dict[str, str] | None:
    """`name`'s entry in the release-state file, as {"version", "sha256"?}, or None."""
    path = os.environ.get(STATE)
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.exit(f"pubrules: {STATE} names {path}, which cannot be read as JSON: {e}")
    block = data.get(BLOCK) if isinstance(data, dict) else None
    entry = block.get(name) if isinstance(block, dict) else None
    if entry is None:
        return None
    if isinstance(entry, str):
        entry = {"version": entry}
    if (not isinstance(entry, dict) or not isinstance(entry.get("version"), str)
            or not RELEASE.fullmatch(entry["version"])
            or not isinstance(entry.get("sha256", ""), str)):
        sys.exit(f'pubrules: {path}\'s {BLOCK}.{name} must be a version, such as "1.2.3", '
                 f'or {{"version": "1.2.3", "sha256": "..."}}')
    return {**entry, "version": RELEASE.fullmatch(entry["version"])["version"]}


def version(name: str, owner: str = OWNER) -> str:
    """The release of `name` to pin: the release state's, else its latest GitHub release."""
    entry = stated(name)
    if entry is not None:
        return entry["version"]
    from .nightly import latest_release  # noqa: PLC0415
    tag = latest_release(f"{owner}/{name}")
    m = RELEASE.fullmatch(tag or "")
    if not m:
        sys.exit(f"pubrules: no release of {name} to pin: neither {STATE}'s {BLOCK} block nor "
                 f"{owner}/{name}'s latest GitHub release names one")
    return m["version"]


def wheel(name: str, owner: str = OWNER) -> dict[str, str]:
    """`name`'s release wheel to pin, as deploy.providers lists one."""
    entry = stated(name) or {}
    at = entry.get("version") or version(name, owner)
    url = WHEEL.format(owner=owner, name=name, version=at)
    sha = entry.get("sha256")
    if not sha:
        from .cli import sha256_of  # noqa: PLC0415
        sha = sha256_of(url)
    return {"distribution": name, "wheel": url, "sha256": sha}
