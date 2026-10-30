"""`pubkit state --check`: is a state repository's state.json sound?

    {"owner": "OWNER",
     "repos": {"NAME": {"project": "some-group", "board": true, "protect": true, "govern": false}}}

One file per GitHub owner says which of its repositories are graded, protected
and governed. This checks its shape, that each repository exists under the
owner, and that each one with `protect` has a protect block in its pubkit.json,
since that block is what says how. It is the state repository's one required
check, so it builds nothing and takes seconds. It reads GitHub through `gh`,
with a token that can read every repository the file names.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from . import protect
from .protect import Api, ApiError

OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")
NAME = re.compile(r"[A-Za-z0-9_.-]+")
ROLES = ("board", "protect", "govern")


def shape(state: Any) -> list[str]:
    """What is wrong with the file's shape, one phrase each."""
    if not isinstance(state, dict):
        return [f"it is {json.dumps(state)}, not an object"]
    out = [f"{key} is not a setting; it takes owner and repos"
           for key in sorted(state.keys() - {"owner", "repos"})]
    if not OWNER.fullmatch(str(state.get("owner", ""))) or not isinstance(state.get("owner"), str):
        out.append('"owner" must be a GitHub owner')
    repos = state.get("repos")
    if not isinstance(repos, dict):
        return [*out, '"repos" must be an object, from each repository\'s name to its roles']
    for name, roles in repos.items():
        if not NAME.fullmatch(name):
            out.append(f"{name} is not a repository's name")
        if not isinstance(roles, dict):
            out.append(f"{name}: its roles must be an object, not {json.dumps(roles)}")
            continue
        for key in sorted(roles.keys() - {"project", *ROLES}):
            out.append(f"{name}: {key} is not a setting; it takes project, board, protect and govern")
        for role in ROLES:
            if not isinstance(roles.get(role), bool):
                out.append(f"{name}: \"{role}\" must be true or false")
        if not isinstance(roles.get("project", ""), str) or roles.get("project") == "":
            out.append(f"{name}: \"project\" must be a name")
    return out


def on_github(api: Api, owner: str, repos: dict) -> list[str]:
    """What GitHub says is wrong: a repository missing, moved or unreadable,
    or one to protect that has no sound protect block."""
    out = []
    for name, roles in repos.items():
        slug = f"{owner}/{name}"
        try:
            found = api("GET", f"repos/{slug}", None) or {}
        except ApiError as e:
            out.append(f"{slug} does not exist" if e.status == 404 else f"{slug}: {e}")
            continue
        # GitHub answers for a renamed or transferred repository's old name.
        if found.get("full_name", slug).lower() != slug.lower():
            out.append(f"{slug} is now {found['full_name']}")
            continue
        if roles["protect"]:
            try:
                protect.remote(api, slug)
            except ValueError as e:
                out.append(f"{slug} has \"protect\" on, but {e}")
    return out


def check(path: Path, api: Api = protect.gh) -> int:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.exit(f"pubkit state: {path}: {e}")
    bad = shape(state) or on_github(api, state["owner"], state["repos"])
    for line in bad:
        print(f"{path}: {line}")
    if bad:
        return 1
    print(f"{path}: {len(state['repos'])} repositories of {state['owner']}, all sound")
    return 0
