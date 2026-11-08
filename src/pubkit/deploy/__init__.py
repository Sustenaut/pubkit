"""`pubkit deploy walk`, `pubkit deploy check` and `pubkit deploy types`:
what a service needs before it deploys, whether it has it, and making it.

    pubkit deploy walk [--fulfil] [--yes] [--unit NAME] [--kubectl CMD] [--server HOST]
                       [--zones PATH] [--journal URL] [--json] [--offline] [--namespace NS]
    pubkit deploy check ...                      walk without --fulfil
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

`walk --fulfil` then acts (walk.py): it prints the plan, a line per need
it will act on, waits for a typed `yes` (--yes skips that), asks for a
Bitwarden write token at a hidden prompt when a need wants one, and visits
the needs again, making each auto one that is missing. A walk from GitHub
Actions, or with --yes and no terminal, asks for no token, so the needs that
want one are unknown. The channels kubectl and the server are reached by are
channels.py's; a block's units, units.py's: each is walked, and journaled,
on its own.

Exit 0 when every need checked is ready, 1 otherwise, 2 for a bad block.
`--json` prints the walk as a deploy-journal/2 document instead (journal.json),
a list of them for more than one unit; `--journal URL` also writes it to
storage (store.py). No value is printed: a check names a secret, its key,
its length and its kind, never what it holds.

    load(root) -> Plan            the needs of a unit, in walk order
    check(plan, ctx) -> Journal   the walk
"""

from __future__ import annotations

import json
import os
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

from .model import Context, Journal, Need, NeedType, Result, Stop
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
    unit: str = ""


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
    from .channels import plain  # noqa: PLC0415
    return plain(command)


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
         namespace: str | None = None, types: dict[str, NeedType] | None = None, unit: str | None = None) -> Plan:
    """The needs of a unit of `root`'s deploy block (the top level's by
    default), with those the rendered manifests imply when they render, in
    walk order; `problems` when the block is not sound."""
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
    from .units import unit_block  # noqa: PLC0415
    if bad := problems(config, root, types):
        return Plan(root, config, "", {}, [], bad)
    unit = unit or config[BLOCK]["namespace"]
    block = unit_block(config[BLOCK], unit)
    ns = namespace or block["namespace"]
    rendered = ctx.rendered if ctx else renderer(root, block.get("overlay", DEFAULT_OVERLAY))
    try:
        found = k8s.collect(k8s.documents(rendered()), ns)
    except (Stop, ValueError, yaml.YAMLError):
        found = None  # k8s.secrets-declared says why
    needs, bad = build(config, types, root=root, namespace=ns, found=found, unit=unit)
    bad = bad or cycle_problems(needs, types)
    return Plan(root, config, ns, needs, [] if bad else topological(needs, types), bad, unit)


def check(plan: Plan, ctx: Context, types: dict[str, NeedType] | None = None) -> Journal:
    """Walk the plan's needs."""
    from .registry import types as registered  # noqa: PLC0415
    from .walk import walk  # noqa: PLC0415
    from .. import __version__  # noqa: PLC0415
    service = {"repository": ctx.slug, "namespace": plan.namespace, "unit": plan.unit or plan.namespace,
               "commit": git(plan.root, "rev-parse", "HEAD"), "pubkit": __version__}
    by = ctx.env.get("GITHUB_ACTOR") or git(plan.root, "config", "user.email")
    origin = "actions" if ctx.env.get("GITHUB_ACTIONS") == "true" else (
        "plane" if ctx.env.get("PUBKIT_PLANE") else "laptop")
    return walk(plan.needs, types or registered(), ctx, service, by=by, origin=origin)


def context(root: Path, config: dict[str, Any], offline: bool, kubectl_command: str | None = None,
            zones_file: Path | None = None, namespace: str | None = None, server: str | None = None,
            unit: str | None = None) -> Context:
    """The context `pubkit deploy walk` runs a unit in: the real tools."""
    from ..app_secrets import send  # noqa: PLC0415
    from ..protect import gh  # noqa: PLC0415
    from .channels import channels  # noqa: PLC0415
    from .units import unit_block  # noqa: PLC0415
    unit = unit or config[BLOCK]["namespace"]
    block = unit_block(config[BLOCK], unit)
    ns = namespace or block["namespace"]
    reach = channels(kubectl_command, server, os.environ, ns)
    return Context(root=root, config=config, block=block, namespace=ns, kubectl=reach.kubectl,
                   server=reach.server, channel=reach.name, gh=gh, send=send, http_get=http_get, env=os.environ,
                   now=lambda: datetime.now(UTC), rendered=renderer(root, block.get("overlay", DEFAULT_OVERLAY)),
                   offline=offline, slug=slug(root), zones=zones_of(config, zones_file), resolve=resolve, unit=unit)


