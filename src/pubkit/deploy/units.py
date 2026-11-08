"""A deploy block's units, and what `pubkit sync --check` asks of its workflow.

The top level of the deploy block is the unit its `namespace` names; `units`
adds more, each with its own namespace, overlay, gate and host:

    "deploy": {"namespace": "example", "overlay": "k8s", "gate": "DEPLOY_EXAMPLE",
               "units": {"other": {"namespace": "example-other", "overlay": "k8s-other",
                                   "gate": "DEPLOY_OTHER", "host": "other.example.eu"}},
               "needs": [...]}

They share the workflow, and every need with no `unit`; `"unit": "other"`
scopes a need to one. A unit takes nothing of the top level's namespace,
overlay, gate or host.

When the workflow calls pubkit's k8s-deploy.yml, its `deploys` lines and the
units must agree: each unit's overlay and namespace (the unit the server's
program deploys, and the namespace it deploys into) a line, and each line a
unit. And from pubkit 0.31.0, k8s-deploy.yml deploys what a run of the CI
workflow tested, so the workflow must start on `workflow_run` of it, with
`actions: read`; `pubkit bump` rewrites a trigger it moves across that
release (`retrigger`).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .schema import DEFAULT_OVERLAY, DEFAULT_WORKFLOW, WORKFLOWS

OWN = ("namespace", "overlay", "gate", "host")
K8S_DEPLOY = re.compile(r"(?P<owner>[A-Za-z0-9][A-Za-z0-9-]*)/pubkit/\.github/workflows/k8s-deploy\.ya?ml@"
                        r"(?:v|[0-9a-f]{40}[ \t]+#[ \t]*v)(?P<version>[0-9][^\s'\"]*)")
WORKFLOW_RUN = (0, 31, 0)  # the first k8s-deploy.yml that needs workflow_run
CI = "main"  # the CI workflow pubkit writes, by name
TRIGGER = "  workflow_run: { workflows: [main], types: [completed], branches: [main] }"
PERMISSIONS = "permissions: { actions: read, contents: read, packages: write }"


def names(block: dict[str, Any]) -> list[str]:
    """The units, by name: the top level's first."""
    return [block["namespace"], *sorted(block.get("units") or {})]


def unit_block(block: dict[str, Any], unit: str) -> dict[str, Any]:
    """The block as one unit sees it: its own namespace, overlay, gate and host."""
    shared = {k: v for k, v in block.items() if k not in ("units", *OWN)}
    if unit == block["namespace"]:
        own = {k: block[k] for k in OWN if k in block}
    else:
        own = dict((block.get("units") or {})[unit])
    return {**shared, **own, "unit": unit}


def release(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version.split("-")[0])[:3])


def deploys(text: str) -> list[tuple[str, str]] | None:
    """The (overlay, unit) of each deploys line of a workflow calling
    k8s-deploy.yml, or None when it calls none."""
    try:
        found = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    jobs = found.get("jobs") if isinstance(found, dict) else None
    out = None
    for job in (jobs or {}).values():
        if isinstance(job, dict) and K8S_DEPLOY.search(str(job.get("uses", ""))):
            given = str((job.get("with") or {}).get("deploys", ""))
            out = (out or []) + [tuple(line.split()[:2]) for line in given.splitlines() if len(line.split()) >= 2]
    return out


def problems(root: Path, block: dict[str, Any]) -> list[str]:
    """What is wrong with the workflow for the units: none when it calls no k8s-deploy.yml."""
    workflow = block.get("workflow", DEFAULT_WORKFLOW)
    path = root / WORKFLOWS / workflow
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out = trigger_problems(text, workflow)
    lines = deploys(text)
    if lines is None:
        return out
    units = {(unit_block(block, u).get("overlay", DEFAULT_OVERLAY), unit_block(block, u)["namespace"]): u
             for u in names(block)}
    for (overlay, ns), unit in units.items():
        if (overlay, ns) not in lines:
            out.append(f"deploy: unit {unit} ({overlay}, namespace {ns}) is no deploys line of {workflow}: "
                       f"add \"{overlay} {ns}\" to its deploys")
    for overlay, ns in lines:
        if (overlay, ns) not in units:
            out.append(f"deploy: {workflow}'s deploys line \"{overlay} {ns}\" is no unit of the deploy block: "
                       f"add it to deploy.units, or drop the line")
    return out


def trigger_problems(text: str, workflow: str) -> list[str]:
    """From k8s-deploy.yml 0.31.0, the caller starts on workflow_run of CI, with actions: read."""
    m = K8S_DEPLOY.search(text)
    if not m or release(m["version"]) < WORKFLOW_RUN:
        return []
    try:
        found = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return []
    on = found.get("on", found.get(True)) if isinstance(found, dict) else None  # YAML 1.1 reads `on` as true
    run = on.get("workflow_run") if isinstance(on, dict) else None
    runs = isinstance(run, dict) and CI in (run.get("workflows") or [])
    permissions = found.get("permissions") if isinstance(found, dict) else None
    reads = isinstance(permissions, dict) and permissions.get("actions") in ("read", "write")
    if runs and reads:
        return []
    return [f"deploy: {workflow} calls k8s-deploy.yml {m['version']}, which deploys what a run of {CI} "
            f"tested: under on, have\n{TRIGGER}\nand\n{PERMISSIONS}"]


def retrigger(text: str) -> str:
    """A caller of k8s-deploy.yml started by `push`, started by `workflow_run`
    of CI instead, with `actions: read`: the two lines trigger_problems names.
    Text it cannot read as one of those shapes is left as it is."""
    lines = text.splitlines(keepends=True)
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"^on:\s*(#.*)?$", line):
            out.append(line)
            i += 1
            body = []
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                body.append(lines[i])
                i += 1
            kept, in_push = [], False
            for b in body:
                if re.match(r"^\s{1,3}\S", b):
                    in_push = bool(re.match(r"^\s+push:", b))
                if not in_push:
                    kept.append(b)
            if not any(re.match(r"^\s+workflow_run:", b) for b in body):
                kept.insert(0, TRIGGER + "\n")
            out += kept
            continue
        m = re.match(r"^on:\s*(\[[^\]]*\]|[A-Za-z_]+|\{.*\})\s*$", line)
        if m:
            words = [w.strip() for w in re.split(r"[\[\],{}:]", m[1]) if w.strip() and w.strip() not in ("push", "{}")]
            out += ["on:\n", TRIGGER + "\n", *[f"  {w}: {{}}\n" for w in words if w != "workflow_run"]]
            i += 1
            continue
        flow = re.match(r"^permissions:\s*\{(.*)\}\s*$", line)
        if flow and "actions:" not in flow[1]:
            out.append(f"permissions: {{ actions: read, {flow[1].strip()} }}\n")
            i += 1
            continue
        if re.match(r"^permissions:\s*$", line):
            out.append(line)
            i += 1
            block = []
            while i < len(lines) and lines[i].startswith((" ", "\t")):
                block.append(lines[i])
                i += 1
            if not any(re.match(r"^\s+actions:", b) for b in block):
                block.insert(0, "  actions: read\n")
            out += block
            continue
        out.append(line)
        i += 1
    text = "".join(out)
    if not re.search(r"^permissions:", text, re.M):
        text = re.sub(r"^(jobs:)", PERMISSIONS + "\n\\1", text, count=1, flags=re.M)
    return text
