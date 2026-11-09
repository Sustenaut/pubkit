"""The variables block of a repository's pubkit.json: the names of the GitHub
variables it reads, never their values, and maybe the workflow that takes
them in, to run once one changes:

    "variables": {"names": ["SOME_SETTING", "SOME_MAP"], "deploy": "deploy.yml"}

`pubkit sync --check` refuses a block of the wrong shape. The values live in
the owner's state file, which a control plane keeps GitHub to; pubkit holds
none of them.
"""

from __future__ import annotations

import json
import re
from typing import Any

BLOCK = "variables"
NAME = re.compile(r"[A-Z_][A-Z0-9_]*")
WORKFLOW = re.compile(r"[A-Za-z0-9_.-]+\.ya?ml")


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
