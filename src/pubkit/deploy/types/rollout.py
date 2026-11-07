"""github.gate and deploy.rollout: whether the deploy may run, and whether it did.

The gate is the repository variable that must be "on" for the deploy
workflow to deploy. It is walked after every need but the rollout and the
service's health, and judged by them: open while all are ready, closed
while one is not; open too early when one is missing; closed with nothing
left to wait for is the one step left to take.
"""

from __future__ import annotations

from ...protect import ApiError
from ..model import Context, Need, Result
from . import Base, github_refused, kubectl_json, kubectl_refused, schema
from .github import GITHUB_NAME, NO_ORIGIN, gate_value

LAST = ("deploy.rollout", "service.health")


class Gate(Base):
    type = "github.gate"
    grade = "auto"
    budget = 2.0
    singleton = True
    schema = schema({"variable": GITHUB_NAME}, ("variable",))

    def check(self, need: Need, ctx: Context) -> Result:
        if not ctx.slug:
            return NO_ORIGIN
        variable = need.params["variable"]
        value, refused = gate_value(ctx, variable)
        if refused:
            return refused
        earlier = {i: r for i, r in ctx.results.items()
                   if i != need.id and ctx.needs.get(i, need).type not in LAST}
        late = sorted(i for i, r in earlier.items() if r.state != "ready")
        waiting = sorted(i for i, r in earlier.items() if r.state in ("missing", "blocked"))
        set_on = f"gh variable set {variable} -R {ctx.slug} --body on"
        if value == "on":
            if waiting:
                return Result("drift", f"{variable} is on while {len(waiting)} need"
                                       f"{'s are' if len(waiting) != 1 else ' is'} missing",
                              fix=f"gh variable set {variable} -R {ctx.slug} --body off, until they are ready",
                              details=[f"missing: {i}" for i in waiting])
            if late:
                return Result("unknown", f"{variable} is on, and {len(late)} need"
                                         f"{'s' if len(late) != 1 else ''} could not be shown ready",
                              fix="see the needs above", details=[f"not ready: {i}" for i in late])
            return Result("ready", f"{variable} is on, and the {len(earlier)} needs before it are ready")
        shown = "unset" if value is None else "not on"
        if late:
            return Result("blocked", f"{variable} is {shown}, and waits for {len(late)} need"
                                     f"{'s' if len(late) != 1 else ''}",
                          fix=f"once they are ready: {set_on}", details=[f"not ready: {i}" for i in late])
        return Result("missing", f"{variable} is {shown}, and every need before it is ready", fix=set_on)


class Rollout(Base):
    """The latest deploy run on the default branch passed on its head, and
    every Deployment in the namespace has its replicas ready."""
    type = "deploy.rollout"
    grade = "auto"
    budget = 5.0
    singleton = True

    def check(self, need: Need, ctx: Context) -> Result:
        if not ctx.slug:
            return NO_ORIGIN
        workflow = ctx.block.get("workflow", "deploy.yml")
        try:
            branch = (ctx.gh("GET", f"repos/{ctx.slug}", None) or {}).get("default_branch", "main")
            head = ((ctx.gh("GET", f"repos/{ctx.slug}/branches/{branch}", None) or {}).get("commit") or {}).get("sha", "")
            runs = []
            for event in ("push", "workflow_run"):
                found = ctx.gh("GET", f"repos/{ctx.slug}/actions/workflows/{workflow}/runs"
                                      f"?branch={branch}&event={event}&per_page=1", None) or {}
                runs += found.get("workflow_runs") or []
        except ApiError as e:
            if e.status == 404:
                return Result("missing", f"{workflow} is not a workflow of {ctx.slug}",
                              fix=f"push .github/workflows/{workflow} to {branch}")
            return github_refused(e, f"the runs of {workflow}")
        if not runs:
            return Result("missing", f"{workflow} has not run on {branch}",
                          fix=f"gh workflow run {workflow} -R {ctx.slug} --ref {branch}")
        run = max(runs, key=lambda r: str(r.get("created_at", "")))
        sha = str(run.get("head_sha", ""))
        if run.get("conclusion") != "success":
            how = run.get("conclusion") or run.get("status") or "unknown"
            return Result("drift", f"the last deploy, of {sha[:7]}, is {how}",
                          fix=f"see why in gh run view {run.get('id')} -R {ctx.slug}, then rerun it")
        if head and sha != head:
            return Result("drift", f"{branch} is at {head[:7]}, the last passed deploy is of {sha[:7]}",
                          fix=f"wait for {workflow} on {head[:7]}, or gh workflow run {workflow} -R {ctx.slug}")
        found, ran = kubectl_json(ctx, "-n", ctx.namespace, "get", "deployment")
        if found is None:
            return kubectl_refused(ran, ctx.namespace, "deployments")
        late = []
        deployments = [d for d in found.get("items") or [] if isinstance(d, dict)]
        for d in deployments:
            want = (d.get("spec") or {}).get("replicas", 1)
            have = (d.get("status") or {}).get("readyReplicas", 0)
            if have != want:
                late.append(f"Deployment/{(d.get('metadata') or {}).get('name')} has {have} of {want} ready")
        if late:
            return Result("drift", late[0], details=late,
                          fix=f"pubkit k8s diagnose --namespace {ctx.namespace}")
        return Result("ready", f"{branch}'s {sha[:7]} is deployed, and {len(deployments)} Deployment"
                               f"{'s are' if len(deployments) != 1 else ' is'} ready")
