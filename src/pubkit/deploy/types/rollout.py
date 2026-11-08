"""github.gate and deploy.rollout: whether the deploy may run, and whether it did.

The gate is the repository variable that must be "on" for the deploy
workflow to deploy. It is walked after every need but the rollout and the
service's health, and judged by them: open while all are ready, closed
while one is not; open too early when one is missing; closed with nothing
left to wait for is the one step left to take.

Fulfilled, the gate is set on, only while every need before it is ready,
and the deploy workflow is run on the default branch; the rollout runs it
(unless the gate did, in this walk) and waits for that run to end.
"""

from __future__ import annotations

from typing import Any

from ...protect import ApiError
from ..model import Context, Need, Result
from . import Base, github_refused, kubectl_json, kubectl_refused, schema
from .github import GITHUB_NAME, NO_ORIGIN, gate_value

LAST = ("deploy.rollout", "service.health")
POLL = 15.0  # seconds between looks at a run a fulfil waits for


def default_branch(ctx: Context) -> str:
    return (ctx.gh("GET", f"repos/{ctx.slug}", None) or {}).get("default_branch", "main")


def dispatch(ctx: Context, workflow: str, branch: str) -> str:
    """Run the workflow on the branch; when, as runs' created_at say it."""
    at = ctx.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    ctx.gh("POST", f"repos/{ctx.slug}/actions/workflows/{workflow}/dispatches", {"ref": branch})
    ctx.dispatched[workflow] = ctx.now()
    return at


def set_variable(ctx: Context, name: str, value: str) -> None:
    try:
        ctx.gh("PATCH", f"repos/{ctx.slug}/actions/variables/{name}", {"name": name, "value": value})
    except ApiError as e:
        if e.status != 404:
            raise
        ctx.gh("POST", f"repos/{ctx.slug}/actions/variables", {"name": name, "value": value})


class Gate(Base):
    type = "github.gate"
    grade = "auto"
    budget = 2.0
    fulfil_budget = 30.0
    singleton = True
    schema = schema({"variable": GITHUB_NAME}, ("variable",))

    def action(self, need: Need, ctx: Context) -> str:
        workflow = ctx.block.get("workflow", "deploy.yml")
        return f"set {need.params['variable']} on, once every need before it is ready, and run {workflow}"

    def fulfil(self, need: Need, ctx: Context) -> Result:
        variable, workflow = need.params["variable"], ctx.block.get("workflow", "deploy.yml")
        late = sorted(i for i, r in ctx.results.items()
                      if i != need.id and ctx.needs.get(i, need).type not in LAST and r.state != "ready")
        if late:
            return Result("missing", f"{variable} stays off: {late[0]} is not ready", fix="see the needs above")
        try:
            set_variable(ctx, variable, "on")
            branch = default_branch(ctx)
            dispatch(ctx, workflow, branch)
        except ApiError as e:
            refused = github_refused(e, f"variable {variable}")
            return Result("missing", f"GitHub refused to set {variable} or run {workflow}", fix=refused.fix)
        return Result("ready", f"set {variable} on, and ran {workflow} on {branch}")

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
                                       f"{'s are' if len(waiting) != 1 else ' is'} missing", fixable=False,
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
    fulfil_budget = 900.0
    singleton = True

    def action(self, need: Need, ctx: Context) -> str:
        return f"run {ctx.block.get('workflow', 'deploy.yml')} on the default branch, and wait for it"

    def fulfil(self, need: Need, ctx: Context) -> Result:
        workflow = ctx.block.get("workflow", "deploy.yml")
        try:
            branch = default_branch(ctx)
            given = ctx.dispatched.get(workflow)
            at = given.strftime("%Y-%m-%dT%H:%M:%SZ") if given else dispatch(ctx, workflow, branch)
            for _ in range(int((self.fulfil_budget - 2 * POLL) / POLL)):
                ctx.sleep(POLL)
                found: Any = ctx.gh("GET", f"repos/{ctx.slug}/actions/workflows/{workflow}/runs"
                                           f"?branch={branch}&event=workflow_dispatch&per_page=5", None) or {}
                runs = [r for r in found.get("workflow_runs") or [] if str(r.get("created_at", "")) >= at]
                if not runs or runs[0].get("status") != "completed":
                    continue
                run = runs[0]
                if run.get("conclusion") != "success":
                    return Result("drift", f"{workflow} run {run.get('id')} ended {run.get('conclusion')}",
                                  fix=f"gh run view {run.get('id')} -R {ctx.slug} --log-failed")
                return Result("ready", f"ran {workflow} on {branch}: run {run.get('id')} passed")
        except ApiError as e:
            return Result("missing", f"GitHub refused to run {workflow}", fix=github_refused(e, workflow).fix)
        return Result("drift", f"{workflow} on {branch} did not end in time",
                      fix=f"gh run list -R {ctx.slug} --workflow {workflow}")

    def check(self, need: Need, ctx: Context) -> Result:
        if not ctx.slug:
            return NO_ORIGIN
        workflow = ctx.block.get("workflow", "deploy.yml")
        try:
            branch = (ctx.gh("GET", f"repos/{ctx.slug}", None) or {}).get("default_branch", "main")
            head = ((ctx.gh("GET", f"repos/{ctx.slug}/branches/{branch}", None) or {}).get("commit") or {}).get("sha", "")
            runs = []
            for event in ("push", "workflow_run", "workflow_dispatch"):
                found = ctx.gh("GET", f"repos/{ctx.slug}/actions/workflows/{workflow}/runs"
                                      f"?branch={branch}&event={event}&per_page=1", None) or {}
                runs += found.get("workflow_runs") or []
        except ApiError as e:
            if e.status == 404:
                return Result("missing", f"{workflow} is not a workflow of {ctx.slug}", fixable=False,
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
