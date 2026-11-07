"""`pubkit deploy check` and `pubkit deploy types`: what a service needs
before it deploys, and whether it has it.

    pubkit deploy check [--offline] [--json] [--kubectl CMD] [--zones PATH] [--namespace NS]
    pubkit deploy types

pubkit.json's `deploy` block names the namespace, and maybe the host, the
deploy workflow, the gate variable, the overlay and needs nothing implies.
The block, the secrets, github_app and variables blocks, and the Secrets the
rendered overlay reads imply the rest (implied.py). `check` walks every need
in order (order.py, walk.py) and prints its state: ready, missing, drift,
blocked or unknown. It reads, and changes nothing. `--offline` checks only
what needs no network: the rendered manifests against the needs, the
manifest files, and the host against the hosting block. `pubkit sync
--check` runs that too, so a block that disagrees with the manifests fails CI.

Exit 0 when every need checked is ready, 1 otherwise, 2 for a bad block.
`--json` prints the walk as a deploy-journal/1 document instead (journal.json).
No value is printed: a check names a secret, its key, its length and its
kind, never what it holds.

    load(root) -> Plan            the needs, in walk order
    check(plan, ctx) -> Journal   the walk
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from .model import Context, Journal, Need, NeedType, Ran, Result, Stop
from .schema import BLOCK, DEFAULT_OVERLAY

__all__ = ["Context", "Journal", "Need", "NeedType", "Plan", "Result", "check", "load"]
RENDER_TIMEOUT = 60
TOOL_TIMEOUT = 30


@dataclass
class Plan:
    root: Path
    config: dict[str, Any]
    namespace: str
    needs: dict[str, Need]
    order: list[str]
    problems: list[str]


def render(root: Path, overlay: str) -> str:
    """The overlay as kustomize builds it: kustomize, or kubectl's own."""
    where = root / overlay
    if shutil.which("kustomize"):
        argv = ["kustomize", "build", str(where)]
    elif shutil.which("kubectl"):
        argv = ["kubectl", "kustomize", str(where)]
    else:
        raise Stop(f"neither kustomize nor kubectl is installed, to render {overlay}", missing_tool=True)
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=RENDER_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Stop(f"{argv[0]} did not render {overlay} ({type(e).__name__})", missing_tool=True) from None
    if done.returncode != 0:
        last = (done.stderr.strip().splitlines() or [f"exit {done.returncode}"])[-1]
        raise Stop(f"{' '.join(argv[:2])} {overlay} failed: {last[:300]}")
    return done.stdout


def renderer(root: Path, overlay: str) -> Any:
    """render, once."""
    return cache(lambda: render(root, overlay))


