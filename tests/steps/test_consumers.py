"""Steps for the guard that keeps consumers' names out of what pubkit ships."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pytest_bdd import given, parsers, scenarios, then, when

scenarios("consumers.feature")

ROOT = Path(__file__).resolve().parents[2]
SHIPPED = ("src/pubkit", "actions", "terraform", ".github")

NAME = r"[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9_.-]*?"
END = r"(?:\.git)?(?=[/@\"'\s,})]|$)"
# The ways a file names a repository rather than a path.
NAMED = [re.compile(rf"github\.com[:/]({NAME}){END}"),
         re.compile(rf"\buses:\s*({NAME}){END}"),
         re.compile(rf"--repo[ =]({NAME}){END}"),
         re.compile(rf"\blatest ({NAME}){END}"),
         re.compile(rf"\"repo\":\s*\"({NAME})\""),
         re.compile(rf"\bREPOS?=({NAME}){END}"),
         re.compile(rf"(?i)\b((?:schryer|sustenaut)/[A-Za-z0-9][A-Za-z0-9_.-]*?){END}")]

# pubkit itself, its release tool, and what the examples use.
ALWAYS = {"pubkit", "pubrel"}
NEUTRAL = "example-org"
# The owners of the actions and of the registry pubkit's own files depend on.
DEPENDENCIES = {"actions", "bitwarden", "docker", "google", "hashicorp", "pypa", "rust-lang", "SonarSource",
                "tailscale"}

# pub is a pubkit tool, like pubrel, so `pubkit new` names where it comes from.
# pubkit's own CI, which it does not ship to anyone, runs the integrations scan.
TOOLS = {("src/pubkit/new.py", "Sustenaut/publet"),
         (".github/workflows/integrations.yml", "Sustenaut/integrations")}
# TODO: shrink this list. Each goes with the deprecated pub-version input, in
# the breaking release that removes it, or with the release control plane
# work that moves the nightly and its lists out of pubkit.
TODO = {(".github/workflows/rust-ci.yml", "Sustenaut/publet"),
        (".github/workflows/job-rust-functional.yml", "Sustenaut/publet"),
        (".github/workflows/nightly.yml", "Sustenaut/publet")}
ALLOWED = TOOLS | TODO


def named_in(text: str) -> set[str]:
    """Every owner/repo `text` names that no rule allows anywhere."""
    found = {m.group(1) for pattern in NAMED for m in pattern.finditer(text)}
    return {name for name in found
            if name.split("/")[1] not in ALWAYS
            and name.split("/")[0] != NEUTRAL
            and name.split("/")[0] not in DEPENDENCIES}


def shipped_files():
    """What git tracks there, so nothing a build leaves behind counts."""
    listed = subprocess.run(["git", "ls-files", "--", *SHIPPED], cwd=ROOT, check=True,
                            capture_output=True, text=True).stdout
    return [ROOT / name for name in listed.splitlines()]


@when("I scan what pubkit ships for owner/repo names", target_fixture="found")
def scan_shipped():
    found = set()
    for path in shipped_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        found |= {(path.relative_to(ROOT).as_posix(), name) for name in named_in(text)}
    return found


@then("it finds none but those the guard allows")
def only_allowed(found):
    assert found <= ALLOWED, sorted(found - ALLOWED)


@then("every allowance is still used")
def allowances_used(found):
    assert ALLOWED <= found, sorted(ALLOWED - found)


@given(parsers.parse('a shipped file reads "{text}"'), target_fixture="shipped")
def shipped_text(text):
    return text


@when("I scan it for owner/repo names", target_fixture="names")
def scan_text(shipped):
    return named_in(shipped)


@then(parsers.parse('it finds "{name}"'))
def finds(names, name):
    assert names == {name}


@then("it finds nothing")
def finds_nothing(names):
    assert names == set()
