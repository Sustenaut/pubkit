"""deploy.providers in `pubkit bump`: each package moved to its latest release.

A provider's wheel is a GitHub release's asset, so its latest release is
read through `gh`, and the asset of the same distribution in it is the new
wheel, with the sha256 GitHub records for it (or, for an older release
without one, the sha256 of the file). pubkit.json is changed in place, the
two strings and nothing else, so its layout stays the repository's.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from pathlib import Path
from typing import Any

WHEEL = re.compile(r"https://github\.com/(?P<repo>[A-Za-z0-9-]+/[A-Za-z0-9_.-]+)/releases/download/[^/\s]+/"
                   r"(?P<name>[A-Za-z0-9_.]+)-(?P<version>[^-/\s]+)-py3-none-any\.whl")


def sha256(url: str) -> str:
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - a release asset's address
        for chunk in iter(lambda: response.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def latest(wheel: str, gh: Any = None) -> tuple[str, str] | None:
    """The newest release's wheel of the same distribution, and its sha256; None when there is none."""
    from ..protect import ApiError, gh as api  # noqa: PLC0415
    m = WHEEL.fullmatch(wheel)
    if not m:
        return None
    try:
        release = (gh or api)("GET", f"repos/{m['repo']}/releases/latest", None) or {}
    except ApiError:
        return None
    for asset in release.get("assets") or []:
        name = str(asset.get("name", ""))
        if name.startswith(f"{m['name']}-") and name.endswith("-py3-none-any.whl"):
            url = str(asset.get("browser_download_url", ""))
            digest = str(asset.get("digest") or "")
            return url, digest.removeprefix("sha256:") if digest.startswith("sha256:") else sha256(url)
    return None


def bump(root: Path, gh: Any = None, find: Any = None) -> list[str]:
    """Move each provider of pubkit.json to its latest release; a line per one moved."""
    path = root / "pubkit.json"
    text = path.read_text(encoding="utf-8")
    deploy = json.loads(text).get("deploy")
    out = []
    for given in (deploy or {}).get("providers") or [] if isinstance(deploy, dict) else []:
        if not isinstance(given, dict) or not isinstance(given.get("wheel"), str):
            continue
        found = (find or latest)(given["wheel"], gh)
        if found is None or found[0] == given["wheel"]:
            continue
        text = text.replace(given["wheel"], found[0]).replace(str(given.get("sha256")), found[1])
        out.append(f"updated  pubkit.json (deploy.providers {given.get('distribution')}: "
                   f"{WHEEL.fullmatch(found[0])['version'] if WHEEL.fullmatch(found[0]) else found[0]})")
    if out:
        path.write_text(text, encoding="utf-8")
    return out
