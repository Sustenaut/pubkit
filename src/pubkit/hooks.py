"""What installed packages do when pubkit syncs or bumps a repository,
through two entry-point groups, so pubkit imports none of their code:

- `sustenaut.sync`: `check(root, config, check) -> (lines, failed)`, after
  `pubkit sync`'s own work; `--check` fails when one says so. pubdeploy's is
  the deploy block's offline check and the scheduled walk's caller.
- `sustenaut.bump`: `bump(root) -> lines`, after `pubkit bump` has moved
  pubkit's own pins. pubdeploy's moves deploy.providers and the uses of
  pubdeploy's workflows.

A hook that raises is said by its name and the error's type alone, never
its message, and fails `sync --check`. A repository with a deploy block and
no pubdeploy installed cannot be checked, which `--check` says, and fails.
"""

from __future__ import annotations

from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Any

SYNC = "sustenaut.sync"
BUMP = "sustenaut.bump"
PUBDEPLOY = "pubdeploy"


def found(group: str, points: list[EntryPoint] | None = None) -> list[EntryPoint]:
    return sorted(entry_points(group=group) if points is None else points, key=lambda p: (p.name, p.value))


def sync(root: Path, config: dict[str, Any], check: bool,
         points: list[EntryPoint] | None = None) -> tuple[list[str], bool]:
    hooks = found(SYNC, points)
    out, failed = [], False
    if isinstance(config.get("deploy"), dict) and not any(p.name == PUBDEPLOY for p in hooks):
        out.append(f"pubkit: deploy: the deploy block is pubdeploy's to check, and pubdeploy is not installed: "
                   f"./pubkit runs with it, as deploy.providers lists it (`pubkit sync` lists it), or add "
                   f"its wheel to tests/requirements.txt and run `pubkit lock`")
        failed = check
    for point in hooks:
        try:
            lines, bad = point.load()(root, config, check)
        except Exception as e:  # noqa: BLE001 - a hook's failure is said, by its type alone
            out.append(f"pubkit: the {SYNC} hook {point.name} failed ({type(e).__name__})")
            failed = failed or check
            continue
        out += [str(line) for line in lines]
        failed = failed or bool(bad)
    return out, failed


def bump(root: Path, points: list[EntryPoint] | None = None) -> list[str]:
    out = []
    for point in found(BUMP, points):
        try:
            out += [str(line) for line in point.load()(root)]
        except Exception as e:  # noqa: BLE001 - a hook's failure is said, by its type alone
            out.append(f"pubkit: the {BUMP} hook {point.name} failed ({type(e).__name__})")
    return out
