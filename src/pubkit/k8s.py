"""`pubkit k8s preflight` and `pubkit k8s diagnose`: what a deploy to
Kubernetes checks before it applies anything, and what it shows when a
rollout fails.

    pubkit k8s preflight MANIFESTS --namespace NS [--kubectl CMD] [--offline]
    pubkit k8s diagnose --namespace NS [--kubectl CMD] [--selector SEL] [--tail N]

`preflight` reads rendered manifests (YAML, or JSON where PyYAML is not
installed; `-` is standard input) and collects every Secret their pods
use: env secretKeyRef, envFrom secretRef, secret and projected secret
volumes (with the keys their items name), and imagePullSecrets, of every
container and init container of a Pod, Deployment, StatefulSet,
DaemonSet, ReplicaSet, Job or CronJob. A reference marked `optional: true`
may be missing. Each Secret is then read, once, with `kubectl get secret`,
in the workload's namespace or NS: it must be there, with every key a
reference names. A Secret the manifests themselves hold counts as there.

Some values have a shape the program reading them requires, which a
Secret's mere presence does not show. One so far: oauth2-proxy's cookie
secret (the env OAUTH2_PROXY_COOKIE_SECRET, or --cookie-secret of a
container whose image is oauth2-proxy's) must be 16, 24 or 32 bytes, as it
is or after URL-safe base64 decoding, which is oauth2-proxy's own rule;
standard base64, with + or /, is the likely mistake, and is named as such.

No value is ever printed: a problem names the Secret, the key and what
reads it, and a value's shape is described by its length alone. Any
problem fails the command, in the seconds kubectl takes, before anything
is applied. `--offline` reads no cluster: it lists what the manifests
need, and checks the values they hold themselves.

`diagnose` is for after a rollout fails: for every pod that is not ready,
or whose containers restarted, it prints the pod's phase, each container's
state, its last termination, its warning events, and the last lines of
its logs (the previous container's too, after a restart).

`kubectl` is `kubectl` unless --kubectl gives another command, such as
`k3s kubectl --kubeconfig FILE`. The file needs nothing of pubkit's, and
PyYAML only for YAML, so a server's deploy program can run it as it is:
`python3 k8s.py preflight - --namespace NS --kubectl '...'`.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

TIMEOUT = 30
# Where each kind keeps its pod spec.
POD_SPEC = {
    "Pod": ("spec",),
    "Deployment": ("spec", "template", "spec"),
    "StatefulSet": ("spec", "template", "spec"),
    "DaemonSet": ("spec", "template", "spec"),
    "ReplicaSet": ("spec", "template", "spec"),
    "ReplicationController": ("spec", "template", "spec"),
    "Job": ("spec", "template", "spec"),
    "CronJob": ("spec", "jobTemplate", "spec", "template", "spec"),
}
# Kubernetes' own names: a lower-case DNS subdomain, and a Secret's key.
NAME = re.compile(r"[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?")
KEY = re.compile(r"[-._a-zA-Z0-9]{1,253}")
AES = (16, 24, 32)
URL_SAFE = re.compile(rb"[A-Za-z0-9_-]*")
STANDARD = re.compile(rb"[A-Za-z0-9+/]*={0,2}")


def url_decoded(value: bytes) -> bytes | None:
    """`value` as oauth2-proxy decodes it: '=' trimmed from the end, then
    unpadded URL-safe base64, where line breaks are skipped (Go's
    base64.RawURLEncoding). None when it is not that."""
    text = value.rstrip(b"=").replace(b"\r", b"").replace(b"\n", b"")
    if not URL_SAFE.fullmatch(text) or len(text) % 4 == 1:
        return None
    return base64.urlsafe_b64decode(text + b"=" * (-len(text) % 4))


def cookie_secret(value: bytes) -> str | None:
    """Why oauth2-proxy refuses `value` as its cookie secret, or None. It
    takes the URL-safe base64 decoding when that is an AES key's length, and
    the bytes as they are otherwise, which must then be one."""
    decoded = url_decoded(value)
    if len(value) in AES or (decoded is not None and len(decoded) in AES):
        return None
    shape = f"{len(value)} bytes" + (f", {len(decoded)} after URL-safe base64 decoding"
                                     if decoded is not None else "")
    rule = "; oauth2-proxy needs 16, 24 or 32 bytes, as they are or after URL-safe base64 decoding"
    stripped = value.strip()
    if stripped != value and cookie_secret(stripped) is None:
        return f"{shape}, with white space around it{rule}: drop the trailing newline"
    if STANDARD.fullmatch(stripped) and (b"+" in stripped or b"/" in stripped):
        try:
            length = len(base64.b64decode(stripped, validate=True))
        except binascii.Error:
            length = 0
        if length in AES:
            return (f"{shape}: it is standard base64 (with + or /) of {length} bytes{rule}; "
                    "make it URL-safe, + as - and / as _, as `openssl rand -base64 32 | tr -- '+/' '-_'` does")
    return shape + rule


@dataclass(frozen=True)
class Shape:
    """A value with a form the program reading it requires."""
    what: str
    problem: Callable[[bytes], str | None]


COOKIE_SECRET = Shape("oauth2-proxy's cookie secret", cookie_secret)
# By the env variable that holds the value, in any container.
ENV_SHAPES = {"OAUTH2_PROXY_COOKIE_SECRET": COOKIE_SECRET}
# By a command-line flag, in a container whose image is the program's.
FLAG_SHAPES = {"oauth2-proxy": {"--cookie-secret": COOKIE_SECRET}}


@dataclass
class Need:
    """A Secret something reads: `keys` it names (none for envFrom, or a
    volume without items), whether it may be missing, and what reads it."""
    namespace: str
    secret: str
    where: str
    keys: tuple[str, ...] = ()
    optional: bool = False
    shapes: dict[str, Shape] = field(default_factory=dict)  # by key
    prefix: str | None = None  # envFrom: every key, as an env variable


@dataclass
class Literal:
    """A value with a shape written into the manifests as it is."""
    where: str
    value: bytes
    shape: Shape


@dataclass
class Found:
    """What the manifests use and hold."""
    needs: list[Need] = field(default_factory=list)
    literals: list[Literal] = field(default_factory=list)
    held: dict[tuple[str, str], dict[str, bytes]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def documents(text: str) -> list[dict[str, Any]]:
    """The objects in `text`: YAML documents, or JSON where PyYAML is not
    installed, with each List's items in its place."""
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        yaml = None
    if yaml is not None:
        docs = list(yaml.safe_load_all(text))
    else:
        try:
            loaded = json.loads(text)
        except ValueError:
            raise SystemExit("k8s: the manifests are not JSON, and PyYAML, to read YAML, "
                             "is not installed") from None
        docs = loaded if isinstance(loaded, list) else [loaded]
    out = []
    for doc in docs:
        if isinstance(doc, dict) and doc.get("kind") == "List":
            out += [d for d in doc.get("items") or [] if isinstance(d, dict)]
        elif isinstance(doc, dict):
            out.append(doc)
    return out


