"""k8s.namespace, k8s.secret, k8s.secrets-declared, k8s.manifests and
k8s.cronjob: what the cluster must hold, read through kubectl, and what the
rendered manifests read, which needs no cluster. They use pubkit.k8s, which
needs nothing of this package's.

A fulfil writes through the kubectl channel or runs on the server, so each
needs the admin channel or the plane: a value reaches kubectl as a Secret
manifest on its standard input, never in argv or a file."""

from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from ... import k8s
from ..model import Context, Need, Ran, Result, Stop
from . import Base, failed, kubectl_json, kubectl_refused, not_found, restart, run_job, schema, secret_of

KEYS = {"type": "array", "items": {"type": "string", "pattern": r"^[-._a-zA-Z0-9]{1,253}$"},
        "x-phrase": "must list the Secret's keys"}


def hex_32(value: bytes) -> str | None:
    if re.fullmatch(rb"[0-9a-f]{64}", value):
        return None
    return f"{len(value)} bytes; it must be 64 lower-case hex digits, 32 bytes"


def base64_32(value: bytes) -> str | None:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return f"{len(value)} bytes, not base64; it must be base64 of 32 bytes"
    if len(decoded) != 32:
        return f"{len(value)} bytes, base64 of {len(decoded)} bytes; it must be base64 of 32 bytes"
    return None


# The shapes a value may be required to have, by the name a need gives.
SHAPES = {"oauth2-proxy-cookie": k8s.COOKIE_SECRET,
          "hex-32": k8s.Shape("32 bytes as hex", hex_32),
          "base64-32": k8s.Shape("32 bytes as base64", base64_32)}
SHAPE = {"enum": sorted(SHAPES)}


def shape_name(shape: k8s.Shape) -> str | None:
    return next((name for name, s in SHAPES.items() if s is shape), None)


class Namespace(Base):
    """The namespace, with the deploy service account and its role, as the
    server's sustenaut-app program makes them."""
    type = "k8s.namespace"
    grade = "auto"
    budget = 2.0
    fulfil_budget = 60.0
    singleton = True
    needs_of_context = ("server",)

    def action(self, need: Need, ctx: Context) -> str:
        return f"make namespace {need.name} on the server: sustenaut-app {need.name}"

    def fulfil(self, need: Need, ctx: Context) -> Result:
        ran = ctx.server(["sustenaut-app", need.name], None)
        if ran.returncode != 0:
            return failed(f"sustenaut-app {need.name} failed on the server", ran)
        return Result("ready", f"made namespace {need.name}, with the deploy service account and its role")

    def check(self, need: Need, ctx: Context) -> Result:
        ns = need.name
        server = ctx.env.get("SUSTENAUT_SERVER", "<server>")
        fix = f"ssh root@{server} sustenaut-app {ns}"
        if ctx.channel == "deploy":
            # The deploy identity may read neither namespaces nor roles.
            return Result("unknown", f"namespace {ns} is judged over the admin channel only",
                          fix="run the walk with --server HOST, the admin channel, or from the plane")
        _, ran = kubectl_json(ctx, "get", "namespace", ns)
        if ran.returncode != 0:
            if not_found(ran):
                return Result("missing", f"namespace {ns} is not there", fix=fix)
            return kubectl_refused(ran, ns, "namespaces")
        missing = []
        for kind in ("serviceaccount", "role", "rolebinding"):
            _, ran = kubectl_json(ctx, "-n", ns, "get", kind, "deploy")
            if ran.returncode != 0 and not not_found(ran):
                return kubectl_refused(ran, ns, f"{kind}s")
            if ran.returncode != 0:
                missing.append(f"{kind} deploy")
        if missing:
            return Result("missing", f"namespace {ns} has no {', '.join(missing)}", fix=fix)
        return Result("ready", f"namespace {ns} is there, with the deploy service account and its role")


