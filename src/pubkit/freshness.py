"""`pubkit bump`: keep a pub* repository on the latest pubkit, so none sits on
an old standard unnoticed. (a control plane's reusable workflow reports which pubkit each
repository pins; `pinned` here is what it, and the version guard, read.)

`pubkit bump`, run in a repository, moves it to this pubkit: the managed
files (`pubkit sync`), the lock (`pubkit lock`), and every workflow's pin of
pubkit's reusable workflows (rust-ci.yml, service-ci.yml...) and actions
(cache), to the commit of this pubkit's tag (refs.py). The nightly
workflow does this, and more, through `pubkit nightly` (nightly.py).
"""

from __future__ import annotations

import re
from pathlib import Path

from . import __version__
from .refs import (ANY_OWNER, AT, PUBKIT_PIN, VERSION, owner_of, ref, release_commit, repin,
                   stale_pins)

PIN = re.compile(r"/download/v([0-9][^/]*)/pubkit-")
WORKFLOW_PIN = re.compile(ANY_OWNER + r"(?P<path>pubkit/\.github/workflows/rust-ci\.yml@)"
                          + AT + VERSION)


def pinned(repo: Path) -> str | None:
    """The pubkit version a repository's lock pins."""
    lock = repo / "tests" / "requirements.lock"
    if not lock.is_file():
        return None
    m = PIN.search(lock.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def bump(root: Path) -> int:
    """Move this repository to this pubkit: managed files, lock, workflow pins."""
    from .cli import lock, sync  # noqa: PLC0415

    # What installed packages move (pubdeploy's: deploy.providers, and the
    # uses of its workflows), before sync writes the launchers from them.
    from .hooks import bump as bump_hooks  # noqa: PLC0415
    for line in bump_hooks(root):
        print(line)
    sync(root, check=False)
    lock(root)
    commit, calls = release_commit(), owner_of(root)
    for path in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        new = repin(PUBKIT_PIN, text, calls, __version__, commit)
        if new != text:
            path.write_text(new, encoding="utf-8")
            print(f"updated  {path.relative_to(root)} (pubkit@{ref(__version__, commit)})")
    for use in stale_pins(root, __version__, commit)[1]:
        print(f"left     {use}: it names no pubkit version; pin it "
              f"@{ref(__version__, commit)} for `pubkit bump` to move it")
    print(f"bumped to pubkit {__version__}; run `make check`, record nothing (no code "
          f"changed), and open the pull request")
    return 0
