"""gcp.service-account-key: a Google Cloud service account's key, kept in
Bitwarden, that is one of the account's live keys.

The account is Terraform's; the key is not, so it is never in Terraform's
state. The check reads the account and its keys through gcloud, and the key
through the environment ./with-secrets gives: only its private_key_id's
first six characters are said. The fulfil is `pubkit service-account-key
create`'s, without its prompts: gcloud writes a new key to this process's
pipe, the walk's Bitwarden writer keeps it, base64-encoded, and when that
write fails the key is deleted from Google, as nothing holds it.

gcloud runs through `gcloud` below, which a test replaces: no context field.
"""

from __future__ import annotations

import base64
import binascii
import json
import shutil
import subprocess
from typing import Any

from ...app_secrets import env_name
from ..model import Context, Need, Ran, Result
from . import Base, schema, with_secret
from .bitwarden import MARKER
from .github import FROM

ACCOUNT = {"type": "string", "pattern": r"^[a-z][a-z0-9-]{4,28}[a-z0-9](@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com)?$",
           "x-phrase": "must be a service account: its id, or NAME@PROJECT.iam.gserviceaccount.com"}
PROJECT = {"type": "string", "pattern": r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$", "x-phrase": "must be a Google Cloud project's id"}
TIMEOUT = 50


def gcloud(args: list[str]) -> Ran:
    """gcloud with `args`; 127 when it is not installed."""
    if shutil.which("gcloud") is None:
        return Ran(127, "", "gcloud is not installed")
    try:
        done = subprocess.run(["gcloud", *args], capture_output=True, text=True, timeout=TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return Ran(127, "", f"gcloud did not run ({type(e).__name__})")
    return Ran(done.returncode, done.stdout, done.stderr)


def project_of(need: Need, ctx: Context) -> str | None:
    """The need's project, the account's, or the hosting block's google-cloud provider's."""
    if need.params.get("project"):
        return need.params["project"]
    account = need.params["account"]
    if "@" in account:
        return account.split("@", 1)[1].split(".", 1)[0]
    providers = ((ctx.config.get("hosting") or {}).get("providers") or {}).values()
    found = [p.get("project") for p in providers if isinstance(p, dict) and p.get("type") == "google-cloud"]
    return found[0] if len(found) == 1 else None


def email(need: Need, project: str) -> str:
    account = need.params["account"]
    return account if "@" in account else f"{account}@{project}.iam.gserviceaccount.com"


def last(ran: Ran) -> str:
    return (ran.stderr.strip().splitlines() or [f"exit {ran.returncode}"])[-1][:300]


class ServiceAccountKey(Base):
    type = "gcp.service-account-key"
    grade = "auto"
    budget = 4.0
    fulfil_budget = 60.0
    needs_of_context = ("write-token",)
    schema = schema(with_secret({"account": ACCOUNT, "project": PROJECT, "bitwarden": FROM}), ("account", "bitwarden"))

    def default_name(self, params: dict[str, Any]) -> str | None:
        return params.get("bitwarden")

    def value(self, need: Need, ctx: Context) -> bytes | None:
        """The key as Bitwarden keeps it: base64 of its JSON."""
        name = need.params["bitwarden"]
        if name in ctx.values:
            return ctx.values[name]
        found = ctx.env.get(env_name(name)) if ctx.env.get(MARKER) else None
        return None if found is None else found.encode()

    def action(self, need: Need, ctx: Context) -> str:
        project = project_of(need, ctx) or "?"
        return f"make a key of {email(need, project)} and write it to Bitwarden as {need.params['bitwarden']}"

    def check(self, need: Need, ctx: Context) -> Result:
        project = project_of(need, ctx)
        if project is None:
            return Result("unknown", "no project names the account's", fixable=False,
                          fix="give the need's project, or one google-cloud provider in the hosting block")
        account, name = email(need, project), need.params["bitwarden"]
        ran = gcloud(["iam", "service-accounts", "describe", account, f"--project={project}", "--format=value(email)"])
        if ran.returncode == 127:
            return Result("unknown", f"gcloud did not run, to read {account}",
                          fix="install gcloud, the Google Cloud CLI")
        if ran.returncode != 0:
            return Result("missing", f"service account {account} is not there", fixable=False,
                          fix="the account is Terraform's: add it in the stack and apply", details=[last(ran)])
        if name not in ctx.values and not ctx.env.get(MARKER):
            return Result("unknown", f"{name} is not looked for outside ./with-secrets",
                          fix="run this through ./with-secrets, which reads the Bitwarden project")
        held = self.value(need, ctx)
        if held is None:
            return Result("missing", f"{name} is not in the Bitwarden project {ctx.env.get(MARKER)}",
                          fix=f"make it: ./with-secrets ./pubkit deploy walk --fulfil, or pubkit service-account-key "
                              f"create --account {account} --secret {name}")
        try:
            key_id = str(json.loads(base64.b64decode(held, validate=True))["private_key_id"])
        except (binascii.Error, ValueError, KeyError, TypeError):
            return Result("drift", f"{name} in Bitwarden is not a service account key, base64-encoded",
                          fix="make a new one")
        finally:
            del held
        listed = gcloud(["iam", "service-accounts", "keys", "list", f"--iam-account={account}",
                         f"--project={project}", "--managed-by=user", "--format=value(name.basename())"])
        if listed.returncode != 0:
            return Result("unknown", f"the keys of {account} could not be listed", fix=last(listed))
        if key_id not in listed.stdout.split():
            return Result("drift", "the key in Bitwarden is not one of the account's live keys: make a new one",
                          fix=f"./with-secrets ./pubkit deploy walk --fulfil makes one and writes it as {name}")
        return Result("ready", f"key {key_id[:6]} is live")

    def fulfil(self, need: Need, ctx: Context) -> Result:
        project = project_of(need, ctx)
        if project is None or ctx.bitwarden_writer is None:
            return Result("missing", "no project, or no Bitwarden write token", fix="see the check's fix")
        account, name = email(need, project), need.params["bitwarden"]
        made = gcloud(["iam", "service-accounts", "keys", "create", "-", f"--iam-account={account}",
                       f"--project={project}"])
        if made.returncode != 0:
            return Result("missing", f"gcloud did not make a key of {account}", fix=last(made))
        try:
            key_id = str(json.loads(made.stdout)["private_key_id"])
        except (ValueError, KeyError, TypeError):
            return Result("missing", "gcloud printed something other than a key, not shown here",
                          fix=f"look for a new key on {account}, and delete it")
        value = base64.b64encode(made.stdout.encode()).decode()
        del made
        try:
            ctx.bitwarden_writer.write(name, value, f"Key {key_id} of {account}")
        except Exception as e:  # noqa: BLE001 - any failure of the write: the key goes
            gone = gcloud(["iam", "service-accounts", "keys", "delete", key_id, f"--iam-account={account}",
                           f"--project={project}", "--quiet"])
            what = "deleted from Google, as nothing holds it" if gone.returncode == 0 else \
                f"NOT deleted ({last(gone)}): delete it by hand"
            return Result("missing", f"Bitwarden refused the key ({type(e).__name__}); key {key_id[:6]} was {what}",
                          fix=str(e).replace(value, "[hidden]")[:300])
        ctx.values[name] = value.encode()
        return Result("ready", f"made key {key_id[:6]} of {account}, and wrote it to Bitwarden as {name}")
