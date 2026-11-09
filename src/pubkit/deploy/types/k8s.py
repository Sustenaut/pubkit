"""k8s.namespace, k8s.secrets-declared, k8s.manifests and k8s.cronjob:
what the cluster must hold, read through kubectl, and what the rendered
manifests read, which needs no cluster. They use pubkit.k8s, which needs
nothing of this package's. k8s.secret, which reads a Secret's values and
makes one from Bitwarden's, is pubsec's.

A fulfil writes through the kubectl channel or runs on the server, so each
needs the admin channel or the plane."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from ... import k8s
from ..model import Context, Need, Result, Stop
from . import Base, failed, kubectl_json, kubectl_refused, last_success, not_found, run_job, schema


class Namespace(Base):
    """The namespace, with the deploy service account and its role, as the
    server's sustenaut-app program makes them."""
    type = "k8s.namespace"
    grade = "auto"
    # Four kubectl calls: over the admin channel's kept connection about 0.35 s
    # each, as measured to the k3s server over the tailnet.
    budget = 4.0
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
        last, scheduled = last_success(ctx, name, status), status.get("lastScheduleTime")
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
