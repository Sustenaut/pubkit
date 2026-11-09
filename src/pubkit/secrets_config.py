"""What pubkit reads about secrets, never a value: pubkit.json's `github_app`
and `secrets` blocks, which name the values a repository needs and where they
go on GitHub, and its `signing_keys` block, which names each signing key and
where it is backed up. `pubkit sync --check` validates them, and the deploy
walk implies its github.* needs from them (deploy/implied.py).

The commands that send, write or back up those values are pubsec's (`./pubsec
app-secrets`, `github-app create`, `signing-key backup`), which keep their
own copy of what they need of this: env_name, wrong_value and plan.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

BLOCK = "github_app"
OTHERS = "secrets"
KINDS = {"secret", "variable"}
# Each of the three: how it goes to GitHub, and what its value looks like.
SLOTS = {"id": "variable", "installation_id": "variable", "key": "secret"}
FROM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
TO = re.compile(r"[A-Z_][A-Z0-9_]*")
ENVIRONMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def app_problems(block: Any) -> list[str]:
    """What is wrong with one app's three values: one phrase each."""
    if not isinstance(block, dict):
        return ["it must be an object, with id, installation_id and key"]
    out = [f"{key} is not a setting; it takes id, installation_id and key"
           for key in sorted(block.keys() - SLOTS.keys())]
    for slot in SLOTS:
        given = block.get(slot)
        if not isinstance(given, dict) or set(given) != {"from", "to"}:
            out.append(f"{slot} needs \"from\", its Bitwarden name, and \"to\", its GitHub name")
            continue
        if not isinstance(given["from"], str) or not FROM.fullmatch(given["from"]):
            out.append(f"{slot}'s \"from\" is not a secret's name")
        name = given["to"]
        if not isinstance(name, str) or not TO.fullmatch(name) or name.startswith("GITHUB_"):
            out.append(f"{slot}'s \"to\" must be upper case letters, digits and _, "
                       f"not starting GITHUB_")
    return out


def problems(block: Any) -> list[str]:
    """What is wrong with a github_app block, one app or a list of them."""
    if isinstance(block, list) and block:
        return [problem for app in block for problem in app_problems(app)]
    if isinstance(block, list):
        return ["it lists no apps"]
    return app_problems(block)


def other_problems(entries: Any) -> list[str]:
    """What is wrong with a secrets list: one phrase each, none when sound."""
    if not isinstance(entries, list):
        return ["it must be a list of from, to and kind"]
    out = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {"environment"} != {"from", "to", "kind"}:
            out.append(f"{json.dumps(entry)} needs \"from\", \"to\" and \"kind\", and may have \"environment\"")
            continue
        label = entry["to"] if isinstance(entry["to"], str) else json.dumps(entry["to"])
        if not isinstance(entry["from"], str) or not FROM.fullmatch(entry["from"]):
            out.append(f"{label}'s \"from\" is not a secret's name")
        if not isinstance(entry["to"], str) or not TO.fullmatch(entry["to"]) or entry["to"].startswith("GITHUB_"):
            out.append(f"{label}'s \"to\" must be upper case letters, digits and _, not starting GITHUB_")
        if not isinstance(entry["kind"], str) or entry["kind"] not in KINDS:
            out.append(f"{label}'s \"kind\" must be secret or variable")
        if "environment" in entry and not (isinstance(entry["environment"], str) and ENVIRONMENT.fullmatch(entry["environment"])):
            out.append(f"{label}'s \"environment\" is not an environment's name")
    return out


def plan(config: dict) -> list[dict]:
    """Everything the config names: from, to, kind, and what its value must look like."""
    block = config.get(BLOCK, [])
    apps = block if isinstance(block, list) else [block]
    out = [{"from": app[slot]["from"], "to": app[slot]["to"], "kind": kind, "check": slot}
           for app in apps for slot, kind in SLOTS.items()]
    return out + [{**entry, "check": None} for entry in config.get(OTHERS, [])]


def config_problems(config: dict) -> list[str]:
    """What is wrong with both blocks, including one name used twice."""
    out = []
    if BLOCK in config:
        out += [f"{BLOCK}: {p}" for p in problems(config[BLOCK])]
    if OTHERS in config:
        out += [f"{OTHERS}: {p}" for p in other_problems(config[OTHERS])]
    if out:
        return out
    names = [(item["kind"], item["to"], item.get("environment", "")) for item in plan(config)]
    return [f"two of them have the same \"to\", {to}" for kind, to, _ in sorted(set(names)) if names.count((kind, to, _)) > 1]


def wrong_value(slot: str | None, value: str) -> str | None:
    """How a value isn't what it should be, without saying what it is."""
    if not value:
        return "is empty"
    if slot in ("id", "installation_id") and not (value.isascii() and value.isdigit()):
        return "is not an id: only digits are expected"
    if slot == "key" and not (re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", value)
                              and re.search(r"-----END [A-Z ]*PRIVATE KEY-----", value)):
        return "is not a private key: it needs its BEGIN and END lines"
    return None


def env_name(name: str) -> str:
    """The name with-secrets exports a secret under."""
    return name.upper().replace("-", "_")


SIGNING_KEYS = "signing_keys"
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def signing_key_problems(block: object) -> list[str]:
    """What is wrong with a pubkit.json `signing_keys` block: one phrase each."""
    if block is None:
        return []
    if not isinstance(block, list):
        return [f"{SIGNING_KEYS} must be a list"]
    out: list[str] = []
    names: set[tuple[str, str]] = set()
    for i, entry in enumerate(block):
        where = f"{SIGNING_KEYS}[{i}]"
        if not isinstance(entry, dict):
            out.append(f"{where} must be an object")
            continue
        out += [f"{where}: {key} is not a setting" for key in sorted(entry.keys() - {"path", "bitwarden"})]
        path = entry.get("path")
        if not (isinstance(path, str) and path.strip()):
            out.append(f"{where} needs a path")
        elif Path(path).is_absolute():
            out.append(f"{where}: path is from the repository's root, or starts with ~/: {path}")
        elif not path.startswith("~/") and ".." in Path(path).parts:
            out.append(f"{where}: path leaves the repository: {path}")
        bitwarden = entry.get("bitwarden")
        if not isinstance(bitwarden, dict):
            out.append(f"{where} needs bitwarden, with a project and a name")
            continue
        out += [f"{where}: bitwarden.{key} is not a setting" for key in sorted(bitwarden.keys() - {"project", "name"})]
        project, name = bitwarden.get("project"), bitwarden.get("name")
        if isinstance(project, dict):
            out += [f"{where}: bitwarden.project.{key} is not a setting"
                    for key in sorted(project.keys() - {"id", "name"})]
            if not (isinstance(project.get("id"), str) and UUID.fullmatch(project["id"])):
                out.append(f"{where}: bitwarden.project.id must be the project's id, a UUID")
            if "name" in project and not (isinstance(project["name"], str) and project["name"].strip()):
                out.append(f"{where}: bitwarden.project.name, if given, is the project's name")
            project = project.get("id")
        elif not (isinstance(project, str) and project.strip()):
            out.append(f"{where} needs bitwarden.project: {{\"id\": …, \"name\": …}}, or the project's id or name")
        if not (isinstance(name, str) and NAME.fullmatch(name)):
            out.append(f"{where}: bitwarden.name must be a letter, then letters, digits, _ and -")
        elif (project, name) in names:
            out.append(f"{where}: {name} is declared twice")
        names.add((project, name))
    return out