def dig(doc: Any, path: tuple[str, ...]) -> Any:
    for step in path:
        doc = doc.get(step) if isinstance(doc, dict) else None
    return doc


def items(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def secret_data(doc: dict[str, Any]) -> dict[str, bytes]:
    """A Secret's keys and values: data decoded, stringData as it is."""
    out = {}
    for key, value in (doc.get("data") or {}).items():
        try:
            out[key] = base64.b64decode(value or "", validate=True)
        except (binascii.Error, TypeError):
            out[key] = b""
    for key, value in (doc.get("stringData") or {}).items():
        out[key] = str(value).encode()
    return out


def flag_values(args: list[str], flags: dict[str, Shape]) -> list[tuple[str, str, Shape]]:
    """Each flag of `flags` in `args`, as --flag=VALUE or --flag VALUE."""
    out = []
    for i, arg in enumerate(args):
        name, eq, value = arg.partition("=")
        if name in flags and eq:
            out.append((name, value, flags[name]))
        elif name in flags and i + 1 < len(args):
            out.append((name, args[i + 1], flags[name]))
    return out


def collect(docs: list[dict[str, Any]], namespace: str) -> Found:
    """Every Secret the pods in `docs` read, and every Secret `docs` hold."""
    found = Found()
    for doc in docs:
        kind, meta = doc.get("kind"), doc.get("metadata") or {}
        ns = meta.get("namespace") or namespace
        if kind == "Secret" and meta.get("name"):
            found.held[(ns, meta["name"])] = secret_data(doc)
        spec = dig(doc, POD_SPEC[kind]) if kind in POD_SPEC else None
        if isinstance(spec, dict):
            pod(found, spec, ns, f"{kind}/{meta.get('name', '?')}")
    return found


def pod(found: Found, spec: dict[str, Any], ns: str, workload: str) -> None:
    """The Secrets one pod spec reads."""
    def need(secret: Any, where: str, **kw: Any) -> None:
        if not isinstance(secret, str) or not NAME.fullmatch(secret):
            found.problems.append(f"{where} names no Secret, or one that is not a Secret's name: {secret!r}")
            return
        found.needs.append(Need(ns, secret, where, **kw))

    containers = [("container", c) for c in items(spec.get("containers"))]
    containers += [("init container", c) for c in items(spec.get("initContainers"))]
    for label, c in containers:
        at = f"{workload} {label} {c.get('name', '?')}"
        env = {e.get("name"): e for e in items(c.get("env"))}
        shapes = {name: shape for name, shape in ENV_SHAPES.items() if name in env}
        image = str(c.get("image", "")).rsplit("/", 1)[-1].split(":")[0].split("@")[0]
        argv = [str(a) for a in (c.get("command") or []) + (c.get("args") or [])]
        for flag, value, shape in flag_values(argv, FLAG_SHAPES.get(image, {})):
            var = re.fullmatch(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)", value)
            if var and var[1] in env:
                shapes[var[1]] = shape
            elif not var:
                found.literals.append(Literal(f"{flag} of {at}", value.encode(), shape))
        for name, e in env.items():
            ref = dig(e, ("valueFrom", "secretKeyRef"))
            shape = shapes.get(name)
            if isinstance(ref, dict):
                key = ref.get("key")
                where = f"env {name} of {at}"
                if not isinstance(key, str) or not KEY.fullmatch(key):
                    found.problems.append(f"{where} names no key of its Secret")
                    continue
                need(ref.get("name"), where, keys=(key,), optional=ref.get("optional") is True,
                     shapes={key: shape} if shape else {})
            elif shape and "value" in e:
                found.literals.append(Literal(f"env {name} of {at}", str(e["value"]).encode(), shape))
        for e in items(c.get("envFrom")):
            ref = e.get("secretRef")
            if isinstance(ref, dict):
                prefix = str(e.get("prefix") or "")
                need(ref.get("name"), f"envFrom of {at}", optional=ref.get("optional") is True,
                     prefix=prefix)
    for v in items(spec.get("volumes")):
        where = f"volume {v.get('name', '?')} of {workload}"
        sources = [v["secret"] | {"name": v["secret"].get("secretName")}] if isinstance(
            v.get("secret"), dict) else []
        sources += [s["secret"] for s in items(dig(v, ("projected", "sources")))
                    if isinstance(s.get("secret"), dict)]
        for s in sources:
            keys = tuple(str(i.get("key")) for i in items(s.get("items")))
            need(s.get("name"), where, keys=keys, optional=s.get("optional") is True)
    for p in items(spec.get("imagePullSecrets")):
        need(p.get("name"), f"imagePullSecrets of {workload}")


class Cluster:
    """Secrets read through kubectl, each once."""

    def __init__(self, kubectl: list[str]) -> None:
        self.kubectl = kubectl
        self.read: dict[tuple[str, str], dict[str, bytes] | str | None] = {}

    def secret(self, ns: str, name: str) -> dict[str, bytes] | str | None:
        """The Secret's keys and values, None when it is not there, or why
        it could not be read."""
        if (ns, name) not in self.read:
            self.read[(ns, name)] = self.get(ns, name)
        return self.read[(ns, name)]

    def get(self, ns: str, name: str) -> dict[str, bytes] | str | None:
        try:
            done = subprocess.run([*self.kubectl, "-n", ns, "get", "secret", name, "-o", "json"],
                                  capture_output=True, text=True, timeout=TIMEOUT, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            return f"kubectl did not run: {error}"
        if done.returncode == 0:
            try:
                return secret_data(json.loads(done.stdout))
            except ValueError:
                return "kubectl's answer is not JSON"
        err = done.stderr.strip().splitlines()[-1:] or [f"exit {done.returncode}"]
        if "(NotFound)" in done.stderr or " not found" in done.stderr:
            return None
        if "(Forbidden)" in done.stderr or "forbidden" in done.stderr:
            return (f"kubectl may not read Secrets in {ns}: the deploy identity needs get on "
                    f"secrets there ({err[0]})")
        return f"kubectl failed: {err[0]}"


def preflight(text: str, namespace: str, cluster: Cluster | None) -> int:
    """Check the Secrets the manifests in `text` read; 1 on any problem."""
    found = collect(documents(text), namespace)
    problems = list(found.problems)
    for lit in found.literals:
        if (why := lit.shape.problem(lit.value)) is not None:
            problems.append(f"{lit.where}, {lit.shape.what}: {why}")
    by_secret: dict[tuple[str, str], list[Need]] = {}
    for n in found.needs:
        by_secret.setdefault((n.namespace, n.secret), []).append(n)
    unchecked = []
    for (ns, name), needs in sorted(by_secret.items()):
        held = found.held.get((ns, name))
        data = held if held is not None else (cluster.secret(ns, name) if cluster else False)
        if data is False:
            keys = sorted({k for n in needs for k in n.keys})
            unchecked.append(f"{name}" + (f" ({', '.join(keys)})" if keys else ""))
            continue
        if isinstance(data, str):
            problems.append(f"Secret {name} in {ns}: {data}")
            continue
        required = [n for n in needs if not n.optional]
        if data is None:
            if required:
                problems.append(f"Secret {name} is not in namespace {ns}; "
                                f"{'; '.join(n.where for n in required)} read it")
            else:
                print(f"Secret {name} is not in {ns}, and every reference to it is optional")
            continue
        bad = check_keys(name, needs, data)
        problems += bad
        if not bad:
            keys = sorted({k for n in needs for k in n.keys})
            print(f"ok: Secret {name} in {ns}" + (f", keys {', '.join(keys)}" if keys else ""))
    if unchecked:
        print(f"not read (offline), the manifests need the Secrets {', '.join(unchecked)}")
    for p in problems:
        print(f"error: {p}")
    count = len(by_secret)
    if problems:
        print(f"preflight: {len(problems)} problem{'s' * (len(problems) != 1)} with the Secrets "
              f"in {namespace}: fix them before this deploys", file=sys.stderr)
        return 1
    print(f"preflight: the manifests read {count} Secret{'s' * (count != 1)}, "
          + ("listed above" if cluster is None else "and each is as they need"))
    return 0


def check_keys(name: str, needs: list[Need], data: dict[str, bytes]) -> list[str]:
    """What is wrong with one Secret that is there, for what reads it."""
    out = []
    for n in needs:
        for key in n.keys:
            if key not in data:
                if not n.optional:
                    out.append(f"Secret {name} has no key {key}, which {n.where} reads")
                continue
            shape = n.shapes.get(key)
            if shape and (why := shape.problem(data[key])) is not None:
                out.append(f"Secret {name} key {key} ({n.where}), {shape.what}: {why}")
        if n.prefix is not None:
            for key, value in sorted(data.items()):
                shape = ENV_SHAPES.get(n.prefix + key)
                if shape and (why := shape.problem(value)) is not None:
                    out.append(f"Secret {name} key {key} ({n.where}, as {n.prefix + key}), "
                               f"{shape.what}: {why}")
    return out


def kubectl_json(kubectl: list[str], ns: str, *args: str) -> dict[str, Any]:
    done = subprocess.run([*kubectl, "-n", ns, *args, "-o", "json"], capture_output=True,
                          text=True, timeout=TIMEOUT, check=False)
    if done.returncode != 0:
        print(f"kubectl {' '.join(args)}: {done.stderr.strip()}")
        return {}
    try:
        return json.loads(done.stdout)
    except ValueError:
        return {}


def diagnose(kubectl: list[str], ns: str, selector: str, tail: int) -> int:
    """Print what is wrong with the pods in `ns` that are not ready or
    restarted. Always 0: it explains a failure, and is not one."""
    args = ["get", "pods"] + (["-l", selector] if selector else [])
    pods = kubectl_json(kubectl, ns, *args).get("items") or []
    events = kubectl_json(kubectl, ns, "get", "events").get("items") or []
    shown = 0
    for p in pods:
        name, status = dig(p, ("metadata", "name")), p.get("status") or {}
        statuses = items(status.get("initContainerStatuses")) + items(status.get("containerStatuses"))
        ready = any(c.get("type") == "Ready" and c.get("status") == "True"
                    for c in items(status.get("conditions")))
        if ready and not any(s.get("restartCount") for s in statuses):
            continue
        shown += 1
        print(f"--- pod {name}: {status.get('phase', '?')}"
              + (f", {status['reason']}" if status.get("reason") else ""))
        for s in statuses:
            state = s.get("state") or {}
            now = next(iter(state), "?")
            detail = state.get(now) or {}
            why = " ".join(str(detail[k]) for k in ("reason", "message") if detail.get(k))
            last = dig(s, ("lastState", "terminated")) or {}
            print(f"  container {s.get('name')}: {now}" + (f" ({why})" if why else "")
                  + f", {s.get('restartCount', 0)} restarts"
                  + (f", last exit {last.get('exitCode')} {last.get('reason', '')}".rstrip()
                     if last else ""))
        for e in [e for e in events if dig(e, ("involvedObject", "name")) == name
                  and e.get("type") == "Warning"][-10:]:
            print(f"  event {e.get('reason')}: {e.get('message', '').strip()}")
        for s in statuses:
            for previous in ([True] if s.get("restartCount") else []) + [False]:
                done = subprocess.run([*kubectl, "-n", ns, "logs", name, "-c", str(s.get("name")),
                                       f"--tail={tail}", *(["--previous"] if previous else [])],
                                      capture_output=True, text=True, timeout=TIMEOUT, check=False)
                lines = (done.stdout if done.returncode == 0 else done.stderr).rstrip()
                if lines:
                    print(f"  logs of {s.get('name')}{' (previous)' if previous else ''}:")
                    print("\n".join(f"    {line}" for line in lines.splitlines()))
    if not shown:
        print(f"diagnose: every pod in {ns} is ready and none restarted")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pubkit k8s", description="Check the Secrets rendered manifests "
                                     "read before a deploy, or show why a rollout failed.")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("preflight", help="every Secret the manifests read is there, with its "
                                              "keys, and values of a known shape have it")
    p.add_argument("manifests", help="the rendered manifests, or - for standard input")
    p.add_argument("--offline", action="store_true",
                   help="read no cluster: list what is needed, and check what the manifests hold")
    p = commands.add_parser("diagnose", help="what is wrong with the pods that are not ready")
    p.add_argument("--selector", default="", help="only the pods with these labels, such as app=web")
    p.add_argument("--tail", type=int, default=30, help="the lines of each container's logs")
    for p in commands.choices.values():
        p.add_argument("--namespace", "-n", required=True, help="the namespace to read")
        p.add_argument("--kubectl", default="kubectl",
                       help="the kubectl command, such as 'k3s kubectl --kubeconfig FILE'")
    args = parser.parse_args(argv)
    if not NAME.fullmatch(args.namespace):
        parser.error(f"--namespace: {args.namespace!r} is not a namespace's name")
    kubectl = shlex.split(args.kubectl)
    if args.command == "diagnose":
        return diagnose(kubectl, args.namespace, args.selector, args.tail)
    if args.manifests == "-":
        text = sys.stdin.read()
    else:
        with open(args.manifests, encoding="utf-8") as f:
            text = f.read()
    return preflight(text, args.namespace, None if args.offline else Cluster(kubectl))


if __name__ == "__main__":
    sys.exit(main())
