"""github.secret, github.variable, github.deploy-settings and
github.variable-entry: what the repository on GitHub holds, through the API
(`gh`). A secret is checked by name: GitHub never returns its value. A
variable's value is compared only with the one Bitwarden gives it, run
through ./with-secrets, and never shown.

A fulfil sets what the secrets and github_app blocks name, as
`pubkit app-secrets` does (app_secrets.send, the value on gh's standard
input), from the environment ./with-secrets gives. A variable pubkit.json's
variables block declares is the state file's, so its fulfil only proposes."""

from __future__ import annotations

import json
from typing import Any

from ...app_secrets import env_name
from ...protect import ApiError
from ..implied import DEPLOY_SETTINGS
from ..model import Context, Need, Result
from . import Base, at_once, github_refused, schema

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


def github_name(need: Need) -> str:
    return need.params.get("to") or need.name.rsplit("/", 1)[-1]


def entries(ctx: Context) -> list[dict[str, Any]]:
    """What the secrets and github_app blocks send, as app_secrets plans it."""
    from ...app_secrets import BLOCK, OTHERS, config_problems, plan  # noqa: PLC0415
    if (BLOCK not in ctx.config and OTHERS not in ctx.config) or config_problems(ctx.config):
        return []
    return plan(ctx.config)


def send(ctx: Context, kind: str, name: str, source: str, environment: str | None) -> Result:
    """Set one secret or variable from Bitwarden's value of `source`."""
    value = ctx.env.get(env_name(source)) if ctx.env.get("PUBKIT_SECRETS_PROJECT") else None
    if value is None:
        return Result("missing", f"{source} is not in this walk's environment",
                      fix=f"put {source} in Bitwarden, and run the walk through ./with-secrets")
    refused = ctx.send(kind, name, ctx.slug, value, environment)
    if refused is not None:
        return Result("missing", f"gh refused {kind} {name}", fix=refused.replace(value, "[hidden]")[:300])
    return Result("ready", f"set {kind} {name} in {where(ctx.slug, environment)} from Bitwarden's {source}, "
                           f"{len(value.encode())} bytes")


def setter(kind: str, name: str, slug: str, environment: str | None, source: str | None) -> str:
    if source:
        return f"put {source} in Bitwarden, then ./with-secrets ./pubkit app-secrets"
    env = f" --env {environment}" if environment else ""
    return f"gh {kind} set {name} -R {slug}{env}"


class GitHubSecret(Base):
    type = "github.secret"
    grade = "auto"
    budget = 3.0
    fulfil_budget = 30.0
    kind = "secret"
    schema = schema({"from": FROM, "to": GITHUB_NAME, "environment": ENVIRONMENT})

    def grade_for(self, need: Need) -> str:
        return "auto" if need.params.get("from") else "human"

    def action(self, need: Need, ctx: Context) -> str:
        return f"gh {self.kind} set {github_name(need)} from Bitwarden's {need.params.get('from')}"

    def fulfil(self, need: Need, ctx: Context) -> Result:
        return send(ctx, self.kind, github_name(need), need.params["from"], need.params.get("environment"))

    def check(self, need: Need, ctx: Context) -> Result:
        if not ctx.slug:
            return NO_ORIGIN
        name, env = github_name(need), need.params.get("environment")
        try:
            ctx.gh("GET", path(ctx.slug, "secret", name, env), None)
        except ApiError as e:
            if e.status == 404:
                return Result("missing", f"secret {name} is not set in {where(ctx.slug, env)}",
                              fix=setter("secret", name, ctx.slug, env, need.params.get("from")))
            return github_refused(e, f"secret {name}")
        return Result("ready", f"secret {name} is set in {where(ctx.slug, env)}")