class Secret(Base):
    """A Secret in the namespace, with its keys, and values of a known shape having it."""
    type = "k8s.secret"
    grade = "auto"
    budget = 5.0
    fulfil_budget = 60.0
    needs_of_context = ("kubectl-write",)
    schema = schema({"keys": KEYS, "shapes": {"type": "object", "additionalProperties": SHAPE,
                                              "x-phrase": "must give a shape by key"}})

    def fillers(self, need: Need, ctx: Context) -> list[Need]:
        """The needs whose secret fills this Secret, in the plan."""
        return [n for n in ctx.needs.values() if (secret_of(n) or {}).get("name") == need.name]

    def action(self, need: Need, ctx: Context) -> str:
        sources = ", ".join(n.id for n in self.fillers(need, ctx))
        return f"apply Secret {need.name} in {ctx.namespace} from {sources}, and restart what reads it"

    def check(self, need: Need, ctx: Context) -> Result:
        result = self.found(need, ctx)
        # Made only from the needs that fill it, and only when each gives its value.
        fillers = self.fillers(need, ctx)
        result.fixable = bool(fillers) and all(callable(getattr(ctx.types.get(n.type), "value", None))
                                               for n in fillers)
        return result

    def exists(self, need: Need, ctx: Context, source: str) -> Result:
        """Over the deploy channel, which shows no Secret's data: its key names,
        as the server's `secret-keys` verb lists them, or, where the server has
        no such verb yet, whether it is there by name alone."""
        ns, name = ctx.namespace, need.name
        missing = Result("missing", f"Secret {name} is not in {ns}", fix=f"make it in {ns}, from what fills it ({source})")
        ran = ctx.kubectl(["-n", ns, "secret-keys", name])
        if ran.returncode == 0:
            try:
                keys = json.loads(ran.stdout)
            except ValueError:
                keys = None
            if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
                return Result("unknown", f"the server's secret-keys did not list Secret {name}'s keys",
                              fix="see the server's deploy program")
            lacking = [k for k in need.params.get("keys", []) if k not in keys]
            if lacking:
                return Result("drift", f"Secret {name} has no key {lacking[0]}, which need {need.id} reads",
                              fix=f"put the right value in Secret {name} in {ns}, from what fills it ({source})",
                              details=[f"Secret {name} has no key {k}" for k in lacking])
            return Result("ready", f"Secret {name} in {ns} has keys {', '.join(sorted(keys))}; their values "
                                   f"are not checked over the deploy channel")
        if not_found(ran):
            return missing
        if ran.returncode != 2:
            return kubectl_refused(Ran(ran.returncode, "", ran.stderr), ns, "Secrets")
        ran = ctx.kubectl(["-n", ns, "get", "secret", name, "-o", "name"])
        if ran.returncode == 0 and ran.stdout.strip() == f"secret/{name}":
            return Result("ready", f"Secret {name} is in {ns}",
                          details=["keys not checked on this channel: the server has no secret-keys verb"])
        if not_found(ran):
            return missing
        return kubectl_refused(Ran(ran.returncode, "", ran.stderr), ns, "Secrets")

    def fulfil(self, need: Need, ctx: Context) -> Result:
        ns, name = ctx.namespace, need.name
        data: dict[str, bytes] = {}
        sources = []
        for filler in self.fillers(need, ctx):
            secret = secret_of(filler) or {}
            if not secret.get("key"):
                return Result("missing", f"{filler.id} names no key of Secret {name} to fill",
                              fix=f"give {filler.id}'s secret a key")
            value = ctx.types[filler.type].value(filler, ctx)
            if value is None:
                return Result("missing", f"{filler.id}'s value is not in this walk",
                              fix="run the walk through ./with-secrets, which reads Bitwarden")
            if secret.get("decode") == "base64":
                try:
                    value = base64.b64decode(value, validate=True)
                except (binascii.Error, ValueError):
                    return Result("drift", f"{filler.id}'s value is not base64, as its secret's decode says",
                                  fix=f"make {filler.name} again")
            data[secret["key"]] = value
            sources.append(filler.id.replace("bitwarden.generated:", "Bitwarden ")
                           .replace("bitwarden.secret:", "Bitwarden "))
        if lacking := [k for k in need.params.get("keys", []) if k not in data]:
            return Result("missing", f"no need fills key {lacking[0]} of Secret {name}",
                          fix=f"add a need whose secret fills {name} key {lacking[0]}")
        manifest = json.dumps({"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
                               "metadata": {"name": name, "namespace": ns},
                               "data": {k: base64.b64encode(v).decode() for k, v in sorted(data.items())}})
        ran = ctx.kubectl(["-n", ns, "apply", "-f", "-"], input=manifest.encode())
        if ran.returncode != 0:
            return failed(f"kubectl apply of Secret {name} in {ns} failed", ran, tuple(data.values()))
        size = sum(len(v) for v in data.values())
        return Result("ready", f"made Secret {name} in {ns} from {', '.join(sources)}, {size} bytes",
                      details=restart(ctx, name))

    def found(self, need: Need, ctx: Context) -> Result:
        ns, name = ctx.namespace, need.name
        source = need.implied_by or "deploy.needs"
        if ctx.channel == "deploy":
            return self.exists(need, ctx, source)
        data = k8s.Cluster([], run=ctx.kubectl).secret(ns, name)
        if data is None:
            return Result("missing", f"Secret {name} is not in {ns}",
                          fix=f"make it in {ns}, from what fills it ({source})")
        if isinstance(data, str):
            if "may not read Secrets" in data:
                return Result("unknown", f"kubectl may not read Secret {name} in {ns}", fix=data)
            return Result("unknown", f"Secret {name} in {ns} could not be read", fix=data)
        keys = tuple(need.params.get("keys", []))
        shapes = {k: SHAPES[s] for k, s in need.params.get("shapes", {}).items()}
        bad = k8s.check_keys(name, [k8s.Need(ns, name, f"need {need.id}", keys, shapes=shapes)], data)
        if bad:
            return Result("drift", bad[0], fix=f"put the right value in Secret {name} in {ns}, "
                                                f"from what fills it ({source})", details=bad)
        listed = f" has keys {', '.join(keys)}" if keys else " is there"
        return Result("ready", f"Secret {name} in {ns}{listed}")


