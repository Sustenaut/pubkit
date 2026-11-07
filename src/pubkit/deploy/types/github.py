"""github.secret, github.variable and github.deploy-settings: what the
repository on GitHub holds, through the API (`gh`). A secret is checked by
name: GitHub never returns its value. A variable's value is compared only
with the one Bitwarden gives it, run through ./with-secrets, and never shown."""

from __future__ import annotations

from typing import Any

from ...app_secrets import env_name
from ...protect import ApiError
from ..implied import DEPLOY_SETTINGS
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


def github_name(need: Need) -> str:
    return need.params.get("to") or need.name.rsplit("/", 1)[-1]


def setter(kind: str, name: str, slug: str, environment: str | None, source: str | None) -> str:
    if source:
        return f"put {source} in Bitwarden, then ./with-secrets ./pubkit app-secrets"
    env = f" --env {environment}" if environment else ""
    return f"gh {kind} set {name} -R {slug}{env}"


class GitHubSecret(Base):
    type = "github.secret"
    grade = "auto"
    budget = 3.0
    schema = schema({"from": FROM, "to": GITHUB_NAME, "environment": ENVIRONMENT})

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


class GitHubVariable(Base):
    type = "github.variable"
    grade = "propose"
    budget = 3.0
    schema = schema({"from": FROM, "to": GITHUB_NAME, "environment": ENVIRONMENT, "declared": {"type": "boolean"}})

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
    the environment the job runs in."""
    type = "github.deploy-settings"
    grade = "propose"
    budget = 3.0
    singleton = True
    schema = schema({"environment": ENVIRONMENT})

    def check(self, need: Need, ctx: Context) -> Result:
        if not ctx.slug:
            return NO_ORIGIN
        env = need.params.get("environment", SERVER_ENVIRONMENT)
        missing = []
        for name, kind in DEPLOY_SETTINGS.items():
            found = False
            for environment in (None, env):
                try:
                    ctx.gh("GET", path(ctx.slug, kind, name, environment), None)
                    found = True
                    break
                except ApiError as e:
                    if e.status != 404:
                        return github_refused(e, f"{kind} {name}")
            if not found:
                missing.append(name)
        if missing:
            return Result("missing", f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} set neither "
                                     f"in {ctx.slug} nor its {env} environment",
                          fix="name them in pubkit.json's secrets block, then ./with-secrets ./pubkit app-secrets")
        return Result("ready", f"the {len(DEPLOY_SETTINGS)} deploy settings are set in {ctx.slug} or its {env} "
                               "environment")


def gate_value(ctx: Context, variable: str) -> tuple[str | None, Result | None]:
    """The gate variable's value, None when unset, or the result of a refusal."""
    try:
        found: Any = ctx.gh("GET", path(ctx.slug, "variable", variable, None), None) or {}
    except ApiError as e:
        if e.status == 404:
            return None, None
        return None, github_refused(e, f"variable {variable}")
    return (found.get("value") if isinstance(found, dict) else None), None
