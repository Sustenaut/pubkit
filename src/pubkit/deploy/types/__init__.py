"""The need types pubkit has itself, and what their checks and fulfils share."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ...protect import ApiError
from ..model import FULFIL_BUDGET, Context, Grade, Need, Ran, Result


class Base:
    """What a built-in type shares. A `singleton` is named by the namespace."""
    type = ""
    schema: dict[str, Any] = {"type": "object", "additionalProperties": False, "properties": {}}
    grade: Grade = "none"
    budget = 1.0
    fulfil_budget = FULFIL_BUDGET
    offline = False
    singleton = False
    needs_of_context: tuple[str, ...] = ()  # what requires() says, for every need of the type

    def default_name(self, params: dict[str, Any]) -> str | None:
        return None

    def depends_on(self, need: Need, needs: Mapping[str, Need]) -> list[str]:
        return []

    def check(self, need: Need, ctx: Context) -> Result:
        raise NotImplementedError

    def requires(self, need: Need) -> tuple[str, ...]:
        return self.needs_of_context

    def action(self, need: Need, ctx: Context) -> str:
        return "fulfil it"

    def fulfil(self, need: Need, ctx: Context) -> Result:
        return Result("unknown", f"{self.type} has no fulfil", fix="see the check's fix")


def failed(what: str, ran: Ran, hidden: tuple[bytes, ...] = ()) -> Result:
    """A fulfil's failure, with the last line the command said, any hidden value taken out."""
    last = (ran.stderr.strip().splitlines() or [f"exit {ran.returncode}"])[-1][:300]
    for value in hidden:
        for part in [value, *value.splitlines()]:
            if part:
                last = last.replace(part.decode(errors="replace"), "[hidden]")
    return Result("missing", what, fix=last)


def secret_of(need: Need) -> dict[str, Any] | None:
    secret = need.params.get("secret")
    return secret if isinstance(secret, dict) else None


def readers(ctx: Context, secret: str) -> list[str]:
    """The Deployments the rendered manifests show reading the Secret in the namespace."""
    from ... import k8s  # noqa: PLC0415
    try:
        found = k8s.collect(k8s.documents(ctx.rendered()), ctx.namespace)
    except Exception:  # noqa: BLE001 - no manifests to read, no Deployment to restart
        return []
    out = []
    for n in found.needs:
        workload = n.where.split(" of ", 1)[-1].split(" ", 1)[0]
        if n.namespace == ctx.namespace and n.secret == secret and workload.startswith("Deployment/"):
            out.append(workload.split("/", 1)[1])
    return sorted(set(out))


def restart(ctx: Context, secret: str) -> list[str]:
    """Restart each Deployment reading the Secret, so its pods read the new one: a detail each."""
    out = []
    for name in readers(ctx, secret):
        ran = ctx.kubectl(["-n", ctx.namespace, "rollout", "restart", f"deployment/{name}"])
        if ran.returncode == 0:
            out.append(f"restarted Deployment/{name}, which reads Secret {secret}")
        elif not not_found(ran):
            out.append(f"Deployment/{name} was not restarted: {failed('', ran).fix}")
    return out


def last_success(ctx: Context, cronjob: str, status: dict[str, Any]) -> str | None:
    """When the CronJob last succeeded: its own lastSuccessfulTime, or a Job
    made from it by hand that completed later. Such a Job, `create job
    --from=cronjob`, is not the CronJob's, so its success never moves the
    CronJob's status: the Jobs named NAME-pubkit-... (a fulfil's) and NAME-now
    (the fix's) are looked at too."""
    times = [str(status["lastSuccessfulTime"])] if status.get("lastSuccessfulTime") else []
    found, _ran = kubectl_json(ctx, "-n", ctx.namespace, "get", "job")
    for job in (found or {}).get("items") or []:
        name = str((job.get("metadata") or {}).get("name", ""))
        done = job.get("status") or {}
        if (name.startswith(f"{cronjob}-pubkit-") or name == f"{cronjob}-now") and (done.get("succeeded") or 0) >= 1 \
                and done.get("completionTime"):
            times.append(str(done["completionTime"]))
    return max(times) if times else None


def run_job(ctx: Context, cronjob: str, budget: float) -> Result:
    """A Job made from the CronJob, waited for: ready once it completes."""
    job = f"{cronjob}-pubkit-{ctx.now().strftime('%Y%m%d%H%M%S')}"[:63]
    ran = ctx.kubectl(["-n", ctx.namespace, "create", "job", f"--from=cronjob/{cronjob}", job])
    if ran.returncode != 0:
        return failed(f"a Job could not be made from CronJob {cronjob}", ran)
    wait = max(int(budget) - 20, 10)
    ran = ctx.kubectl(["-n", ctx.namespace, "wait", "--for=condition=complete", f"job/{job}", f"--timeout={wait}s"])
    if ran.returncode != 0:
        return failed(f"Job {job}, from CronJob {cronjob}, did not complete in {wait} s", ran)
    return Result("ready", f"ran Job {job} from CronJob {cronjob}, which completed")


def schema(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "object", "additionalProperties": False, "properties": properties}
    if required:
        out["required"] = list(required)
    return out


def with_secret(properties: dict[str, Any]) -> dict[str, Any]:
    from ..schema import SECRET  # noqa: PLC0415
    return {**properties, "secret": SECRET}


def kubectl_json(ctx: Context, *args: str) -> tuple[Any, Ran]:
    """kubectl's JSON answer to `args` -o json, or None, and how it ran."""
    ran = ctx.kubectl([*args, "-o", "json"])
    if ran.returncode != 0:
        return None, ran
    try:
        return json.loads(ran.stdout), ran
    except ValueError:
        return None, Ran(1, "", "kubectl's answer is not JSON")


def not_found(ran: Ran) -> bool:
    return "(NotFound)" in ran.stderr or " not found" in ran.stderr


def kubectl_refused(ran: Ran, ns: str, what: str) -> Result:
    """`unknown`, saying what access is missing, or what kubectl said."""
    last = (ran.stderr.strip().splitlines() or [f"exit {ran.returncode}"])[-1]
    if ran.returncode == 127:
        return Result("unknown", f"kubectl did not run, to read {what}", fix="install kubectl, or give --kubectl CMD")
    if "(Forbidden)" in ran.stderr or "forbidden" in ran.stderr:
        return Result("unknown", f"kubectl may not read {what} in {ns}",
                      fix=f"use a kubeconfig that may get {what} in {ns} ({last})")
    return Result("unknown", f"kubectl could not read {what} in {ns}", fix=f"kubectl said: {last}")


def github_refused(error: ApiError, what: str) -> Result:
    if error.status in (401, 403):
        return Result("unknown", f"GitHub refused to show {what}",
                      fix="sign gh in as someone who administers the repository (gh auth login)")
    return Result("unknown", f"GitHub could not be asked for {what}", fix=f"gh said: {str(error)[:200]}")
