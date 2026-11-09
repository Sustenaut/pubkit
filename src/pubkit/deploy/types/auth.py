"""auth.sign-in-client: the service's sign-in client, registered with an
identity provider's realm by the auth repository's register-app.py, which
runs on the server, where the client is kept, and puts it in a Secret there.

It drives register-app.py as a service's register-client.sh does: the
script goes over the admin channel on python3's standard input, with the
registration's settings as arguments. The check asks `--status`, which
changes nothing and prints {"status": "none" | "waiting" | "registered"}:

    registered, and the Secret has both keys   ready
    registered, and the Secret is not there    missing: the fulfil makes it on the
                                               server, from the client kept there, so
                                               its value never leaves the server
    waiting                                    blocked: the approval record must be
                                               published; the fulfil writes it into
                                               `approvals` when that file is here
    none                                       blocked: a person signs in (human)

Without the admin channel it is unknown. Whether the person has the role on
the access page is not checked: the fix names the page.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..model import Context, Need, Result
from . import Base, failed, restart, schema, secret_of
from .net import HOST, HTTPS

FILE = {"type": "string", "pattern": r"^(?!/)[A-Za-z0-9_./-]+$", "x-phrase": "must be a path from the repository"}
WORD = {"type": "string", "pattern": r"^[a-z0-9][a-z0-9-]*$", "x-phrase": "must be a lower-case name"}
KEYS = ["client-id", "client-secret"]
# Run on the server, on python3's standard input: the client kept there,
# applied as a Secret manifest on kubectl's standard input. argv: the
# client's file, the Secret's name and its namespace, none of them a value.
SECRET_SCRIPT = b'''import base64, json, subprocess, sys
client = json.load(open(sys.argv[1]))
b64 = lambda v: base64.b64encode(v.encode()).decode()
manifest = json.dumps({"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
                       "metadata": {"name": sys.argv[2], "namespace": sys.argv[3]},
                       "data": {"client-id": b64(client["client_id"]), "client-secret": b64(client["client_secret"])}})
sys.exit(subprocess.run(["k3s", "kubectl", "apply", "-f", "-"], input=manifest.encode()).returncode)
'''


class SignInClient(Base):
    type = "auth.sign-in-client"
    grade = "auto"
    budget = 6.0
    fulfil_budget = 120.0
    needs_of_context = ("server",)
    schema = schema({"realm": WORD, "role": WORD, "auth": HTTPS, "domain": HOST, "host": HOST,
                     "state": {"type": "string", "pattern": r"^/[A-Za-z0-9_./-]+$",
                               "x-phrase": "must be the directory on the server where the client is kept"},
                     "client_name": {"type": "string", "minLength": 1},
                     "register_app": FILE, "approvals": FILE,
                     "access": {"type": "string", "pattern": r"^https://", "x-phrase": "must be an https:// address"},
                     "secret": {"type": "object", "required": ["name"], "additionalProperties": False,
                                "properties": {"name": {"type": "string",
                                                        "pattern": r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$"}},
                                "x-phrase": "must be an object with the Secret's name"}},
                    ("realm", "role", "auth", "domain", "state", "secret"))

    def default_name(self, params: dict[str, Any]) -> str | None:
        return params.get("role")

    def secret_keys(self, params: dict[str, Any]) -> list[str]:
        return list(KEYS)

    def script(self, need: Need, ctx: Context) -> Path:
        return Path(ctx.root) / need.params.get("register_app", "../auth/register-app.py")

    def argv(self, need: Need, ctx: Context, *more: str) -> list[str]:
        p = need.params
        host = p.get("host") or ctx.block.get("host") or p["domain"]
        return ["python3", "-I", "-", "--state-dir", p["state"], "--auth", p["auth"], "--domain", p["domain"],
                "--name", p.get("client_name", f"{p['domain']} {p['realm']} sites"),
                "--redirect", f"https://{host}/oauth2/callback", "--logout", f"https://{host}/",
                "--role", p["role"], *more]

    def status(self, need: Need, ctx: Context) -> tuple[dict[str, Any] | None, Result | None]:
        script = self.script(need, ctx)
        if not script.is_file():
            return None, Result("unknown", f"no {script.name} at {need.params.get('register_app', '../auth/register-app.py')}",
                                fix="clone the auth repository beside this one, or give the need's register_app")
        ran = ctx.server(self.argv(need, ctx, "--status"), script.read_bytes())
        if ran.returncode == 127:
            return None, Result("unknown", "the sign-in client is judged over the admin channel only",
                                fix="run the walk with --server HOST, the admin channel")
        try:
            found = json.loads(ran.stdout)
        except ValueError:
            found = None
        if not isinstance(found, dict) or found.get("status") not in ("none", "waiting", "registered"):
            return None, Result("unknown", f"{script.name} --status gave no status",
                                fix=(ran.stderr.strip().splitlines() or [f"exit {ran.returncode}"])[-1][:300])
        return found, None

    def check(self, need: Need, ctx: Context) -> Result:
        found, result = self.status(need, ctx)
        if result is not None:
            return result
        secret, domain = (secret_of(need) or {})["name"], need.params["domain"]
        page = need.params.get("access", "the realm's access page")
        if found["status"] == "none":
            command = " ".join(self.argv(need, ctx))
            return Result("blocked", f"the {domain} client is not registered: a person signs in", fixable=False,
                          fix=f"ssh root@<server> \"{command}\" < {self.script(need, ctx).name}: it prints a link and "
                              f"a code; sign in there as the registrant, with the role {need.params['role']} on {page}")
        if found["status"] == "waiting":
            approvals = need.params.get("approvals")
            record = found.get("record") if isinstance(found.get("record"), dict) else {}
            return Result("blocked", f"the {domain} client waits for its approval record",
                          fix=f"the approval record is waiting: add it to {approvals or 'the approvals file'} and apply",
                          details=[f"record {record.get('name', '?')} TXT"],
                          fixable=bool(approvals) and (Path(ctx.root) / approvals).is_file(), step="record")
        # The Secret is read by k8s.secret, pubsec's, which alone reads a Secret's values.
        reader = ctx.types.get("k8s.secret")
        if reader is None:
            return Result("unknown", f"Secret {secret} is not looked at without k8s.secret",
                          fix="list pubsec in deploy.providers, as `pubkit sync` does")
        held = reader.check(Need("k8s.secret", secret, {"keys": KEYS}), ctx)
        if held.state == "ready":
            return Result("ready", f"the {domain} client is registered, and Secret {secret} has its keys")
        if held.state == "missing":
            return Result("missing", f"the {domain} client is registered, and Secret {secret} is not in {ctx.namespace}",
                          fix=f"run the walk with --fulfil, which makes it on the server", step="push")
        return held

    def action(self, need: Need, ctx: Context) -> str:
        approvals = need.params.get("approvals")
        if ctx.results.get(need.id, Result("ready", "")).step == "record":
            return f"write the approval record into {approvals}, for a pull request there"
        return f"make Secret {(secret_of(need) or {}).get('name')} on the server, from the client kept there"

    def fulfil(self, need: Need, ctx: Context) -> Result:
        found, result = self.status(need, ctx)
        if result is not None:
            return Result("missing", result.summary, fix=result.fix)
        if found["status"] == "waiting":
            return self.record(need, ctx, found)
        if found["status"] != "registered":
            return Result("missing", "the client is not registered", fix="see the check's fix")
        secret = (secret_of(need) or {})["name"]
        ran = ctx.server(["python3", "-I", "-", f"{need.params['state']}/client.json", secret, ctx.namespace],
                         SECRET_SCRIPT)
        if ran.returncode != 0:
            return failed(f"Secret {secret} was not made on the server", ran)
        return Result("ready", f"made Secret {secret} in {ctx.namespace} on the server, from the client kept there",
                      details=restart(ctx, secret))

    def record(self, need: Need, ctx: Context, found: dict[str, Any]) -> Result:
        """The approval record, written into the approvals file, as register-client.sh does."""
        approvals = Path(ctx.root) / need.params["approvals"]
        value = (found.get("record") or {}).get("value")
        if not isinstance(value, str) or not value:
            return Result("missing", "the status gives no record to write", fix="see register-app.py's --status")
        try:
            held = json.loads(approvals.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return Result("missing", f"{need.params['approvals']} cannot be read as JSON", fix=type(e).__name__)
        if held.get("@") == value:
            return Result("missing", f"{need.params['approvals']} has the record already, and it is not published yet",
                          fix="merge it and apply there; the DNS may take a while (its TTL)")
        held["@"] = value
        approvals.write_text(json.dumps(held, indent=2) + "\n", encoding="utf-8")
        return Result("ready", f"wrote the approval record into {need.params['approvals']}: commit it, merge, apply")
