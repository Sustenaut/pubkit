"""`pubkit variables --check | --apply`: keep repositories' GitHub variables to a state file.

A repository's pubkit.json names the variables it reads, never their values, and
may name the workflow that takes them in, to run once one changes:

    "variables": {"names": ["SOME_SETTING", "SOME_MAP"], "deploy": "deploy.yml"}

Their values are in the owner's state file, under each repository, so a change to one
is a pull request there, with its history:

    "some-repo": {"board": true, "protect": true, "govern": false,
                  "variables": {"SOME_SETTING": "a string", "SOME_MAP": {"a": "b"}}}

A string goes to GitHub as it is. Anything else is JSON, kept structured in the state
so a diff shows the entry that changed, and sent compact, in the file's order. A JSON
value matches what GitHub holds when that parses to the same thing, however spaced.

`--check` changes nothing: it prints how each repository's variables differ from the
state, and fails when one does. `--apply` sets those that differ, and then runs the
repository's `deploy` workflow on its default branch, once, if it set any. Neither
touches a variable the state leaves out, nor removes one: dropping a variable is a
deliberate step by hand. Each acts on every repository in the state with variables,
or those named (NAME or OWNER/NAME), or those a repos file lists. It reads through
`gh`, with a token that can read the repositories' pubkit.json and variables, and,
for --apply, write their variables and run their workflows.
"""

from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any

from .protect import Api, ApiError, gh, gh_set, remote_config
from .repos import read

BLOCK = "variables"
NAME = re.compile(r"[A-Z_][A-Z0-9_]*")
WORKFLOW = re.compile(r"[A-Za-z0-9_.-]+\.ya?ml")
PAGE = 30


def name_problem(name: Any) -> str | None:
    if not isinstance(name, str) or not NAME.fullmatch(name) or name.startswith("GITHUB_"):
        return f"{json.dumps(name)} is not a variable's name: upper case letters, digits and _, " \
               f"not starting GITHUB_"
    return None


def block_problems(block: Any) -> list[str]:
    """What is wrong with a pubkit.json's variables block: one phrase each."""
    if not isinstance(block, dict):
        return ['it must be an object, with "names" and maybe "deploy"']
    out = [f"{key} is not a setting; it takes names and deploy"
           for key in sorted(block.keys() - {"names", "deploy"})]
    names = block.get("names")
    if not isinstance(names, list) or not names:
        out.append('"names" must list at least one variable')
    else:
        out += [p for n in names if (p := name_problem(n))]
        if len(set(map(json.dumps, names))) != len(names):
            out.append('"names" lists a variable twice')
    if "deploy" in block and not (isinstance(block["deploy"], str) and WORKFLOW.fullmatch(block["deploy"])):
        out.append('"deploy" must be a workflow\'s file name, such as deploy.yml')
    return out


def value_problems(values: Any) -> list[str]:
    """What is wrong with one repository's variables in a state file: one phrase each."""
    if not isinstance(values, dict):
        return ["its variables must be an object, from each name to its value"]
    out = [p for n in values if (p := name_problem(n))]
    for name, value in values.items():
        if value is None or value == "":
            out.append(f"{name} has no value; GitHub keeps no empty variable")
    return out


def serialise(value: Any) -> str:
    """What GitHub holds for a value from the state."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def same(want: Any, have: str) -> bool:
    if isinstance(want, str):
        return want == have
    try:
        return canonical(json.loads(have)) == canonical(want)
    except ValueError:
        return False


def pretty(value: Any) -> list[str]:
    return json.dumps(value, ensure_ascii=False, indent=2).splitlines()


def difference(name: str, want: Any, have: str | None) -> list[str]:
    """How one variable differs, as lines: a diff of the entries for JSON."""
    if have is None:
        return [f"  {name} is not set; the state has:", *(f"    + {line}" for line in
                                                           pretty(want) if not isinstance(want, str)),
                *([f"    + {want}"] if isinstance(want, str) else [])]
    if isinstance(want, str):
        return [f"  {name}:", f"    - {have}", f"    + {want}"]
    try:
        before = pretty(json.loads(have))
    except ValueError:
        before = [have]
    lines = difflib.unified_diff(before, pretty(want), "on GitHub", "in the state", lineterm="", n=0)
    return [f"  {name}:", *(f"    {line}" for line in lines)]


def live(api: Api, slug: str) -> dict[str, str]:
    """Every variable `slug` holds, by name."""
    out: dict[str, str] = {}
    page = 1
    while True:
        found = api("GET", f"repos/{slug}/actions/variables?per_page={PAGE}&page={page}", None) or {}
        batch = found.get("variables", [])
        out |= {v["name"]: v["value"] for v in batch}
        if len(batch) < PAGE:
            return out
        page += 1


def declared(api: Api, slug: str) -> dict:
    """`slug`'s variables block, from the pubkit.json on its default branch."""
    config = remote_config(api, slug)
    if not isinstance(config, dict) or BLOCK not in config:
        raise ValueError(f"its pubkit.json declares no variables; add "
                         f'"{BLOCK}": {{"names": [...]}} to it')
    if bad := block_problems(config[BLOCK]):
        raise ValueError(f"its pubkit.json's {BLOCK} block: {'; '.join(bad)}")
    return config[BLOCK]