class SecretsDeclared(Base):
    """Every Secret the rendered manifests read is one a need fills, and every
    Secret a need fills is read."""
    type = "k8s.secrets-declared"
    budget = 1.0
    offline = True
    singleton = True

    def check(self, need: Need, ctx: Context) -> Result:
        from ..implied import declarers  # noqa: PLC0415
        from ..registry import types  # noqa: PLC0415
        try:
            found = k8s.collect(k8s.documents(ctx.rendered()), ctx.namespace)
        except Stop as stop:
            if stop.missing_tool:
                return Result("unknown", "the overlay could not be rendered", fix=str(stop))
            return Result("drift", "the overlay does not render", fix=str(stop))
        except (ValueError, yaml.YAMLError) as e:
            return Result("drift", "the rendered manifests are not YAML", fix=f"see what the overlay renders "
                                                                             f"({type(e).__name__})")
        declared = declarers(ctx.block, ctx.types or types(), ctx.namespace)
        held = {name for ns, name in found.held if ns == ctx.namespace}
        reads: dict[str, list[k8s.Need]] = {}
        for n in found.needs:
            if n.namespace == ctx.namespace:
                reads.setdefault(n.secret, []).append(n)
        lines = list(found.problems)
        for name, refs in sorted(reads.items()):
            required = [r for r in refs if not r.optional]
            if name in held or not required:
                continue
            fillers = declared.get(name)
            if not fillers:
                lines.append(f"Secret {name} is read by {', '.join(r.where for r in required)}, and no need "
                             f"says where it comes from: add it to deploy.needs, or a need's secret")
                continue
            if any(keys is None for _, keys in fillers):
                continue
            provided = {k for _, keys in fillers for k in keys or []}
            for r in required:
                for key in r.keys:
                    if key not in provided:
                        lines.append(f"Secret {name} key {key} is read by {r.where}, and "
                                     f"{', '.join(i for i, _ in fillers)} provides only {', '.join(sorted(provided))}")
        for name, fillers in sorted(declared.items()):
            if name not in reads:
                lines.append(f"Secret {name} is declared by {', '.join(i for i, _ in fillers)} but nothing reads it")
        count = len([n for n, refs in reads.items() if n not in held and any(not r.optional for r in refs)])
        if lines:
            return Result("drift", f"the manifests and the needs disagree about {len(lines)} "
                                   f"Secret{'s' * (len(lines) != 1)}", details=lines,
                          fix="make deploy.needs say where each Secret the manifests read comes from, "
                              "and drop what nothing reads")
        return Result("ready", f"the manifests read {count} Secret{'s' * (count != 1)}, "
                               f"and a need says where each comes from")


