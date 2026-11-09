"""github.variable-entry: an entry of a JSON variable another repository
keeps, read through the API (`gh`), and what github.gate and deploy.rollout
read of the repository's variables. github.secret, github.variable and
github.deploy-settings, which set what Bitwarden holds, are pubsec's."""

from __future__ import annotations

import json
from typing import Any

from ...protect import ApiError
from ..model import Context, Need, Result
from . import Base, github_refused, schema

GITHUB_NAME = {"type": "string", "pattern": r"^(?!GITHUB_)[A-Z_][A-Z0-9_]*$",
               "x-phrase": "must be upper case letters, digits and _, not starting GITHUB_"}
FROM = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", "x-phrase": "must be a secret's name"}
ENVIRONMENT = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
               "x-phrase": "must be an environment's name"}
SERVER_ENVIRONMENT = "server"  # k8s-deploy.yml's environment input, by default
NO_ORIGIN = Result("unknown", "origin is not a GitHub repository",
                   fix="run this in a clone whose origin is the repository on GitHub")


def where(slug: str, environment: str | None) -> str:
    return f"{slug}, environment {environment}" if environment else slug


def path(slug: str, kind: str, name: str, environment: str | None) -> str:
    plural = f"{kind}s"
    if environment:
        return f"repos/{slug}/environments/{environment}/{plural}/{name}"
    return f"repos/{slug}/actions/{plural}/{name}"


POINTER = {"type": "string", "pattern": r"^(/([^~/]|~[01])*)+$", "x-phrase": "must be a JSON pointer, such as /evaluator"}
REPOSITORY = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+$",
              "x-phrase": "must be a repository, OWNER/NAME"}


def pointed(document: Any, pointer: str) -> tuple[bool, Any]:
    """Whether the JSON pointer is in the document, and what it points at."""
    node = document
    for part in pointer.split("/")[1:]:
        step = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and step in node:
            node = node[step]
        elif isinstance(node, list) and step.isdigit() and int(step) < len(node):
            node = node[int(step)]
        else:
            return False, None
    return True, node


class VariableEntry(Base):
    """An entry of a JSON variable another repository keeps, such as a release
    state: the variable is set, is JSON, has the pointer, and maybe its value.
    The variable's value is compared, never shown."""
    type = "github.variable-entry"
    grade = "propose"
    budget = 3.0
    schema = schema({"repository": REPOSITORY, "variable": GITHUB_NAME, "path": POINTER, "value": {}},
                    ("repository", "variable", "path"))

    def default_name(self, params: dict[str, Any]) -> str | None:
        if all(isinstance(params.get(k), str) for k in ("repository", "variable", "path")):
            return f"{params['repository']}/{params['variable']}{params['path']}"
        return None

    def check(self, need: Need, ctx: Context) -> Result:
        repo, variable, pointer = need.params["repository"], need.params["variable"], need.params["path"]
        try:
            found = ctx.gh("GET", f"repos/{repo}/actions/variables/{variable}", None) or {}
        except ApiError as e:
            if e.status == 404:
                return Result("missing", f"variable {variable} is not set in {repo}", fix=self.where(need))
            return github_refused(e, f"variable {variable} of {repo}")
        try:
            document = json.loads(found.get("value") if isinstance(found, dict) else "")
        except (TypeError, ValueError):
            return Result("drift", f"variable {variable} of {repo} is not JSON", fix=self.where(need))
        there, value = pointed(document, pointer)
        del document
        if not there:
            return Result("missing", f"variable {variable} of {repo} has no {pointer}", fix=self.where(need))
        if "value" in need.params and value != need.params["value"]:
            return Result("drift", f"{pointer} of variable {variable} in {repo} is not the need's value",
                          fix=self.where(need))
        compared = ", the need's value" if "value" in need.params else ""
        return Result("ready", f"variable {variable} of {repo} has {pointer}{compared}")

    def where(self, need: Need) -> str:
        repo, variable, pointer = need.params["repository"], need.params["variable"], need.params["path"]
        value = json.dumps(need.params["value"]) if "value" in need.params else "<its value>"
        return (f"in the owner's state file, under \"{repo.rsplit('/', 1)[-1]}\": set {pointer} of variables."
                f"{variable} to {value}\nthen pubkit variables --apply {repo}")

    def fulfil(self, need: Need, ctx: Context) -> Result:
        return Result("missing", "the variable is the state file's", fix=self.where(need))


def gate_value(ctx: Context, variable: str) -> tuple[str | None, Result | None]:
    """The gate variable's value, None when unset, or the result of a refusal."""
    try:
        found: Any = ctx.gh("GET", path(ctx.slug, "variable", variable, None), None) or {}
    except ApiError as e:
        if e.status == 404:
            return None, None
        return None, github_refused(e, f"variable {variable}")
    return (found.get("value") if isinstance(found, dict) else None), None