def undeclared(block: dict, values: dict) -> list[str]:
    return [n for n in values if n not in block["names"]]


def chosen(state: dict, names: list[str], repos_file: Path | None) -> list[str]:
    """The repositories to act on, by name within the state's owner."""
    owner = state["owner"]
    if repos_file:
        names = [e["repo"] for e in read(repos_file, "variables")]
    if not names:
        return [n for n, roles in state["repos"].items() if roles.get(BLOCK)]
    out = []
    for name in names:
        own, _, short = name.rpartition("/")
        if own and own.lower() != owner.lower():
            sys.exit(f"pubkit variables: {name} is not one of {owner}'s, as the state is")
        if short not in state["repos"]:
            sys.exit(f"pubkit variables: the state names no repository {short}")
        if state["repos"][short].get(BLOCK):
            out.append(short)
        elif not repos_file:
            sys.exit(f"pubkit variables: the state gives {short} no variables")
    return out


def one(api: Api, slug: str, values: dict, apply: bool, setter) -> tuple[list[str], bool, bool]:
    """Lines for one repository, whether it differs, and whether something failed."""
    try:
        block = declared(api, slug)
    except ValueError as e:
        return [f"{slug}: {e}"], False, True
    if names := undeclared(block, values):
        return [f"{slug}: its pubkit.json doesn't declare {', '.join(names)}, "
                f"which the state gives it"], False, True
    try:
        have = live(api, slug)
    except ApiError as e:
        return [f"{slug}: its variables can't be read ({e})"], False, True
    differs = [n for n, v in values.items() if n not in have or not same(v, have[n])]
    if not differs:
        return [f"{slug}: {len(values)} variables as the state says"], False, False
    lines = [f"{slug}: {len(differs)} of {len(values)} variables differ from the state"]
    for name in differs:
        lines += difference(name, values[name], have.get(name))
    if not apply:
        return lines, True, False
    failed = False
    for name in differs:
        if (refused := setter("variable", name, slug, serialise(values[name]))) is not None:
            lines.append(f"  FAILED   {name}: {refused}")
            failed = True
        else:
            lines.append(f"  set      {name}")
    if workflow := block.get("deploy"):
        try:
            branch = (api("GET", f"repos/{slug}", None) or {}).get("default_branch", "main")
            api("POST", f"repos/{slug}/actions/workflows/{workflow}/dispatches", {"ref": branch})
            lines.append(f"  ran      {workflow} on {branch}")
        except ApiError as e:
            lines.append(f"  FAILED   to run {workflow}: {e}")
            failed = True
    return lines, True, failed


def run(path: Path, names: list[str], apply: bool, repos_file: Path | None = None,
        api: Api = gh, setter=gh_set) -> int:
    from .state import shape  # noqa: PLC0415
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.exit(f"pubkit variables: {path}: {e}")
    if bad := shape(state):
        sys.exit(f"pubkit variables: {path}: {'; '.join(bad)}")
    repos = chosen(state, names, repos_file)
    if not repos:
        print(f"{path}: no repository of {state['owner']} has variables")
        return 0
    differs = failed = False
    for name in repos:
        lines, d, f = one(api, f"{state['owner']}/{name}", state["repos"][name][BLOCK], apply, setter)
        print("\n".join(lines))
        differs, failed = differs or d, failed or f
    return 1 if failed or (differs and not apply) else 0
