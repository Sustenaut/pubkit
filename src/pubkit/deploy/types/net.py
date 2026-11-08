"""upstream, dns.site, service.health and storage.tenant: what answers on the network.

dns.site's offline part reads the hosting block, of this repository's
pubkit.json or of the one --zones names (the repository that keeps the
zone): the host must be a site of one of its zones. Online, its A or AAAA
records must resolve. Its fulfil proposes the site to add.

service.health's fulfil runs a Job from its CronJob, when that is what is
missing; nothing is done for a host that does not answer.
"""

from __future__ import annotations

import time
import base64
import json
from datetime import datetime, timedelta
from typing import Any

from ...protect import ApiError
from ..model import Context, Need, Result
from . import Base, github_refused, kubectl_json, kubectl_refused, last_success, run_job, schema

HTTPS = {"type": "string", "pattern": r"^https://[^\s/?#]+(/[^\s]*)?$", "x-phrase": "must be an https:// address"}
HOST = {"type": "string", "pattern": r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$",
        "x-phrase": "must be a host name in lower case"}
TIMEOUT = 5.0
SINCE = timedelta(hours=24)  # how recently a CronJob's last success must be


def ask(ctx: Context, url: str, timeout: float) -> tuple[int | None, float, str]:
    """The status `url` answers with, how long it took, and why there is none."""
    started = time.monotonic()
    try:
        status, _body = ctx.http_get(url, timeout)
    except TimeoutError:
        return None, time.monotonic() - started, "timeout"
    except OSError as e:
        return None, time.monotonic() - started, type(e).__name__
    return status, time.monotonic() - started, ""


def path_of(url: str) -> str:
    rest = url.split("://", 1)[-1]
    return "/" + rest.split("/", 1)[1] if "/" in rest else "/"


class Upstream(Base):
    """A service this one calls, which must answer its health address."""
    type = "upstream"
    budget = 6.0
    schema = schema({"health": HTTPS}, ("health",))

    def check(self, need: Need, ctx: Context) -> Result:
        url = need.params["health"]
        status, seconds, why = ask(ctx, url, TIMEOUT)
        if why == "timeout":
            return Result("unknown", f"{need.name} did not answer at {path_of(url)} in {TIMEOUT:g} s",
                          fix=f"see whether {need.name} is up: curl -sS {url}")
        if status is None:
            return Result("missing", f"{need.name} cannot be reached at {path_of(url)} ({why})",
                          fix=f"{need.name} must be up before this deploys: curl -sS {url}")
        if not 200 <= status < 400:
            return Result("missing", f"{need.name} answers {status} at {path_of(url)}",
                          fix=f"{need.name} must answer 2xx or 3xx before this deploys: curl -sS {url}")
        return Result("ready", f"{need.name} answers {status} at {path_of(url)} in {seconds:.1f} s")


def site_of(host: str, zones: dict[str, Any]) -> str | None:
    """The zone `host` is a site of, or None."""
    for zone, given in zones.items():
        sites = (given or {}).get("sites", {}) if isinstance(given, dict) else {}
        if host == zone and "@" in sites:
            return zone
        if host.endswith(f".{zone}") and host[:-len(zone) - 1] in sites:
            return zone
    return None


class Site(Base):
    """The public host: a site in the hosting block, and in the DNS."""
    type = "dns.site"
    grade = "propose"
    budget = 5.0
    offline = True
    schema = schema({"host": HOST})

    def default_name(self, params: dict[str, Any]) -> str | None:
        return params.get("host")

    def fulfil(self, need: Need, ctx: Context) -> Result:
        host = need.params.get("host", need.name)
        zone = next((z for z in sorted(ctx.zones or {}, key=len, reverse=True) if host.endswith(f".{z}")), None)
        if host in (ctx.zones or {}):
            zone, label = host, "@"
        else:
            label = host[:-len(zone) - 1] if zone else host
        within = f"hosting.zones[\"{zone}\"].sites" if zone else "the sites of its zone in hosting.zones"
        entry = json.dumps({label: {"provider": "<provider>", "kind": "server", "server": "<server>"}})
        return Result("missing", f"{host} is a site to add", fix=f"in the pubkit.json that keeps the zone, add to "
                                                                    f"{within}:\n  {entry[1:-1]}\nthen apply its hosting stack")

    def check(self, need: Need, ctx: Context) -> Result:
        host = need.params.get("host", need.name)
        if ctx.zones is None:
            return Result("unknown", f"no hosting block says where {host} is",
                          fix="give --zones PATH, the pubkit.json whose hosting block keeps the zone")
        zone = site_of(host, ctx.zones)
        if zone is None:
            return Result("missing", f"{host} is no site of a zone in the hosting block",
                          fix="add it to the sites of its zone in the hosting block")
        if ctx.offline:
            return Result("ready", f"{host} is a site of {zone}")
        found = [ctx.resolve(host, rtype) for rtype in ("A", "AAAA")]
        if any(f is None for f in found):
            return Result("unknown", f"{host} could not be resolved", fix="install dig (bind9-dnsutils)")
        records = [r for f in found for r in f or []]
        if not records:
            return Result("missing", f"{host} has no A or AAAA record",
                          fix=f"apply the hosting stack that keeps {zone}")
        return Result("ready", f"{host} is a site of {zone}, with {len(records)} address"
                               f"{'es' if len(records) != 1 else ''}")