class Manifests(Base):
    """A manifest file applied by hand, such as an admin's: it parses, and,
    online, each object in it is in the cluster."""
    type = "k8s.manifests"
    grade = "auto"
    budget = 5.0
    fulfil_budget = 60.0
    offline = True
    needs_of_context = ("kubectl-write",)
    schema = schema({"path": {"type": "string", "pattern": r"^(?!/)(?!.*(^|/)\.\.(/|$))[A-Za-z0-9_./-]+\.ya?ml$",
                              "x-phrase": "must be a YAML file in the repository"}}, ("path",))

    def default_name(self, params: dict[str, Any]) -> str | None:
        return params.get("path")

    def action(self, need: Need, ctx: Context) -> str:
        return f"kubectl apply -f {need.params['path']}"

    def fulfil(self, need: Need, ctx: Context) -> Result:
        path = need.params["path"]
        try:
            text = (Path(ctx.root) / path).read_bytes()
        except OSError as e:
            return Result("missing", f"{path} cannot be read", fix=type(e).__name__)
        ran = ctx.kubectl(["-n", ctx.namespace, "apply", "-f", "-"], input=text)
        if ran.returncode != 0:
            return failed(f"kubectl apply -f {path} failed", ran)
        return Result("ready", f"applied {path}: {len(ran.stdout.splitlines())} object"
                               f"{'s' * (len(ran.stdout.splitlines()) != 1)}")

    def files(self, params: dict[str, Any]) -> list[str]:
        return [params["path"]] if isinstance(params.get("path"), str) else []

    def check(self, need: Need, ctx: Context) -> Result:
        path = need.params["path"]
        try:
            text = (Path(ctx.root) / path).read_text(encoding="utf-8")
            docs = k8s.documents(text)
        except (OSError, ValueError, SystemExit, yaml.YAMLError) as e:
            return Result("drift", f"{path} cannot be read as manifests",
                          fix=f"make {path} YAML that kubectl applies ({type(e).__name__})")
        if not docs:
            return Result("drift", f"{path} holds no object", fix=f"put the objects to apply in {path}")
        objects = [(str(d.get("kind")), str((d.get("metadata") or {}).get("name")),
                    (d.get("metadata") or {}).get("namespace") or ctx.namespace) for d in docs]
        if ctx.offline:
            return Result("ready", f"{path} holds {len(objects)} object{'s' * (len(objects) != 1)}")
        missing = []
        for kind, name, ns in objects:
            ran = ctx.kubectl(["-n", ns, "get", f"{kind.lower()}/{name}", "-o", "name"])
            if ran.returncode != 0 and not not_found(ran):
                return kubectl_refused(ran, ns, f"{kind} objects")
            if ran.returncode != 0:
                missing.append(f"{kind}/{name}")
        if missing:
            return Result("missing", f"{', '.join(missing)} from {path} {'is' if len(missing) == 1 else 'are'} "
                                     f"not in {ctx.namespace}", fix=f"kubectl apply -f {path}, as an admin")
        return Result("ready", f"the {len(objects)} object{'s' * (len(objects) != 1)} in {path} are applied")


DURATION = {"type": "string", "pattern": r"^[1-9][0-9]*[mhd]$", "x-phrase": "must be a duration: minutes, hours "
                                                                            "or days, such as 90m, 36h or 2d"}
UNITS = {"m": timedelta(minutes=1), "h": timedelta(hours=1), "d": timedelta(days=1)}


def duration(text: str) -> timedelta:
    return int(text[:-1]) * UNITS[text[-1]]