class GitHubVariable(GitHubSecret):
    """A variable the secrets or github_app blocks fill from Bitwarden (auto),
    or one the variables block declares, whose value is the state file's (propose)."""
    type = "github.variable"
    grade = "auto"
    kind = "variable"
    schema = schema({"from": FROM, "to": GITHUB_NAME, "environment": ENVIRONMENT, "declared": {"type": "boolean"}})

    def grade_for(self, need: Need) -> str:
        return "auto" if need.params.get("from") and not need.params.get("declared") else "propose"

    def fulfil(self, need: Need, ctx: Context) -> Result:
        if self.grade_for(need) == "auto":
            return super().fulfil(need, ctx)
        name = github_name(need)
        repo = ctx.slug.rsplit("/", 1)[-1] if ctx.slug else "REPOSITORY"
        return Result("missing", f"variable {name} is the state file's",
                      fix=f"in the owner's state file, under \"{repo}\": add to its variables\n"
                          f"  \"{name}\": <its value>\nthen pubkit variables --apply {repo}")

    def check(self, need: Need, ctx: Context) -> Result:
        if not ctx.slug:
            return NO_ORIGIN
        name, env, source = github_name(need), need.params.get("environment"), need.params.get("from")
        try:
            found = ctx.gh("GET", path(ctx.slug, "variable", name, env), None) or {}
        except ApiError as e:
            if e.status == 404:
                return Result("missing", f"variable {name} is not set in {where(ctx.slug, env)}",
                              fix=setter("variable", name, ctx.slug, env, source))
            return github_refused(e, f"variable {name}")
        value = found.get("value") if isinstance(found, dict) else None
        if need.params.get("declared") and not value:
            return Result("missing", f"variable {name} is empty in {where(ctx.slug, env)}",
                          fix="give it its value in the state file, then pubkit variables --apply")
        wanted = ctx.env.get(env_name(source)) if source else None
        if wanted is not None and wanted != value:
            return Result("drift", f"variable {name} in {where(ctx.slug, env)} is not Bitwarden's {source}",
                          fix="./with-secrets ./pubkit app-secrets")
        compared = " and is Bitwarden's" if wanted is not None else ""
        return Result("ready", f"variable {name} is set in {where(ctx.slug, env)}{compared}")


class DeploySettings(Base):
    """The five names the server's deploy job reads, on the repository or in
    the environment the job runs in: each scope listed once."""
    type = "github.deploy-settings"
    grade = "auto"
    budget = 3.0
    fulfil_budget = 60.0
    singleton = True
    schema = schema({"environment": ENVIRONMENT})

    def missing(self, need: Need, ctx: Context) -> tuple[list[str], Result | None]:
        """The settings set nowhere. The four listings are asked at once, as
        one after another overran the budget."""
        env = need.params.get("environment", SERVER_ENVIRONMENT)
        held: set[tuple[str, str]] = set()
        asked = [(kind, scope) for kind in ("secret", "variable")
                 for scope in (f"repos/{ctx.slug}/actions", f"repos/{ctx.slug}/environments/{env}")]
        answers = at_once(ctx.gh, [("GET", f"{scope}/{kind}s?per_page={100 if kind == 'secret' else 30}", None)
                                   for kind, scope in asked], keep_errors=True)
        for (kind, scope), found in zip(asked, answers):
            if isinstance(found, ApiError):
                if found.status == 404 and "environments" in scope:
                    continue
                return [], github_refused(found, f"the {kind}s of {ctx.slug}")
            held |= {(kind, str(item.get("name"))) for item in (found or {}).get(f"{kind}s") or []
                     if isinstance(item, dict)}
        return [name for name, kind in DEPLOY_SETTINGS.items() if (kind, name) not in held], None

    def action(self, need: Need, ctx: Context) -> str:
        return "gh secret set and gh variable set the deploy settings the secrets block names"

    def check(self, need: Need, ctx: Context) -> Result:
        if not ctx.slug:
            return NO_ORIGIN
        env = need.params.get("environment", SERVER_ENVIRONMENT)
        missing, refused = self.missing(need, ctx)
        if refused:
            return refused
        if missing:
            named = {e["to"] for e in entries(ctx)}
            return Result("missing", f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} set neither "
                                     f"in {ctx.slug} nor its {env} environment",
                          fix="name them in pubkit.json's secrets block, then ./with-secrets ./pubkit app-secrets",
                          fixable=all(m in named for m in missing))
        return Result("ready", f"the {len(DEPLOY_SETTINGS)} deploy settings are set in {ctx.slug} or its {env} "
                               "environment")

    def fulfil(self, need: Need, ctx: Context) -> Result:
        missing, refused = self.missing(need, ctx)
        if refused:
            return Result("missing", refused.summary, fix=refused.fix)
        done, sent = [], {e["to"]: e for e in entries(ctx)}
        for name in missing:
            entry = sent.get(name)
            if entry is None:
                return Result("missing", f"the secrets block does not name {name}",
                              fix="name it in pubkit.json's secrets block")
            made = send(ctx, entry["kind"], name, entry["from"], entry.get("environment"))
            if made.state != "ready":
                return made
            done.append(name)
        return Result("ready", f"set {', '.join(done) or 'nothing'} from Bitwarden")


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