def kubectl(command: str) -> Any:
    base = shlex.split(command)

    def run(args: list[str]) -> Ran:
        try:
            done = subprocess.run([*base, *args], capture_output=True, text=True, timeout=TOOL_TIMEOUT, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            return Ran(127, "", f"{base[0] if base else 'kubectl'} did not run ({type(e).__name__})")
        return Ran(done.returncode, done.stdout, done.stderr)
    return run


def http_get(url: str, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": "pubkit-deploy-check"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - an https:// need
            return response.status, response.read(1 << 16)
    except urllib.error.HTTPError as e:
        return e.code, b""
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise TimeoutError from None
        raise ConnectionError(type(e.reason).__name__) from None


def resolve(host: str, rtype: str) -> list[str] | None:
    from ..dns import NAME, checked, checked_type  # noqa: PLC0415
    if not shutil.which("dig"):
        return None
    done = subprocess.run(["dig", "+short", "-q", checked(f"{host}."), "-t", checked_type(rtype)],
                          capture_output=True, text=True, timeout=TOOL_TIMEOUT, check=False)
    return [line for line in done.stdout.split() if line and not NAME.fullmatch(line)]


def git(root: Path, *args: str) -> str:
    try:
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    except OSError:
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def slug(root: Path) -> str:
    """OWNER/NAME of the GitHub repository origin points at, or ""."""
    import re  # noqa: PLC0415
    m = re.search(r"github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$", git(root, "remote", "get-url", "origin"))
    return m.group(1) if m else ""


def zones_of(config: dict[str, Any], zones_file: Path | None) -> dict[str, Any] | None:
    """The hosting block's zones: this repository's, and --zones's."""
    out: dict[str, Any] = {}
    found = False
    for given in [config] + ([json.loads(zones_file.read_text(encoding="utf-8"))] if zones_file else []):
        hosting = given.get("hosting") if isinstance(given, dict) else None
        if isinstance(hosting, dict) and isinstance(hosting.get("zones"), dict):
            out |= hosting["zones"]
            found = True
    return out if found else None


def load(root: Path, config: dict[str, Any] | None = None, ctx: Context | None = None,
         namespace: str | None = None, types: dict[str, NeedType] | None = None) -> Plan:
    """The needs of `root`'s deploy block, with those the rendered manifests
    imply when they render, in walk order; `problems` when the block is not sound."""
    from .. import k8s  # noqa: PLC0415
    from .implied import build  # noqa: PLC0415
    from .order import cycle_problems, topological  # noqa: PLC0415
    from .registry import types as registered  # noqa: PLC0415
    from .schema import problems  # noqa: PLC0415
    if config is None:
        from ..cli import read_config  # noqa: PLC0415
        config = read_config(root)
    types = types or registered()
    if BLOCK not in config:
        return Plan(root, config, "", {}, [], [f"{BLOCK}: pubkit.json has no deploy block"])
    if bad := problems(config, root, types):
        return Plan(root, config, "", {}, [], bad)
    ns = namespace or config[BLOCK]["namespace"]
    rendered = ctx.rendered if ctx else renderer(root, config[BLOCK].get("overlay", DEFAULT_OVERLAY))
    try:
        found = k8s.collect(k8s.documents(rendered()), ns)
    except (Stop, ValueError, yaml.YAMLError):
        found = None  # k8s.secrets-declared says why
    needs, bad = build(config, types, root=root, namespace=ns, found=found)
    bad = bad or cycle_problems(needs, types)
    return Plan(root, config, ns, needs, [] if bad else topological(needs, types), bad)


def check(plan: Plan, ctx: Context, types: dict[str, NeedType] | None = None) -> Journal:
    """Walk the plan's needs."""
    from .registry import types as registered  # noqa: PLC0415
    from .walk import walk  # noqa: PLC0415
    from .. import __version__  # noqa: PLC0415
    service = {"repository": ctx.slug, "namespace": plan.namespace, "commit": git(plan.root, "rev-parse", "HEAD"),
               "pubkit": __version__}
    by = ctx.env.get("GITHUB_ACTOR") or git(plan.root, "config", "user.email")
    origin = "actions" if ctx.env.get("GITHUB_ACTIONS") == "true" else (
        "plane" if ctx.env.get("PUBKIT_PLANE") else "laptop")
    return walk(plan.needs, types or registered(), ctx, service, by=by, origin=origin)


def context(root: Path, config: dict[str, Any], offline: bool, kubectl_command: str = "kubectl",
            zones_file: Path | None = None, namespace: str | None = None) -> Context:
    """The context `pubkit deploy check` runs in: the real tools."""
    from ..protect import gh  # noqa: PLC0415
    block = config[BLOCK]
    return Context(root=root, config=config, block=block, namespace=namespace or block["namespace"],
                   kubectl=kubectl(kubectl_command), gh=gh, http_get=http_get, env=os.environ,
                   now=lambda: datetime.now(UTC), rendered=renderer(root, block.get("overlay", DEFAULT_OVERLAY)),
                   offline=offline, slug=slug(root), zones=zones_of(config, zones_file), resolve=resolve)


def offline_lines(root: Path, config: dict[str, Any], ctx: Context | None = None) -> tuple[list[str], bool]:
    """For `pubkit sync`: what is wrong with the deploy block, or the offline
    walk's needs that are not ready, and whether that fails --check. An
    offline need that could not be checked (no kustomize, no hosting block)
    is said, and fails nothing."""
    from .registry import load as registry  # noqa: PLC0415
    loaded = registry()
    lines = [f"pubkit: warning: {w}" for w in loaded.warnings]
    ctx = ctx or context(root, config, offline=True)
    plan = load(root, config, ctx, types=loaded.types)
    if plan.problems:
        return lines + [f"pubkit: {p}" for p in plan.problems], True
    journal = check(plan, ctx, loaded.types)
    failed = False
    for need, result in journal.entries:
        if not loaded.types[need.type].offline or result.state == "ready":
            continue
        failed = failed or result.state != "unknown"
        said = "not checked" if result.state == "unknown" else result.state
        lines.append(f"pubkit: deploy: {said}, {need.id}: {result.summary}")
        lines += [f"pubkit:   {d}" for d in result.details]
        if result.fix:
            lines.append(f"pubkit:   {result.fix}")
    return lines, failed


def run_check(root: Path, offline: bool, as_json: bool, kubectl_command: str, zones: Path | None,
              namespace: str | None) -> int:
    from ..cli import read_config  # noqa: PLC0415
    from .registry import load as registry  # noqa: PLC0415
    from .walk import counted, lines  # noqa: PLC0415
    config = read_config(root)
    loaded = registry()
    for warning in loaded.warnings:
        print(f"pubkit: warning: {warning}", file=sys.stderr)
    if BLOCK not in config:
        print(f"pubkit: {BLOCK}: pubkit.json has no deploy block", file=sys.stderr)
        return 2
    try:
        ctx = context(root, config, offline, kubectl_command, zones, namespace)
    except (OSError, ValueError) as e:
        print(f"pubkit: deploy: --zones {zones}: {type(e).__name__}", file=sys.stderr)
        return 2
    plan = load(root, config, ctx, namespace, loaded.types)
    if plan.problems:
        for problem in plan.problems:
            print(f"pubkit: {problem}", file=sys.stderr)
        return 2
    journal = check(plan, ctx, loaded.types)
    print(journal.json() if as_json else "\n".join(lines(journal)))
    return 0 if all(r.state == "ready" for _, r in counted(journal, loaded.types, offline)) else 1


def run_types() -> int:
    from .registry import load as registry  # noqa: PLC0415
    from .schema import COMMON, listed  # noqa: PLC0415
    loaded = registry()
    for warning in loaded.warnings:
        print(f"pubkit: warning: {warning}", file=sys.stderr)
    rows = [("type", "grade", "budget", "offline", "from", "keys")]
    for name in sorted(loaded.types):
        kind = loaded.types[name]
        required = set(kind.schema.get("required", []))
        keys = [f"{k}{'' if k in required else '?'}" for k in kind.schema.get("properties", {})]
        rows.append((name, kind.grade, f"{kind.budget:g} s", "yes" if kind.offline else "no",
                     loaded.origin[name], ", ".join(keys) or "-"))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]) - 1)]
    for row in rows:
        print("  ".join(cell.ljust(w) for cell, w in zip(row, widths)) + "  " + row[-1])
    print(f"{len(loaded.types)} types; every need also takes {listed(list(COMMON))}")
    return 0
