"""`pubkit signing-key backup`: copy the signing keys repositories declare to Bitwarden.

A repository says which signing keys it uses, and where each is backed up, in
pubkit.json:

    "signing_keys": [
      {"path": "corpus/.publet/signing.key",
       "bitwarden": {"project": "pub", "name": "pub-signing-key-example"}}
    ]

`path` is from the repository's root, or from the home directory when it
starts with "~/": a key kept outside any repository is declared by the one
whose tools use it.

    pubkit signing-key backup [--under DIR] [--dry-run]

reads the declarations of this repository, or of every repository directly
under DIR, and writes each key that is on this machine and not yet in its
Bitwarden project, with Bitwarden's SDK, from memory. It asks for an access
token that may read and write those projects at a hidden prompt. A secret
already there with the same value is left alone; one with another value is
never overwritten, and fails the run. --dry-run says what it would write and
asks for nothing. No key or token is printed, put on a command line or
written anywhere else.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

CONFIG = "pubkit.json"
FIELD = "signing_keys"
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
# Where a corpus keeps the key pub signs with: a package's corpus/, or a
# corpus at the repository's root.
KEY_PLACES = ("corpus/.publet/signing.key", ".publet/signing.key")


@dataclass(frozen=True)
class Declared:
    repo: Path
    path: Path
    project: str
    name: str


def problems(block: object) -> list[str]:
    """What is wrong with a pubkit.json `signing_keys` block: one phrase each."""
    if block is None:
        return []
    if not isinstance(block, list):
        return [f"{FIELD} must be a list"]
    out: list[str] = []
    names: set[tuple[str, str]] = set()
    for i, entry in enumerate(block):
        where = f"{FIELD}[{i}]"
        if not isinstance(entry, dict):
            out.append(f"{where} must be an object")
            continue
        out += [f"{where}: {key} is not a setting" for key in sorted(entry.keys() - {"path", "bitwarden"})]
        path = entry.get("path")
        if not (isinstance(path, str) and path.strip()):
            out.append(f"{where} needs a path")
        elif Path(path).is_absolute():
            out.append(f"{where}: path is from the repository's root, or starts with ~/: {path}")
        elif not path.startswith("~/") and ".." in Path(path).parts:
            out.append(f"{where}: path leaves the repository: {path}")
        bitwarden = entry.get("bitwarden")
        if not isinstance(bitwarden, dict):
            out.append(f"{where} needs bitwarden, with a project and a name")
            continue
        out += [f"{where}: bitwarden.{key} is not a setting" for key in sorted(bitwarden.keys() - {"project", "name"})]
        project, name = bitwarden.get("project"), bitwarden.get("name")
        if not (isinstance(project, str) and project.strip()):
            out.append(f"{where} needs bitwarden.project, the project's name or id")
        if not (isinstance(name, str) and NAME.fullmatch(name)):
            out.append(f"{where}: bitwarden.name must be a letter, then letters, digits, _ and -")
        elif (project, name) in names:
            out.append(f"{where}: {name} is declared twice")
        names.add((project, name))
    return out


def declared(repo: Path, config: dict) -> list[Declared]:
    out = []
    for entry in config.get(FIELD) or []:
        path = Path(entry["path"]).expanduser() if entry["path"].startswith("~/") else repo / entry["path"]
        out.append(Declared(repo, path, entry["bitwarden"]["project"], entry["bitwarden"]["name"]))
    return out


def undeclared(repo: Path, config: dict) -> list[Path]:
    """The corpus keys here that pubkit.json does not declare."""
    named = {Path(entry.get("path", "")) for entry in config.get(FIELD) or [] if isinstance(entry, dict)}
    return [repo / place for place in KEY_PLACES if (repo / place).is_file() and Path(place) not in named]


def repositories(under: Path | None) -> list[Path]:
    """This repository, or every one directly under `under` (and `under` itself)."""
    if under is None:
        return [Path.cwd()]
    candidates = [under, *sorted(p for p in under.iterdir() if p.is_dir())]
    return [p for p in candidates if (p / CONFIG).is_file()]


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def run(under: Path | None, dry_run: bool) -> int:
    from .github_app import BwsFailed, _listed, ask_hidden, bws  # noqa: PLC0415

    failed = False
    keys: dict[tuple[str, str], list[Declared]] = {}
    for repo in repositories(under):
        try:
            config = json.loads((repo / CONFIG).read_text(encoding="utf-8"))
        except ValueError:
            print(f"{repo / CONFIG}: not JSON; skipped")
            failed = True
            continue
        if wrong := problems(config.get(FIELD)):
            for phrase in wrong:
                print(f"{repo / CONFIG}: {phrase}")
            failed = True
            continue
        for path in undeclared(repo, config):
            print(f"{path}: a signing key {CONFIG} does not declare; add it to {FIELD} to back it up")
        for key in declared(repo, config):
            keys.setdefault((key.project, key.name), []).append(key)

    # The same name may be declared by several checkouts of one repository:
    # one value is backed up, and two values under one name are a mistake.
    plan: dict[tuple[str, str], tuple[Declared, str]] = {}
    for (project, name), places in sorted(keys.items()):
        here = [(key, key.path.read_text(encoding="utf-8")) for key in places if key.path.is_file()]
        if not here:
            print(f"{name}: not on this machine ({', '.join(str(key.path) for key in places)})")
            continue
        if len({_digest(value) for _, value in here}) > 1:
            print(f"{name}: different keys at {', '.join(str(key.path) for key, _ in here)}; none backed up")
            failed = True
            continue
        plan[(project, name)] = here[0]

    if dry_run or not plan:
        for (project, name), (key, _) in plan.items():
            print(f"{name}: would back up {key.path} to the project {project}, unless it is there")
        return 1 if failed else 0

    token = ask_hidden(f"Bitwarden access token that may read and write {', '.join(sorted({p for p, _ in plan}))}: ")
    if not token:
        print("no token given; nothing written")
        return 1
    from .bitwarden import WriteFailed, ensure, write  # noqa: PLC0415
    try:
        projects = _listed(bws(token, "project", "list", "--output", "json", "--color", "no"))
    except BwsFailed as error:
        print(f"bws: {error}")
        return 1
    sdk_ready = False
    for project in sorted({p for p, _ in plan}):
        found = [p for p in projects if project in (p.get("id"), p.get("name"))]
        names = [(p, n) for p, n in plan if p == project]
        if len(found) != 1:
            print(f"this token sees {len(found)} projects named {project}, not one; "
                  f"not backed up: {', '.join(n for _, n in names)}")
            failed = True
            continue
        try:
            stored = _listed(bws(token, "secret", "list", found[0]["id"], "--output", "json", "--color", "no"))
        except BwsFailed as error:
            print(f"bws: {error}")
            failed = True
            continue
        values = {}
        for secret in stored:
            values.setdefault(secret.get("key"), []).append(_digest(str(secret.get("value", ""))))
        for _, name in names:
            key, value = plan[(project, name)]
            if name in values:
                if values[name] == [_digest(value)]:
                    print(f"{name}: already backed up in {project}")
                else:
                    print(f"{name}: {project} holds another value under this name; not overwritten")
                    failed = True
                continue
            if not sdk_ready:
                if why := ensure():
                    print(why)
                    return 1
                sdk_ready = True
            try:
                write(token, found[0]["organizationId"], found[0]["id"], name, value,
                      f"signing key, a copy of {key.path} (declared in {key.repo / CONFIG})")
            except WriteFailed as error:
                print(f"{name}: Bitwarden refused the write: {error}")
                failed = True
                continue
            print(f"{name}: backed up {key.path} to {project}")
    return 1 if failed else 0
