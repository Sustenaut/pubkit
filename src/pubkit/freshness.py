"""`pubkit freshness` and `pubkit bump`: keep every pub* repository on the
latest pubkit, so none sits on an old standard unnoticed.

`pubkit freshness DIR...` reports, for each checked-out repository, the
pubkit its lock pins, whether it is this one, whether it conforms to this
pubkit's standard, and its open exceptions: one Markdown table, also
appended to the GitHub step summary when there is one. It reads; it changes
nothing.

`pubkit bump`, run in a repository, moves it to this pubkit: the managed
files (`pubkit sync`), the lock (`pubkit lock`), and every workflow's pin of
`rust-ci.yml`, to the commit of this pubkit's tag (refs.py). The nightly
workflow does this, and more, through `pubkit nightly` (nightly.py).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import __version__
from .refs import ANY_OWNER, AT, VERSION, owner_of, ref, release_commit, repin

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


def row(repo: Path) -> dict:
    from .conform import run as conform  # noqa: PLC0415

    config_path = repo / "pubkit.json"
    out = {"repo": repo.name, "pinned": pinned(repo) or "-", "latest": __version__}
    if not config_path.is_file():
        return {**out, "conforms": "no pubkit.json", "exceptions": []}
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("kind") != "rust":
        return {**out, "conforms": "not Rust", "exceptions": []}
    try:
        lines, problems = conform(repo, config)
    except Exception as e:  # noqa: BLE001 - report every repository, whatever fails
        return {**out, "conforms": f"could not check: {e}", "exceptions": []}
    exceptions = [line.removeprefix("open exception: ") for line in lines
                  if line.startswith("open exception: ")]
    # The workflow pin is checked against the pinned pubkit by `sync --check`
    # in the repository itself; here every rule is judged by the latest.
    problems = [p for p in problems if not p.startswith(".github/workflows/")]
    verdict = "yes" if not problems else f"no: {len(problems)} problem(s): " + "; ".join(problems)
    return {**out, "conforms": verdict, "exceptions": exceptions}


def table(rows: list[dict]) -> str:
    lines = [f"Every pub* repository against pubkit {__version__}.", "",
             "| Repository | Pins pubkit | Current | Conforms to the latest standard | Open exceptions |",
             "|---|---|---|---|---|"]
    for r in rows:
        current = "yes" if r["pinned"] == r["latest"] else "**no**"
        exceptions = "<br>".join(r["exceptions"]) or "-"
        lines.append(f"| {r['repo']} | {r['pinned']} | {current} | {r['conforms']} | {exceptions} |")
    return "\n".join(lines) + "\n"


def freshness(dirs: list[Path], as_json: bool) -> int:
    rows = [row(d.resolve()) for d in dirs]
    text = json.dumps(rows, indent=2) + "\n" if as_json else table(rows)
    print(text, end="")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and not as_json:
        with open(summary, "a", encoding="utf-8") as out:
            out.write(text)
    return 0


def bump(root: Path) -> int:
    """Move this repository to this pubkit: managed files, lock, workflow pins."""
    from .cli import lock, sync  # noqa: PLC0415

    sync(root, check=False)
    lock(root)
    commit, calls = release_commit(), owner_of(root)
    for path in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        new = repin(WORKFLOW_PIN, text, calls, __version__, commit)
        if new != text:
            path.write_text(new, encoding="utf-8")
            print(f"updated  {path.relative_to(root)} (rust-ci.yml@{ref(__version__, commit)})")
    print(f"bumped to pubkit {__version__}; run `make check`, record nothing (no code "
          f"changed), and open the pull request")
    return 0