def interval(schedule: str) -> timedelta | None:
    """How often a schedule of a common form runs, or None for another."""
    named = {"@hourly": timedelta(hours=1), "@daily": timedelta(days=1), "@midnight": timedelta(days=1),
             "@weekly": timedelta(days=7), "@monthly": timedelta(days=31), "@yearly": timedelta(days=366),
             "@annually": timedelta(days=366)}
    if schedule.strip() in named:
        return named[schedule.strip()]
    fields = schedule.split()
    if len(fields) != 5:
        return None
    minute, hour, day, month, weekday = fields

    def every(field: str) -> int | None:
        """1 for *, N for */N, None for anything else."""
        if field == "*":
            return 1
        m = re.fullmatch(r"\*/([1-9][0-9]*)", field)
        return int(m[1]) if m else None

    def listed(field: str) -> int | None:
        """How many fixed values a field lists: 5, or 1,2,3."""
        return len(field.split(",")) if re.fullmatch(r"[0-9]+(,[0-9]+)*", field) else None

    if (n := every(minute)) and hour == day == month == weekday == "*":
        return timedelta(minutes=n)
    if listed(minute) and month == "*":
        if (n := every(hour)) and day == weekday == "*":
            return timedelta(hours=n) / listed(minute)
        if (count := listed(hour)) and day == weekday == "*":
            return timedelta(days=1) / count
        if listed(hour) == 1 and day == "*" and (count := listed(weekday)):
            return timedelta(days=7) / count
        if listed(hour) == 1 and weekday == "*" and listed(day) == 1:
            return timedelta(days=31)
    return None


def when(text: str) -> datetime:
    return datetime.fromisoformat(str(text).replace("Z", "+00:00"))


class CronJob(Base):
    """A CronJob that succeeds as often as it runs: a service without a host."""
    type = "k8s.cronjob"
    grade = "auto"
    budget = 4.0
    fulfil_budget = 300.0
    needs_of_context = ("kubectl-write",)
    schema = schema({"within": DURATION})

    def action(self, need: Need, ctx: Context) -> str:
        return f"run a Job from CronJob {need.name}, and wait for it"

    def check(self, need: Need, ctx: Context) -> Result:
        ns, name = ctx.namespace, need.name
        found, ran = kubectl_json(ctx, "-n", ns, "get", "cronjob", name)
        if found is None:
            if not_found(ran):
                return Result("missing", f"CronJob {name} is not in {ns}", fixable=False,
                              fix=f"see that the overlay {ctx.block.get('overlay', 'k8s')} holds it")
            return kubectl_refused(ran, ns, "cronjobs")
        spec, status = found.get("spec") or {}, found.get("status") or {}
        given = need.params.get("within")
        schedule = str(spec.get("schedule", ""))
        within = duration(given) if given else (2 * every if (every := interval(schedule)) else None)
        if within is None:
            return Result("unknown", f"CronJob {name}'s schedule {schedule} is not one pubkit reads",
                          fix=f"give the need's within: how recently {name} must have succeeded, such as 36h")
        if spec.get("suspend") is True:
            return Result("drift", f"CronJob {name} is suspended", fixable=False,
                          fix=f"kubectl -n {ns} patch cronjob {name} -p '{{\"spec\":{{\"suspend\":false}}}}'")
        last, scheduled = status.get("lastSuccessfulTime"), status.get("lastScheduleTime")
        hours = f"{within.total_seconds() / 3600:g} h"
        if last and ctx.now() - when(last) <= within:
            return Result("ready", f"CronJob {name} succeeded at {last}, within {hours}")
        fix = f"pubkit k8s diagnose --namespace {ns}"
        if last:
            return Result("drift", f"CronJob {name} last succeeded at {last}, over {hours} ago", fix=fix)
        if scheduled:
            return Result("drift", f"CronJob {name}'s last run did not succeed", fix=fix)
        return Result("missing", f"CronJob {name} never ran",
                      fix=f"kubectl -n {ns} create job --from=cronjob/{name} {name}-now")

    def fulfil(self, need: Need, ctx: Context) -> Result:
        return run_job(ctx, need.name, self.fulfil_budget)
