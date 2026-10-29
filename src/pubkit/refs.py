"""How a workflow pins the reusable workflows pubkit writes calls to: by
the full commit of the release's tag, with the version in a comment,

    uses: schryer/pubkit/.github/workflows/job-rust-test.yml@<commit> # v0.15.2

so the pin cannot move with the tag (SonarQube Cloud's S7637) and still
reads as a version. Every reader here also takes the tag alone, `@v0.15.2`,
as workflows written before pinned.

pubkit's own commit is in its wheel: the release CI writes the tag's
commit into the package, as `release-commit`, before it builds the wheel,
so `pubkit sync` needs no network for it. A source tree has none, and pins
by tag. pubrel's is the one recorded beside the pubrel pubkit pins
(new.py), or the one a workflow already pins at that version, or else
read once from pubrel's tags with `git ls-remote`; when that cannot be
read either, the pin is by tag, with a warning.
"""

from __future__ import annotations

import functools
import os
import re
import subprocess
import sys
from importlib import resources

COMMIT = re.compile(r"[0-9a-f]{40}")
# What follows `@` up to the version: `v`, or a commit and `# v`.
AT = r"(?:v|(?P<commit>[0-9a-f]{40})[ \t]+#[ \t]*v)"
VERSION = r"(?P<version>[0-9][^\s'\"]*)"

RELEASE_PIN = re.compile(r"(schryer/pubrel/\.github/workflows/release\.yml@)" + AT + VERSION)
PUBREL = "https://github.com/schryer/pubrel"


def ref(version: str, commit: str | None) -> str:
    """What follows `@` in a pin of `version`: its commit, with the version
    in a comment, or the tag when the commit is unknown."""
    return f"{commit} # v{version}" if commit else f"v{version}"


def repin(pattern: re.Pattern[str], text: str, version: str, commit: str | None) -> str:
    """`text` with every pin `pattern` matches (the path through `@` its
    first group) at `version`."""
    return pattern.sub(lambda m: m.group(1) + ref(version, commit), text)


def release_commit() -> str | None:
    """The commit of this pubkit's release tag, from the wheel; None from a
    source tree. PUBKIT_RELEASE_COMMIT, which tests set, stands in for it."""
    given = os.environ.get("PUBKIT_RELEASE_COMMIT")
    if given is None:
        try:
            given = (resources.files("pubkit") / "release-commit").read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
    given = given.strip()
    if not given:
        return None
    if not COMMIT.fullmatch(given):
        sys.exit(f"pubkit: its release commit must be 40 hexadecimal digits, not {given!r}")
    return given


def pubrel_commit(version: str, workflows: str) -> str | None:
    """The commit of pubrel's tag v`version`: recorded, already pinned in
    `workflows`, or read from pubrel's tags (PUBKIT_PUBREL_REMOTE, which
    tests set, in place of pubrel's repository)."""
    from .new import PUBREL_COMMIT, PUBREL_PIN  # noqa: PLC0415

    if version == PUBREL_PIN:
        return PUBREL_COMMIT
    for m in RELEASE_PIN.finditer(workflows):
        if m["commit"] and m["version"] == version:
            return m["commit"]
    return tagged(os.environ.get("PUBKIT_PUBREL_REMOTE", PUBREL), f"v{version}")


@functools.cache
def tagged(remote: str, tag: str) -> str | None:
    """The commit `tag` names in `remote`, peeled; None, with a warning,
    when it cannot be read."""
    try:
        out = subprocess.run(["git", "ls-remote", "--tags", remote, f"refs/tags/{tag}",
                              f"refs/tags/{tag}^{{}}"], capture_output=True, text=True,
                             timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        out = None
    found = dict(reversed(line.split("\t", 1)) for line in out.stdout.splitlines()
                 if "\t" in line) if out and out.returncode == 0 else {}
    commit = found.get(f"refs/tags/{tag}^{{}}") or found.get(f"refs/tags/{tag}")
    if commit and COMMIT.fullmatch(commit):
        return commit
    print(f"pubkit: could not read the commit of {tag} from {remote}; pinning it by tag",
          file=sys.stderr)
    return None
