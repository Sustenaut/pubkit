"""The repos file: which repositories pubkit acts on, from outside pubkit.

    {"repositories": [
      {"repo": "OWNER/NAME", "protect": true, "govern": false, "project": "some-group"}
    ]}

`protect` says it should carry pubkit's rulesets, `govern` that a nightly
keeps it current, and `project` (optional) names a group on the board. How it
is protected stays in its own pubkit.json's protect block. `pubkit board`,
`pubkit protect` and `pubkit freshness` take one with `--repos FILE`, and the
reusable board.yml and nightly.yml take the same thing as a JSON input. pubkit
doesn't care who writes it.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
KEYS = {"repo", "protect", "govern", "project"}


def problem(entry: Any) -> str | None:
    """What is wrong with one entry, or None."""
    if not isinstance(entry, dict) or not REPO.fullmatch(str(entry.get("repo", ""))):
        return f"{json.dumps(entry)} needs a repo, OWNER/NAME"
    name = entry["repo"]
    if entry.keys() - KEYS:
        return f"{name}: {', '.join(sorted(entry.keys() - KEYS))} is not a setting; it takes " \
               f"protect, govern and project"
    for key in ("protect", "govern"):
        if not isinstance(entry.get(key, False), bool):
            return f"{name}: \"{key}\" must be true or false, not {json.dumps(entry[key])}"
    if not isinstance(entry.get("project", ""), str):
        return f"{name}: \"project\" must be a name, not {json.dumps(entry['project'])}"
    return None


def entries(text: str | bytes, where: str) -> list[dict]:
    """The entries of a repos file's contents, with `protect` and `govern`
    always there, or a ValueError saying what is wrong."""
    try:
        listed = json.loads(text)["repositories"]
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError(f"{where} needs a \"repositories\" list ({e})") from e
    if not isinstance(listed, list):
        raise ValueError(f"{where}'s \"repositories\" is not a list")
    for entry in listed:
        if (bad := problem(entry)) is not None:
            raise ValueError(f"{where}: {bad}")
    names = [e["repo"] for e in listed]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise ValueError(f"{where} lists {', '.join(twice)} more than once")
    return [{"protect": False, "govern": False, **e} for e in listed]


def read(path: Path, command: str) -> list[dict]:
    """The entries of the repos file at `path`, or stop with what is wrong."""
    try:
        return entries(path.read_text(encoding="utf-8"), str(path))
    except (OSError, ValueError) as e:
        sys.exit(f"pubkit {command}: {e}")


def warn_lists(command: str, flags: str) -> None:
    """Say that a list pubkit read goes away in a later release."""
    print(f"pubkit {command}: warning: {flags} go in a later release; "
          f"use --repos FILE (see README, The repos file)", file=sys.stderr)
