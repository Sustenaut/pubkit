"""How a workflow pins the reusable workflows pubrules writes calls to: by
the full commit of the release's tag, with the version in a comment,

    uses: <owner>/pubrules/.github/workflows/job-rust-test.yml@<commit> # v0.15.2

so the pin cannot move with the tag (SonarQube Cloud's S7637) and still
reads as a version. Every reader here also takes the tag alone, `@v0.15.2`,
as workflows written before pinned.

pubrules' own commit is in its wheel: the release CI writes the tag's
commit into the package, as `release-commit`, before it builds the wheel,
so `pubrules sync` needs no network for it. A source tree has none, and pins
by tag. pubrel's is the one a workflow already pins at that version, or
else read once from pubrel's tags with `git ls-remote`; when that cannot be
read either, the pin is by tag, with a warning.

Whose pubrules and pubrel these are is OWNER, set here and nowhere else; a
repository's pubrules.json can name another as `workflows_owner`. Every
writer pins to that owner, and every reader takes any owner, so a
workflow still pinned to an earlier one reads as before.
"""

from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Any

# The owner of pubrules' and pubrel's repositories, whose workflows a
# repository calls, since their transfer from schryer.
OWNER = "Sustenaut"
OWNER_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
# Any owner, as readers take it, before `/pubrules/` or `/pubrel/`.
ANY_OWNER = r"(?P<owner>[A-Za-z0-9][A-Za-z0-9-]*)/"

COMMIT = re.compile(r"[0-9a-f]{40}")
# What follows `@` up to the version: `v`, or a commit and `# v`.
AT = r"(?:v|(?P<commit>[0-9a-f]{40})[ \t]+#[ \t]*v)"
VERSION = r"(?P<version>[0-9][^\s'\"]*)"

RELEASE_PIN = re.compile(ANY_OWNER + r"(?P<path>pubrel/\.github/workflows/release\.yml@)"
                         + AT + VERSION)
# Any use of pubrules at a release: one of its actions (actions/<name>, such
# as cache) or of its reusable workflows (.github/workflows/<name>,
# such as service-ci.yml or job-sonar.yml), by commit with the version in a
# comment, or by tag. It runs that pubrules' code, so it moves with the
# pubrules the lock pins, written by sync or by hand.
PUBRULES_PIN = re.compile(ANY_OWNER + r"(?P<path>pubrules/(?P<name>actions/[\w.-]+|"
                        r"\.github/workflows/[\w.-]+\.ya?ml)@)" + AT + VERSION)
# Any use of pubrules' actions or reusable workflows, versioned or not.
PUBRULES_USE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/pubrules/(?:actions/[\w.-]+|"
                        r"\.github/workflows/[\w.-]+\.ya?ml)@[^\s'\"]+")


# What this pubrules ships for other repositories to use by commit: its
# actions (actions/<name>), its reusable workflows (.github/workflows/<name>)
# and its Terraform modules (terraform/<name>). The installed wheel carries
# none of these files, so the names are listed here; a test holds the list
# to the repository's tree. A pin of a name not listed is to a pubrules that
# lacks it, which `pins` never moves to and `sync --check` refuses.
SHIPPED = {
    "actions": ("apt-install", "cache", "pr-label", "retry", "terraform-init"),
    "workflows": (
        "job-node-audit.yml", "job-node-browser.yml", "job-node-test.yml", "job-osv.yml",
        "job-python-audit.yml", "job-python-browser.yml", "job-python-functional.yml",
        "job-review.yml", "job-rust-audit.yml", "job-rust-cross.yml", "job-rust-functional.yml",
        "job-rust-fuzz.yml", "job-rust-gates.yml", "job-rust-msrv.yml", "job-rust-test.yml",
        "job-rust-toolchains.yml", "job-service-checks.yml", "job-service-terraform.yml",
        "job-service-tests.yml", "job-sonar.yml", "job-tested.yml", "service-needs.yml", "node-ci.yml",
        "python-ci.yml", "rust-ci.yml", "service-ci.yml",
    ),
    "terraform": (),
}

# A Terraform module source in another repository at a release: a
# `source` naming github.com, the owner and the repository, then `//` and
# the path, with `?ref=`: by tag (`?ref=vX.Y.Z`), or by commit with the
# version in a comment after the closing quote.
TF_SOURCE = re.compile(
    r'(?P<lead>source\s*=\s*)"github\.com/' + ANY_OWNER + r'(?P<repo>[A-Za-z0-9_.-]+)//(?P<path>[^"?\s]+)'
    r'\?ref=(?:v(?P<tag>[0-9][^"\s]*)|(?P<commit>[0-9a-f]{40}))"(?:[ \t]*#[ \t]*v(?P<version>[0-9][^\s]*))?')


def shipped(name: str) -> bool:
    """Whether a pubrules pin's name (actions/<a> or .github/workflows/<w>) is one this pubrules ships."""
    kind, _, base = name.rpartition("/")
    return base in SHIPPED["actions" if kind == "actions" else "workflows"]