def offline_lines(root: Path, config: dict[str, Any], ctx: Context | None = None) -> tuple[list[str], bool]:
    """For `pubkit sync`: what is wrong with the deploy block, or the offline
    walk's needs that are not ready, and whether that fails --check. An
    offline need that could not be checked (no kustomize, no hosting block)
    is said, and fails nothing."""
    from .registry import load as registry  # noqa: PLC0415
    loaded = registry()
    lines = [f"pubkit: warning: {w}" for w in loaded.warnings]
    from .units import names, problems as unit_problems  # noqa: PLC0415
    plan = load(root, config, ctx or context(root, config, offline=True), types=loaded.types)
    if plan.problems:
        return lines + [f"pubkit: {p}" for p in plan.problems], True
    bad = unit_problems(root, config[BLOCK])
    lines += [f"pubkit: {p}" for p in bad]
    failed = bool(bad)
    entries = []
    for unit in names(config[BLOCK]):
        given = ctx or context(root, config, offline=True, unit=unit)
        plan = load(root, config, given, types=loaded.types, unit=unit)
        if plan.problems:
            return lines + [f"pubkit: {p}" for p in plan.problems], True
        entries += check(plan, given, loaded.types).entries
    for need, result in entries:
        if not loaded.types[need.type].offline or result.state == "ready":
            continue
        failed = failed or result.state != "unknown"
        said = "not checked" if result.state == "unknown" else result.state
        lines.append(f"pubkit: deploy: {said}, {need.id}: {result.summary}")
        lines += [f"pubkit:   {d}" for d in result.details]
        if result.fix:
            lines.append(f"pubkit:   {result.fix}")
    return lines, failed


class BitwardenWriter:
    """Writes to the Bitwarden project with a write token, which it keeps
    only for this process: Bitwarden's SDK takes the value in memory."""

    def __init__(self, token: str, project: str) -> None:
        self._token, self.project = token, project
        self._ids: tuple[str, str] | None = None

    def write(self, name: str, value: str, note: str) -> None:
        from ..bitwarden import ensure, write  # noqa: PLC0415
        from ..github_app import _listed, bws  # noqa: PLC0415
        from ..service_account_key import find_project  # noqa: PLC0415
        if why := ensure():
            raise RuntimeError(why)
        if self._ids is None:
            self._ids = find_project(self._token, self.project)
        project, organization = self._ids
        listed = _listed(bws(self._token, "secret", "list", project, "--output", "json", "--color", "no"))
        found = [s["id"] for s in listed if s.get("key") == name]
        if len(found) > 1:
            raise RuntimeError(f"{len(found)} secrets in {self.project} are named {name}: delete all but one")
        write(self._token, organization, project, name, value, note, found[0] if found else None)


def ask(prompt: str) -> str:
    """A typed answer, the prompt on standard error, so --json's output is the document alone."""
    print(prompt, end="", file=sys.stderr, flush=True)
    return sys.stdin.readline().strip()


def project_of(root: Path, env: Any) -> str | None:
    from ..service_account_key import Stop, bitwarden_project  # noqa: PLC0415
    try:
        return bitwarden_project(root, env.get("PUBKIT_SECRETS_PROJECT"))
    except Stop:
        return None


