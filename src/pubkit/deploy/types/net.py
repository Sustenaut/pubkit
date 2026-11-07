"""upstream, dns.site and service.health: what answers on the network.

dns.site's offline part reads the hosting block, of this repository's
pubkit.json or of the one --zones names (the repository that keeps the
zone): the host must be a site of one of its zones. Online, its A or AAAA
records must resolve.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

from ..model import Context, Need, Result
from . import Base, kubectl_json, kubectl_refused, schema

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
        if status != 200:
            return Result("missing", f"{need.name} answers {status} at {path_of(url)}",
                          fix=f"{need.name} must answer 200 before this deploys: curl -sS {url}")
        return Result("ready", f"{need.name} answers 200 at {path_of(url)} in {seconds:.1f} s")


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
    singleton = True
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
            return Result("missing", f"{host} {said} at {path_of(url)}",
                          fix=f"pubkit k8s diagnose --namespace {ctx.namespace}")
        summary = f"{host} answers 200 at {path_of(url)} in {seconds:.1f} s"
        job = need.params.get("job")
        if not job:
            return Result("ready", summary)
        name = job.split("/", 1)[1]
        found, ran = kubectl_json(ctx, "-n", ctx.namespace, "get", "cronjob", name)
        if found is None:
            if ran.returncode != 0 and ("(NotFound)" in ran.stderr or " not found" in ran.stderr):
                return Result("missing", f"CronJob {name} is not in {ctx.namespace}",
                              fix=f"see that the manifests in {ctx.block.get('overlay', 'k8s')} hold it")
            return kubectl_refused(ran, ctx.namespace, "cronjobs")
        last = (found.get("status") or {}).get("lastSuccessfulTime")
        if not last:
            return Result("missing", f"CronJob {name} has not succeeded yet",
                          fix=f"kubectl -n {ctx.namespace} create job --from=cronjob/{name} {name}-now")
        when = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        if ctx.now() - when > SINCE:
            return Result("drift", f"CronJob {name} last succeeded at {last}, over 24 h ago",
                          fix=f"pubkit k8s diagnose --namespace {ctx.namespace}")
        return Result("ready", f"{summary}; CronJob {name} succeeded at {last}")