class Health(Base):
    """The deployed service answers, and its CronJob ran."""
    type = "service.health"
    grade = "auto"
    budget = 6.0
    fulfil_budget = 300.0
    singleton = True
    needs_of_context = ("kubectl-write",)

    def action(self, need: Need, ctx: Context) -> str:
        return f"run a Job from {need.params.get('job')}, and wait for it"

    def fulfil(self, need: Need, ctx: Context) -> Result:
        return run_job(ctx, str(need.params.get("job", "/")).split("/", 1)[1], self.fulfil_budget)
    schema = schema({"path": {"type": "string", "pattern": r"^/[^\s]*$", "x-phrase": "must be a path, starting /"},
                     "job": {"type": "string", "pattern": r"^cronjob/[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$",
                             "x-phrase": "must be cronjob/NAME"}})

    def check(self, need: Need, ctx: Context) -> Result:
        host = ctx.block.get("host")
        if not host:
            return Result("unknown", "the deploy block has no host to ask", fix="give deploy.host")
        url = f"https://{host}{need.params.get('path', '/')}"
        status, seconds, why = ask(ctx, url, TIMEOUT)
        if why == "timeout":
            return Result("unknown", f"{host} did not answer in {TIMEOUT:g} s", fix=f"curl -sS {url}")
        if status != 200:
            said = f"answers {status}" if status is not None else f"cannot be reached ({why})"
            return Result("missing", f"{host} {said} at {path_of(url)}", fixable=False,
                          fix=f"pubkit k8s diagnose --namespace {ctx.namespace}")
        summary = f"{host} answers 200 at {path_of(url)} in {seconds:.1f} s"
        job = need.params.get("job")
        if not job:
            return Result("ready", summary)
        name = job.split("/", 1)[1]
        found, ran = kubectl_json(ctx, "-n", ctx.namespace, "get", "cronjob", name)
        if found is None:
            if ran.returncode != 0 and ("(NotFound)" in ran.stderr or " not found" in ran.stderr):
                return Result("missing", f"CronJob {name} is not in {ctx.namespace}", fixable=False,
                              fix=f"see that the manifests in {ctx.block.get('overlay', 'k8s')} hold it")
            return kubectl_refused(ran, ctx.namespace, "cronjobs")
        last = last_success(ctx, name, found.get("status") or {})
        if not last:
            return Result("missing", f"CronJob {name} has not succeeded yet", step="job",
                          fix=f"kubectl -n {ctx.namespace} create job --from=cronjob/{name} {name}-now")
        when = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        if ctx.now() - when > SINCE:
            return Result("drift", f"CronJob {name} last succeeded at {last}, over 24 h ago", step="job",
                          fix=f"pubkit k8s diagnose --namespace {ctx.namespace}")
        return Result("ready", f"{summary}; CronJob {name} succeeded at {last}")


PRINCIPALS = {"type": "array", "items": {"type": "string", "minLength": 1}, "x-phrase": "must list principals"}
TENANTS = "tenants.json"


class Tenant(Base):
    """A tenant of the storage service: declared in its repository's
    tenants.json with the principals given, and deployed."""
    type = "storage.tenant"
    grade = "propose"
    budget = 4.0
    schema = schema({"storage": {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+$",
                                 "x-phrase": "must be the storage repository, OWNER/NAME"},
                     "url": HTTPS, "write": PRINCIPALS, "read": PRINCIPALS, "delete": PRINCIPALS,
                     "private": {"type": "boolean"}, "heads": {"type": "boolean"}, "inbox": {"type": "boolean"}},
                    ("storage", "url"))

    def check(self, need: Need, ctx: Context) -> Result:
        name, repo = need.name, need.params["storage"]
        try:
            found: Any = ctx.gh("GET", f"repos/{repo}/contents/{TENANTS}", None) or {}
            tenants = json.loads(base64.b64decode(found.get("content", "")))
        except ApiError as e:
            if e.status == 404:
                return Result("missing", f"{repo} has no {TENANTS}", fix=f"see that {repo} is the storage repository")
            return github_refused(e, f"{TENANTS} of {repo}")
        except (ValueError, TypeError, AttributeError):
            return Result("unknown", f"{TENANTS} of {repo} is not JSON", fix=f"see {TENANTS} in {repo}")
        given = tenants.get(name) if isinstance(tenants, dict) else None
        if not isinstance(given, dict):
            return Result("missing", f"tenant {name} is not in {repo}'s {TENANTS}", fix=self.proposal(need))
        for key in ("write", "read", "delete"):
            lacking = [p for p in need.params.get(key, []) if p not in (given.get(key) or [])]
            if lacking:
                return Result("drift", f"tenant {name} in {repo} does not let {lacking[0]} {key}",
                              fix=self.proposal(need), details=[f"{key}: {p} is not listed" for p in lacking])
        url = f"{need.params['url'].rstrip('/')}/{name}/list?prefix=f/"
        status, _seconds, why = ask(ctx, url, TIMEOUT)
        if status is None:
            return Result("unknown", f"storage did not answer for {name} ({why})", fix=f"curl -sS {url}")
        if status == 404:
            return Result("drift", f"tenant {name} is declared but not deployed yet",
                          fix=f"apply {repo}'s deploy, which deploys its {TENANTS}")
        private = need.params.get("private", given.get("private") is True)
        if (private and status in (401, 403)) or (not private and 200 <= status < 300):
            return Result("ready", f"tenant {name} is in {repo}'s {TENANTS}, and storage answers {status} for it")
        return Result("drift", f"storage answers {status} for {'private ' * bool(private)}tenant {name}",
                      fix=f"curl -sS {url}")

    def proposal(self, need: Need) -> str:
        entry = {k: need.params[k] for k in ("private", "heads", "inbox", "read", "write", "delete")
                 if k in need.params}
        return (f"in {need.params['storage']}'s {TENANTS}, add or extend:\n  "
                f"{json.dumps({need.name: entry})[1:-1]}")

    def fulfil(self, need: Need, ctx: Context) -> Result:
        return Result("missing", f"tenant {need.name} is {need.params['storage']}'s to add", fix=self.proposal(need))