def run_walk(root: Path, offline: bool, as_json: bool, kubectl_command: str | None, zones: Path | None,
             namespace: str | None, fulfil: bool = False, unit: str | None = None, server: str | None = None,
             yes: bool = False, journal_url: str | None = None) -> int:
    from ..cli import read_config  # noqa: PLC0415
    from .registry import load as registry  # noqa: PLC0415
    from .units import names  # noqa: PLC0415
    from .walk import counted, fulfil as act, lines, planned, unable, wants_token  # noqa: PLC0415
    config = read_config(root)
    loaded = registry()
    for warning in loaded.warnings:
        print(f"pubkit: warning: {warning}", file=sys.stderr)
    if BLOCK not in config:
        print(f"pubkit: {BLOCK}: pubkit.json has no deploy block", file=sys.stderr)
        return 2
    if fulfil and offline:
        print("pubkit: deploy: --fulfil does not run --offline", file=sys.stderr)
        return 2
    units = names(config[BLOCK]) if isinstance(config[BLOCK], dict) and "namespace" in config[BLOCK] else [None]
    if unit is not None and unit not in units:
        print(f"pubkit: deploy: --unit {unit} is no unit of the deploy block; it has {', '.join(map(str, units))}",
              file=sys.stderr)
        return 2
    walks = []
    for name in [unit] if unit else units:
        try:
            ctx = context(root, config, offline, kubectl_command, zones, namespace, server=server, unit=name)
        except (OSError, ValueError) as e:
            print(f"pubkit: deploy: --zones {zones}: {type(e).__name__}", file=sys.stderr)
            return 2
        plan = load(root, config, ctx, namespace, loaded.types, unit=name)
        if plan.problems:
            for problem in plan.problems:
                print(f"pubkit: {problem}", file=sys.stderr)
            return 2
        walks.append((ctx, check(plan, ctx, loaded.types)))
    if fulfil:
        env = walks[0][0].env
        terminal = sys.stdin.isatty() and env.get("GITHUB_ACTIONS") != "true"
        acts = [(ctx, planned(journal, loaded.types, ctx, token_coming=terminal)) for ctx, journal in walks]
        approved: set[tuple[str, str]] = set()
        if any(plan for _, plan in acts):
            print("deploy: the walk will:", file=sys.stderr)
            for ctx, plan in acts:
                for _, line in plan:
                    print(f"  {ctx.unit}: {line}" if len(walks) > 1 else f"  {line}", file=sys.stderr)
            if yes or ask('Type "yes" to do it: ') == "yes":
                approved = {(ctx.unit, i) for ctx, plan in acts for i, _ in plan}
            else:
                print("deploy: nothing was fulfilled", file=sys.stderr)
        writer = None
        if approved and terminal and any(wants_token(plan, loaded.types, ctx) for ctx, plan in acts):
            from ..github_app import ask_hidden  # noqa: PLC0415
            project = project_of(root, env)
            token = ask_hidden(f"Bitwarden access token with write access to {project} (hidden, and not kept): ")
            writer = BitwardenWriter(token, project) if token and project else None
        for ctx, journal in walks:
            ctx.bitwarden_writer = ctx.bitwarden_writer or writer
            unable(journal, loaded.types, ctx)
            act(journal, loaded.types, ctx, {i for u, i in approved if u == ctx.unit})
    documents = [journal.document() for _, journal in walks]
    if as_json:
        print(json.dumps(documents[0] if len(documents) == 1 else documents, indent=2))
    else:
        for k, (ctx, journal) in enumerate(walks):
            if len(walks) > 1:
                print(("\n" if k else "") + f"unit {ctx.unit}:")
            print("\n".join(lines(journal)))
    url = journal_url or walks[0][0].env.get("PUBKIT_JOURNAL_URL")
    if url:
        from .store import write  # noqa: PLC0415
        for document in documents:
            print(write(url, document, walks[0][0].env), file=sys.stderr)
    ready = all(r.state == "ready" for _, journal in walks for _, r in counted(journal, loaded.types, offline))
    return 0 if ready else 1


def run_check(root: Path, offline: bool, as_json: bool, kubectl_command: str | None, zones: Path | None,
              namespace: str | None, **given: Any) -> int:
    return run_walk(root, offline, as_json, kubectl_command, zones, namespace, **given)


def run_types() -> int:
    from .registry import load as registry  # noqa: PLC0415
    from .schema import COMMON, listed  # noqa: PLC0415
    loaded = registry()
    for warning in loaded.warnings:
        print(f"pubkit: warning: {warning}", file=sys.stderr)
    from .walk import fulfil_budget  # noqa: PLC0415
    rows = [("type", "grade", "budget", "offline", "from", "fulfil", "keys")]
    for name in sorted(loaded.types):
        kind = loaded.types[name]
        required = set(kind.schema.get("required", []))
        keys = [f"{k}{'' if k in required else '?'}" for k in kind.schema.get("properties", {})]
        rows.append((name, kind.grade, f"{kind.budget:g} s", "yes" if kind.offline else "no",
                     loaded.origin[name], f"{fulfil_budget(kind):g} s", ", ".join(keys) or "-"))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]) - 1)]
    for row in rows:
        print("  ".join(cell.ljust(w) for cell, w in zip(row, widths)) + "  " + row[-1])
    print(f"{len(loaded.types)} types; every need also takes {listed(list(COMMON))}, and one with a secret "
          f"fills that Kubernetes Secret, a k8s.secret the walk adds")
    return 0
