"""`pubkit app-secrets`: send what this repository's pubkit.json names to it.

A repository names the values, so pubkit names nothing of anyone's. `github_app`
is one GitHub App's three values, or a list of apps; `secrets` is any others:

    "github_app": {
      "id":              {"from": "some-app-id", "to": "SOME_APP_ID"},
      "installation_id": {"from": "some-app-installation-id", "to": "SOME_APP_INSTALLATION_ID"},
      "key":             {"from": "some-app-key", "to": "SOME_APP_KEY"}
    },
    "secrets": [
      {"from": "some-token", "to": "SOME_TOKEN", "kind": "secret"},
      {"from": "some-user", "to": "SOME_USER", "kind": "variable"},
      {"from": "some-deploy-key", "to": "SOME_DEPLOY_KEY", "kind": "secret", "environment": "some-env"}
    ]

`from` is the secret's name in the Bitwarden project ./with-secrets reads, and
`to` its name on GitHub. An app's id and installation id go as variables and its
key as a secret; `kind` says for the others, and `environment`, if given, names the
GitHub environment one goes in, which must exist. They all go to the repository
`origin` points at and no other. Run it through with-secrets, which puts the
project's secrets in its environment:

    ./with-secrets ./pubkit app-secrets --check   # checks only, sends nothing
    ./with-secrets ./pubkit app-secrets

It writes nothing until every one is there and looks right, so a mistake never
leaves a repository half updated. It prints names and lengths, never a value, and
a value reaches gh on its standard input, never its command line.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

from .protect import origin

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


def run(config: dict, check: bool) -> int:
    if BLOCK not in config and OTHERS not in config:
        print(f"pubkit app-secrets: pubkit.json has no {BLOCK} block or {OTHERS} list")
        return 1
    if bad := config_problems(config):
        print(f"pubkit app-secrets: pubkit.json: {'; '.join(bad)}")
        return 1
    items = plan(config)
    if not items:
        print(f"pubkit app-secrets: pubkit.json's {OTHERS} list is empty")
        return 1
    failed = False
    values = {}
    print("Names and values")
    for item in items:
        name = item["from"]
        value = os.environ.get(env_name(name))
        if value is None:
            print(f"  MISSING  {name}")
            failed = True
        elif (wrong := wrong_value(item["check"], value)) is not None:
            print(f"  BAD      {name} {wrong} ({len(value)} characters)")
            failed = True
        else:
            print(f"  ok       {name} ({len(value)} characters)")
            values[name] = value
    if failed:
        print("Nothing was written. Fix those in Bitwarden, or the names in pubkit.json, and run "
              "this through ./with-secrets, which reads them.")
        return 1
    if check:
        print(f"All {len(items)} look right. --check: nothing was written.")
        return 0
    repo = origin()
    for item in items:
        environment = item.get("environment")
        where = f"{repo}, environment {environment}" if environment else repo
        done = subprocess.run(["gh", item["kind"], "set", item["to"], "-R", repo,
                               *(["--env", environment] if environment else [])],
                              input=values[item["from"]], text=True, capture_output=True, check=False)
        if done.returncode != 0:
            print(f"  FAILED   {item['kind']} {item['to']} in {where}: {done.stderr.strip()}")
            failed = True
        else:
            print(f"  sent     {item['kind']} {item['to']} in {where}")
    if failed:
        print("Fix what gh said and run this again: it only overwrites.")
        return 1
    print(f"Done: {len(items)} values in {repo}.")
    return 0
