"""`pubrules bump`: keep a pub* repository on the latest pubrules, so none sits on
an old standard unnoticed. (a control plane's reusable workflow reports which pubrules each
repository pins; `pinned` here is what it, and the version guard, read.)

`pubrules bump`, run in a repository, moves it to this pubrules: the managed
files (`pubrules sync`), the lock (`pubrules lock`), and every workflow's pin of
pubrules' reusable workflows (rust-ci.yml, service-ci.yml...) and actions
(cache), to the commit of this pubrules' tag (refs.py). The nightly
workflow does this, and more, through `pubrules nightly` (nightly.py).
"""

from __future__ import annotations

import re
from pathlib import Path

from . import __version__
from .refs import (ANY_OWNER, AT, PUBRULES_PIN, VERSION, owner_of, ref, release_commit, repin,
                   stale_pins)

# pubrules's wheel, or pubkit's, which pubrules was called before 0.43.0.
PIN = re.compile(r"/download/v([0-9][^/]*)/(pubrules|pubkit)-")
WORKFLOW_PIN = re.compile(ANY_OWNER + r"(?P<path>pubrules/\.github/workflows/rust-ci\.yml@)"
                          + AT + VERSION)


def pinned_package(repo: Path) -> tuple[str, str] | None:
    """(the package, its version) a repository's lock pins: pubrules, or
    pubkit for a repository not yet moved past the rename."""
    lock = repo / "tests" / "requirements.lock"
    if not lock.is_file():
        return None
    m = PIN.search(lock.read_text(encoding="utf-8"))
    return (m.group(2), m.group(1)) if m else None


def pinned(repo: Path) -> str | None:
    """The pubrules version a repository's lock pins (pubkit's, before the rename)."""
    found = pinned_package(repo)
    return found[1] if found else None


def bump(root: Path) -> int:
    """Move this repository to this pubrules: managed files, lock, workflow pins."""
    from .cli import lock, sync  # noqa: PLC0415

    # What installed packages move (pubdeploy's: deploy.providers, and the
    # uses of its workflows), before sync writes the launchers from them.
    from .hooks import bump as bump_hooks  # noqa: PLC0415
    for line in bump_hooks(root):
        print(line)
    before = pinned_package(root)
    sync(root, check=False)
    lock(root)
    # A rule this pubrules brings that the repository fails is waived, for a
    # while, by the move that brings it.
    from .gherkin import grandfather  # noqa: PLC0415
    for line in grandfather(root, *(before or (None, None))):
        print(line)
    commit, calls = release_commit(), owner_of(root)
    for path in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        new = repin(PUBRULES_PIN, text, calls, __version__, commit)
        if new != text:
            path.write_text(new, encoding="utf-8")
            print(f"updated  {path.relative_to(root)} (pubrules@{ref(__version__, commit)})")
    for use in stale_pins(root, __version__, commit)[1]:
        print(f"left     {use}: it names no pubrules version; pin it "
              f"@{ref(__version__, commit)} for `pubrules bump` to move it")
    print(f"bumped to pubrules {__version__}; run `make check`, record nothing (no code "
          f"changed), and open the pull request")
    return 0