def tf_files(root: Path) -> list[Path]:
    """The repository's own .tf files, outside Terraform's working directories."""
    return sorted(p for p in root.rglob("*.tf")
                  if not any(part.startswith(".") for part in p.relative_to(root).parts[:-1]))


def stale_pins(root: Path, version: str, commit: str | None) -> tuple[list[str], list[str]]:
    """(the uses of pubrules' actions and reusable workflows in the
    repository's workflows that pin another pubrules than `version`, or
    another commit than `commit`; those that name no version, which nothing
    here moves)."""
    stale, unversioned = [], []
    for path in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        text, rel = path.read_text(encoding="utf-8"), path.relative_to(root).as_posix()
        pinned = {m.start() for m in PUBRULES_PIN.finditer(text)}
        for m in PUBRULES_PIN.finditer(text):
            name = m["name"].rsplit("/", 1)[-1]
            if not shipped(m["name"]):
                stale.append(f"{rel}: pubrules {version} has no {m['name']}; it moved out of "
                             f"pubrules (pubrules' README says where), so pin it from there")
            elif m["version"] != version:
                stale.append(f"{rel}: {name}@v{m['version']} is not pubrules {version}, which "
                             f"tests/requirements.lock pins; run `pubrules bump`, or pin it "
                             f"@{ref(version, commit)}")
            elif commit and m["commit"] and m["commit"] != commit:
                stale.append(f"{rel}: {name}@{m['commit']} is not the commit of pubrules "
                             f"{version}'s tag; pin it @{ref(version, commit)}")
        unversioned += [f"{rel}: {m[0]}" for m in PUBRULES_USE.finditer(text)
                        if m.start() not in pinned]
    for path in tf_files(root):
        rel = path.relative_to(root).as_posix()
        for m in TF_SOURCE.finditer(path.read_text(encoding="utf-8")):
            module = m["path"].split("/")
            if m["repo"].lower() == "pubrules" and not (
                    len(module) == 2 and module[0] == "terraform" and module[1] in SHIPPED["terraform"]):
                stale.append(f"{rel}: pubrules {version} has no {m['path']}; it moved out of pubrules "
                             f"(pubrules' README says where), so take the module from there")
    return stale, unversioned


def owner(config: dict[str, Any] | None) -> str:
    """The owner pubrules.json's `workflows_owner` names, or OWNER."""
    given = (config or {}).get("workflows_owner", OWNER)
    if not isinstance(given, str) or not OWNER_NAME.fullmatch(given):
        sys.exit(f"pubrules: pubrules.json's workflows_owner must be a GitHub owner, such as "
                 f'"{OWNER}", not {given!r}')
    return given


def owner_of(root: Path) -> str:
    """The owner for the repository at `root`, from its pubrules.json."""
    path = root / "pubrules.json"
    return owner(json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None)


def ref(version: str, commit: str | None) -> str:
    """What follows `@` in a pin of `version`: its commit, with the version
    in a comment, or the tag when the commit is unknown."""
    return f"{commit} # v{version}" if commit else f"v{version}"


def repin(pattern: re.Pattern[str], text: str, owner: str, version: str,
          commit: str | None, keep: Callable[[re.Match[str]], bool] | None = None) -> str:
    """`text` with every pin `pattern` matches (its `path`, after the
    owner, through `@`) moved to `owner`'s, at `version`; one `keep` says
    stays as it is."""
    return pattern.sub(lambda m: m.group(0) if keep and keep(m)
                       else f"{owner}/{m['path']}{ref(version, commit)}", text)


def release_commit() -> str | None:
    """The commit of this pubrules' release tag, from the wheel; None from a
    source tree. PUBRULES_RELEASE_COMMIT, which tests set, stands in for it."""
    given = os.environ.get("PUBRULES_RELEASE_COMMIT")
    if given is None:
        try:
            given = (resources.files("pubrules") / "release-commit").read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
    given = given.strip()
    if not given:
        return None
    if not COMMIT.fullmatch(given):
        sys.exit(f"pubrules: its release commit must be 40 hexadecimal digits, not {given!r}")
    return given


def pubrel_commit(version: str, workflows: str, owner: str) -> str | None:
    """The commit of pubrel's tag v`version`: already pinned in
    `workflows`, or read from the tags of `owner`'s pubrel
    (PUBRULES_PUBREL_REMOTE, which tests set, in place of it). A transfer
    keeps the commits, so one pinned under another owner serves."""
    for m in RELEASE_PIN.finditer(workflows):
        if m["commit"] and m["version"] == version:
            return m["commit"]
    remote = os.environ.get("PUBRULES_PUBREL_REMOTE", f"https://github.com/{owner}/pubrel")
    return tagged(remote, f"v{version}")


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
    print(f"pubrules: could not read the commit of {tag} from {remote}; pinning it by tag",
          file=sys.stderr)
    return None
