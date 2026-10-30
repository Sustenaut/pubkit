"""`pubkit app-secrets`: send one GitHub App's three values to this repository.

A repository names them in its pubkit.json, so pubkit names nothing of anyone's:

    "github_app": {
      "id":              {"from": "some-app-id", "to": "SOME_APP_ID"},
      "installation_id": {"from": "some-app-installation-id", "to": "SOME_APP_INSTALLATION_ID"},
      "key":             {"from": "some-app-key", "to": "SOME_APP_KEY"}
    }

`from` is the secret's name in the Bitwarden project ./with-secrets reads, and
`to` its name on GitHub: the id and the installation id go as variables, and the
key as a secret, of the repository `origin` points at and no other. Run it through
with-secrets, which puts the project's secrets in its environment:

    ./with-secrets ./pubkit app-secrets --check   # checks only, sends nothing
    ./with-secrets ./pubkit app-secrets

It writes nothing until all three are there and look right, so a mistake never
leaves a repository half updated. It prints names and lengths, never a value, and
a value reaches gh on its standard input, never its command line.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any

from .protect import origin

BLOCK = "github_app"
# Each of the three: how it goes to GitHub, and what its value looks like.
SLOTS = {"id": "variable", "installation_id": "variable", "key": "secret"}
FROM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
TO = re.compile(r"[A-Z_][A-Z0-9_]*")


def problems(block: Any) -> list[str]:
    """What is wrong with a github_app block: one phrase each, none when sound."""
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
    names = [block[s]["to"] for s in SLOTS if isinstance(block.get(s), dict) and "to" in block[s]]
    if all(isinstance(n, str) for n in names) and len(set(names)) != len(names):
        out.append("two of them have the same \"to\"")
    return out


def wrong_value(slot: str, value: str) -> str | None:
    """How a value isn't what its slot needs, without saying what it is."""
    if not value:
        return "is empty"
    if slot != "key" and not (value.isascii() and value.isdigit()):
        return "is not an id: only digits are expected"
    if slot == "key" and not (re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", value)
                              and re.search(r"-----END [A-Z ]*PRIVATE KEY-----", value)):
        return "is not a private key: it needs its BEGIN and END lines"
    return None


def env_name(name: str) -> str:
    """The name with-secrets exports a secret under."""
    return name.upper().replace("-", "_")


def run(config: dict, check: bool) -> int:
    if BLOCK not in config:
        print(f"pubkit app-secrets: pubkit.json has no {BLOCK} block")
        return 1
    if bad := problems(config[BLOCK]):
        print(f"pubkit app-secrets: pubkit.json's {BLOCK} block: {'; '.join(bad)}")
        return 1
    block = config[BLOCK]
    failed = False
    values = {}
    print("Names and values")
    for slot in SLOTS:
        name = block[slot]["from"]
        value = os.environ.get(env_name(name))
        if value is None:
            print(f"  MISSING  {name}")
            failed = True
        elif (wrong := wrong_value(slot, value)) is not None:
            print(f"  BAD      {name} {wrong} ({len(value)} characters)")
            failed = True
        else:
            print(f"  ok       {name} ({len(value)} characters)")
            values[slot] = value
    if failed:
        print("Nothing was written. Fix those in Bitwarden, or the names in pubkit.json, and run "
              "this through ./with-secrets, which reads them.")
        return 1
    if check:
        print("All three look right. --check: nothing was written.")
        return 0
    repo = origin()
    for slot, kind in SLOTS.items():
        to = block[slot]["to"]
        done = subprocess.run(["gh", kind, "set", to, "-R", repo], input=values[slot], text=True,
                              capture_output=True, check=False)
        if done.returncode != 0:
            print(f"  FAILED   {kind} {to} in {repo}: {done.stderr.strip()}")
            failed = True
        else:
            print(f"  sent     {kind} {to} in {repo}")
    if failed:
        print("Fix what gh said and run this again: it only overwrites.")
        return 1
    print(f"Done: three values in {repo}.")
    return 0
