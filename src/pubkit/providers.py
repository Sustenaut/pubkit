"""deploy.providers: the packages a deploy block's walk runs with, and the
two pubkit floors, pubsec and pubdeploy (`ensure`, in `pubkit sync`), each
listed at the oldest release this pubkit works with or newer. pubkit holds
no deploy code: pubdeploy walks, and its sustenaut.bump hook moves each
provider to its latest release.

pubkit.json is changed in place, the two strings of an entry and nothing
else, so its layout stays the repository's.
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
# Why each floored package is listed, for the line sync --check prints.
WHY = {PUBSEC: "the types that hold a secret's value are pubsec's",
       PUBDEPLOY: "the deploy walk, and the check sync runs of the block, are pubdeploy's"}


def version_of(wheel: str) -> tuple[int, ...] | None:
    m = WHEEL.fullmatch(wheel)
    if not m:
        return None
    try:
        return tuple(int(n) for n in m["version"].split("."))
    except ValueError:
        return None


def floor(name: str) -> dict[str, str]:
    """`name` at the oldest release this pubkit works with, as deploy.providers lists it."""
    from .new import FLOORS  # noqa: PLC0415
    version, sha, wheel = FLOORS[name]
    return {"distribution": name, "wheel": wheel.format(version), "sha256": sha}


def listed(config: dict[str, Any], name: str) -> dict[str, str] | None:
    """deploy.providers' entry for `name`, if it lists one."""
    deploy = config.get("deploy")
    given = deploy.get("providers") if isinstance(deploy, dict) else None
    return next((p for p in given or [] if isinstance(p, dict) and p.get("distribution") == name), None)


def sound(given: dict[str, str] | None, want: dict[str, str]) -> bool:
    return given is not None and isinstance(given.get("wheel"), str) and isinstance(given.get("sha256"), str) \
        and bool(given["sha256"]) and (version_of(given["wheel"]) or ()) >= (version_of(want["wheel"]) or ())


def launched(config: dict[str, Any], name: str) -> dict[str, str]:
    """The `name` its launcher runs: deploy.providers' when it is at least the floor, else the floor."""
    want, given = floor(name), listed(config, name)
    return given if sound(given, want) else want


def ensure(root: Path, config: dict[str, Any], check: bool) -> tuple[list[str], bool]:
    """A repository with a deploy block lists pubsec and pubdeploy in
    deploy.providers, each at this pubkit's floor or newer. The pins are the
    repository's own (`pubkit bump` moves them, through pubdeploy's hook):
    `sync` adds the floor where none is listed, or moves one below it up,
    changing nothing else of pubkit.json; `--check` fails instead. Lines to
    print, and whether that fails."""
    if not isinstance(config.get("deploy"), dict):
        return [], False
    out, failed = [], False
    for name in (PUBSEC, PUBDEPLOY):
        lines, bad = ensure_one(root, config, name, check)
        out += lines
        failed = failed or bad
        if lines and not check:
            config = json.loads((root / "pubkit.json").read_text(encoding="utf-8"))
    return out, failed


def ensure_one(root: Path, config: dict[str, Any], name: str, check: bool) -> tuple[list[str], bool]:
    want, given = floor(name), listed(config, name)
    if sound(given, want):
        return [], False
    version = WHEEL.fullmatch(want["wheel"])["version"]
    if check:
        said = f"does not list {name}" if given is None else f"lists a {name} older than this pubkit works with"
        return [f"pubkit: deploy.providers {said}: {WHY[name]}, {version} or newer; "
                f"run `pubkit sync`, which lists {version}"], True
    path = root / "pubkit.json"
    text = path.read_text(encoding="utf-8")
    entry = json.dumps(want)
    if given is not None:
        for key in ("wheel", "sha256"):
            if isinstance(given.get(key), str) and given[key]:
                text = text.replace(json.dumps(given[key]), json.dumps(want[key]), 1)
        if listed(json.loads(text), name) != want:  # a hand-written entry, in another shape: written whole
            data = json.loads(text)
            data["deploy"]["providers"] = [want if p is given else p for p in data["deploy"]["providers"]]
            text = json.dumps(data, indent=2) + "\n"
    else:
        text = inserted(text, entry, want)
    path.write_text(text, encoding="utf-8")
    return [f"updated  pubkit.json (deploy.providers {name}: {version})"], False


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
