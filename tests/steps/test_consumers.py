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
# The owners of the actions, the tools (actionlint) and the registry pubkit's own files depend on.
DEPENDENCIES = {"actions", "astral-sh", "bitwarden", "docker", "google", "google-github-actions", "hashicorp", "pypa",
                "rhysd", "rust-lang", "SonarSource", "tailscale"}

# pub is a pubkit tool, like pubrel, so `pubkit new` names where it comes from.
# pubkit's own CI, which it does not ship to anyone, runs the integrations scan,
# and its nightly posts to Slack through the integrations command.
TOOLS = {("src/pubkit/new.py", "Sustenaut/publet"),
         (".github/workflows/integrations.yml", "Sustenaut/integrations"),
         (".github/nightly/update.sh", "Sustenaut/integrations")}
ALLOWED = TOOLS


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


# pubkit deploy: its package, its tests and its example service. They may name
# example hosts, the tools' and registries' hosts, pubkit itself, and the names
# pubkit's deploy workflow and the server's program already use; any other
# host, and any other mention of the owner, is a consumer's.
DEPLOY = ("src/pubkit/deploy", "tests/fixtures/deploy-app", "tests/features/deploy-*.feature",
          "tests/steps/deploy_*.py", "tests/steps/test_deploy_*.py")
# A host, not a module path such as .types.net.
HOST = re.compile(r"(?<![.\w])(?:[a-z0-9-]+\.)+(?:eu|com|org|net|io|dev|app|cloud)\b")
EXAMPLE_HOSTS = re.compile(r"(?:^|\.)example\.(?:eu|com|org|net)$")
TOOL_HOSTS = {"github.com", "json-schema.org", "quay.io", "ghcr.io"}
TOOL_DOMAINS = (".k8s.io",)  # Kubernetes' API groups
OWNER = re.compile(r"(?i)\b(sustenaut|schryer)\b[-.\w]*")
OWNER_NAMES = {"sustenaut.needs", "sustenaut-app", "Sustenaut/pubkit"}


def consumer_names(text: str) -> set[str]:
    """Every host and mention of the owner in `text` that is not an example's or a tool's."""
    found = {h for h in HOST.findall(text)
             if not EXAMPLE_HOSTS.search(h) and h not in TOOL_HOSTS and not h.endswith(TOOL_DOMAINS)}
    for m in OWNER.finditer(text):
        word = m.group(0)
        if word.upper().startswith("SUSTENAUT_") or word in OWNER_NAMES or text[m.start():].startswith("Sustenaut/pubkit"):
            continue
        found.add(m.group(1))
    return found


@when("I scan pubkit deploy's files for consumers' names", target_fixture="names")
def scan_deploy():
    # What git tracks or would, so bytecode and other ignored files a run leaves behind do not count.
    listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", *DEPLOY],
                            cwd=ROOT, check=True, capture_output=True, text=True).stdout.split()
    assert any(name.startswith("src/pubkit/deploy/") for name in listed), listed
    return {(name, found) for name in listed
            for found in consumer_names((ROOT / name).read_text(encoding="utf-8", errors="ignore"))}


@then("it finds none")
def finds_none(names):
    assert names == set(), sorted(names)


@given(parsers.parse('a deploy file reads "{text}"'), target_fixture="shipped")
def deploy_text(text):
    return text


@when("I scan it for consumers' names", target_fixture="names")
def scan_deploy_text(shipped):
    return consumer_names(shipped)
