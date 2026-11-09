"""deploy.providers: pubsec listed at pubkit's floor or newer (`ensure`, in
`pubkit sync`), and in `pubkit bump` each package, pubsec too, moved to its
latest release.

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
        if not isinstance(given.get("sha256"), str) or not given["sha256"]:
            out.append(f"left     deploy.providers {given.get('distribution')}: it has no sha256 to move; "
                       f"give it the wheel's, as `pubkit sync --check` says")
            continue
        found = (find or latest)(given["wheel"], gh)
        if found is None or found[0] == given["wheel"]:
            continue
        text = text.replace(given["wheel"], found[0]).replace(given["sha256"], found[1])
        out.append(f"updated  pubkit.json (deploy.providers {given.get('distribution')}: "
                   f"{WHEEL.fullmatch(found[0])['version'] if WHEEL.fullmatch(found[0]) else found[0]})")
    if any(line.startswith("updated") for line in out):
        path.write_text(text, encoding="utf-8")
    return out


PUBSEC = "pubsec"


def version_of(wheel: str) -> tuple[int, ...] | None:
    m = WHEEL.fullmatch(wheel)
    if not m:
        return None
    try:
        return tuple(int(n) for n in m["version"].split("."))
    except ValueError:
        return None


def floor() -> dict[str, str]:
    """pubsec at the oldest release this pubkit works with, as deploy.providers lists it."""
    from ..new import PUBSEC_FLOOR, PUBSEC_WHEEL  # noqa: PLC0415
    version, sha = PUBSEC_FLOOR
    return {"distribution": PUBSEC, "wheel": PUBSEC_WHEEL.format(version), "sha256": sha}


def pubsec_of(config: dict[str, Any]) -> dict[str, str] | None:
    """deploy.providers' pubsec entry, if it lists one."""
    deploy = config.get("deploy")
    listed = deploy.get("providers") if isinstance(deploy, dict) else None
    return next((p for p in listed or [] if isinstance(p, dict) and p.get("distribution") == PUBSEC), None)


def launched(config: dict[str, Any]) -> dict[str, str]:
    """The pubsec ./pubsec runs: deploy.providers' when it is at least the floor, else the floor."""
    want, given = floor(), pubsec_of(config)
    if given and isinstance(given.get("wheel"), str) and isinstance(given.get("sha256"), str) and given["sha256"] \
            and (version_of(given["wheel"]) or ()) >= (version_of(want["wheel"]) or ()):
        return given
    return want


def ensure(root: Path, config: dict[str, Any], check: bool) -> tuple[list[str], bool]:
    """A repository with a deploy block lists pubsec in deploy.providers, at
    this pubkit's floor or newer, since the types that hold a secret's value
    are pubsec's. The repository's pin is its own (`pubkit bump` moves it to
    the latest release): `sync` adds the floor where none is listed, or moves
    one below it up to it, changing nothing else of pubkit.json; `--check`
    fails instead. Lines to print, and whether that fails."""
    if not isinstance(config.get("deploy"), dict):
        return [], False
    want, given = floor(), pubsec_of(config)
    if given is not None and (version_of(str(given.get("wheel", ""))) or ()) >= (version_of(want["wheel"]) or ()) \
            and given.get("sha256"):
        return [], False
    version = WHEEL.fullmatch(want["wheel"])["version"]
    if check:
        said = "does not list pubsec" if given is None else "lists a pubsec older than this pubkit works with"
        return [f"pubkit: deploy.providers {said}: the types that hold a secret's value are pubsec's, "
                f"{version} or newer; run `pubkit sync`, which lists {version}"], True
    path = root / "pubkit.json"
    text = path.read_text(encoding="utf-8")
    entry = json.dumps(want)
    if given is not None:
        for key in ("wheel", "sha256"):
            if isinstance(given.get(key), str) and given[key]:
                text = text.replace(json.dumps(given[key]), json.dumps(want[key]), 1)
        if pubsec_of(json.loads(text)) != want:  # a hand-written entry, in another shape: written whole
            data = json.loads(text)
            data["deploy"]["providers"] = [want if p is given else p for p in data["deploy"]["providers"]]
            text = json.dumps(data, indent=2) + "\n"
    else:
        text = inserted(text, entry, want)
    path.write_text(text, encoding="utf-8")
    return [f"updated  pubkit.json (deploy.providers pubsec: {version})"], False


def inserted(text: str, entry: str, want: dict[str, str]) -> str:
    """pubkit.json with the entry added to the deploy block's providers, the
    rest of the text as it was. A text edit is kept only when the file then
    reads as the same JSON with that one entry more; otherwise it is written
    whole."""
    expected = json.loads(text)
    expected["deploy"]["providers"] = [want, *expected["deploy"].get("providers", [])]
    candidates = []
    deploy = re.search(r'"deploy"\s*:\s*\{(?P<space>\s*)', text)
    if deploy:
        listed = re.compile(r'"providers"\s*:\s*\[').search(text, deploy.end())
        if listed:
            rest = text[listed.end():]
            candidates.append(text[:listed.end()] + entry + ("" if rest.lstrip().startswith("]") else ", ") + rest)
        space = deploy["space"] if "\n" in deploy["space"] else " "
        candidates.append(text[:deploy.end()] + f'"providers": [{entry}],{space}' + text[deploy.end():])
    for candidate in candidates:
        try:
            if json.loads(candidate) == expected:
                return candidate
        except ValueError:
            continue
    return json.dumps(expected, indent=2) + "\n"
