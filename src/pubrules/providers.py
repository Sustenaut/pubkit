"""deploy.providers: the packages a deploy block's walk runs with, among them
pubsec and pubdeploy (`ensure`, in `pubrules sync`). pubrules holds no deploy
code and no version of either: pubdeploy walks, and its sustenaut.bump hook
moves each provider to its latest release.

The pubsec and pubdeploy the launchers run are the repository's own pins:
deploy.providers' entry, else the one its launcher already fetches, else
(a launcher sync writes for the first time) the latest release
(releases.py).

pubrules.json is changed in place, the entry added and nothing else, so its
layout stays the repository's.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

WHEEL = re.compile(r"https://github\.com/(?P<repo>[A-Za-z0-9-]+/[A-Za-z0-9_.-]+)/releases/download/[^/\s]+/"
                   r"(?P<name>[A-Za-z0-9_.]+)-(?P<version>[^-/\s]+)-py3-none-any\.whl")
PUBSEC = "pubsec"
PUBDEPLOY = "pubdeploy"
# Why each of the two is listed, for the line sync --check prints.
WHY = {PUBSEC: "the types that hold a secret's value are pubsec's",
       PUBDEPLOY: "the deploy walk, and the check sync runs of the block, are pubdeploy's"}
# The wheel a launcher fetches and runs, as the managed launchers write it.
FETCHED = re.compile(r"^fetch (?P<wheel>\S+) (?P<sha256>[0-9a-f]{64}) providers deploy\.providers$", re.M)


def listed(config: dict[str, Any], name: str) -> dict[str, str] | None:
    """deploy.providers' entry for `name`, if it lists one."""
    deploy = config.get("deploy")
    given = deploy.get("providers") if isinstance(deploy, dict) else None
    return next((p for p in given or [] if isinstance(p, dict) and p.get("distribution") == name), None)


def sound(given: dict[str, str] | None) -> bool:
    return given is not None and isinstance(given.get("wheel"), str) and isinstance(given.get("sha256"), str) \
        and bool(given["sha256"]) and WHEEL.fullmatch(given["wheel"]) is not None


def in_launcher(root: Path | None, name: str) -> dict[str, str] | None:
    """The wheel the repository's ./`name` fetches, as sync last wrote it."""
    path = root / name if root is not None else None
    if path is None or not path.is_file():
        return None
    m = FETCHED.search(path.read_text(encoding="utf-8"))
    return {"distribution": name, "wheel": m["wheel"], "sha256": m["sha256"]} if m else None


def pinned(root: Path | None, config: dict[str, Any], name: str) -> dict[str, str]:
    """The `name` the repository pins: deploy.providers', else its launcher's,
    else the latest release."""
    given = listed(config, name)
    if sound(given):
        return given
    found = in_launcher(root, name)
    if found is not None:
        return found
    from .releases import wheel  # noqa: PLC0415
    return wheel(name)


def ensure(root: Path, config: dict[str, Any], check: bool) -> tuple[list[str], bool]:
    """A repository with a deploy block lists pubsec and pubdeploy in
    deploy.providers. The pins are the repository's own (`pubrules bump`
    moves them, through pubdeploy's hook): `sync` adds one where none is
    listed, the one its launcher runs or else the latest release, changing
    nothing else of pubrules.json; `--check` fails instead. Lines to print,
    and whether that fails."""
    if not isinstance(config.get("deploy"), dict):
        return [], False
    out, failed = [], False
    for name in (PUBSEC, PUBDEPLOY):
        lines, bad = ensure_one(root, config, name, check)
        out += lines
        failed = failed or bad
        if lines and not check:
            config = json.loads((root / "pubrules.json").read_text(encoding="utf-8"))
    return out, failed


def ensure_one(root: Path, config: dict[str, Any], name: str, check: bool) -> tuple[list[str], bool]:
    given = listed(config, name)
    if sound(given):
        return [], False
    said = f"does not list {name}" if given is None else f"lists {name} without a release wheel and its sha256"
    if check:
        return [f"pubrules: deploy.providers {said}: {WHY[name]}; run `pubrules sync`, which lists "
                f"the one ./{name} runs, or its latest release"], True
    want = pinned(root, {}, name)
    path = root / "pubrules.json"
    text = path.read_text(encoding="utf-8")
    if given is not None:  # a hand-written entry, in another shape: written whole
        data = json.loads(text)
        data["deploy"]["providers"] = [want if p == given else p for p in data["deploy"]["providers"]]
        text = json.dumps(data, indent=2) + "\n"
    else:
        text = inserted(text, json.dumps(want), want)
    path.write_text(text, encoding="utf-8")
    return [f"updated  pubrules.json (deploy.providers {name}: {WHEEL.fullmatch(want['wheel'])['version']})"], False


def inserted(text: str, entry: str, want: dict[str, str]) -> str:
    """pubrules.json with the entry added to the deploy block's providers, the
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
