"""k8s.namespace, k8s.secret, k8s.secrets-declared and k8s.manifests:
what the cluster must hold, read through kubectl, and what the rendered
manifests read, which needs no cluster. They use pubkit.k8s, which needs
nothing of this package's."""

from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path
from typing import Any

import yaml

from ... import k8s
from ..model import Context, Need, Result, Stop
from . import Base, kubectl_json, kubectl_refused, not_found, schema

KEYS = {"type": "array", "items": {"type": "string", "pattern": r"^[-._a-zA-Z0-9]{1,253}$"},
        "x-phrase": "must list the Secret's keys"}


def hex_32(value: bytes) -> str | None:
    if re.fullmatch(rb"[0-9a-f]{64}", value):
        return None
    return f"{len(value)} bytes; it must be 64 lower-case hex digits, 32 bytes"


def base64_32(value: bytes) -> str | None:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return f"{len(value)} bytes, not base64; it must be base64 of 32 bytes"
    if len(decoded) != 32:
        return f"{len(value)} bytes, base64 of {len(decoded)} bytes; it must be base64 of 32 bytes"
    return None


# The shapes a value may be required to have, by the name a need gives.
SHAPES = {"oauth2-proxy-cookie": k8s.COOKIE_SECRET,
          "hex-32": k8s.Shape("32 bytes as hex", hex_32),
          "base64-32": k8s.Shape("32 bytes as base64", base64_32)}
SHAPE = {"enum": sorted(SHAPES)}


def shape_name(shape: k8s.Shape) -> str | None:
    return next((name for name, s in SHAPES.items() if s is shape), None)


class Namespace(Base):
    """The namespace, with the deploy service account and its role, as the
    server's sustenaut-app program makes them."""
    type = "k8s.namespace"
    grade = "auto"
    budget = 2.0
    singleton = True

    def check(self, need: Need, ctx: Context) -> Result:
        ns = need.name
        server = ctx.env.get("SUSTENAUT_SERVER", "<server>")
        fix = f"ssh root@{server} sustenaut-app {ns}"
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


class Secret(Base):
    """A Secret in the namespace, with its keys, and values of a known shape having it."""
    type = "k8s.secret"
    grade = "auto"
    budget = 5.0
    schema = schema({"keys": KEYS, "shapes": {"type": "object", "additionalProperties": SHAPE,
                                              "x-phrase": "must give a shape by key"}})

    def check(self, need: Need, ctx: Context) -> Result:
        ns, name = ctx.namespace, need.name
        data = k8s.Cluster([], run=ctx.kubectl).secret(ns, name)
        source = need.implied_by or "deploy.needs"
        if data is None:
            return Result("missing", f"Secret {name} is not in {ns}",
                          fix=f"make it in {ns}, from what fills it ({source})")
        if isinstance(data, str):
            if "may not read Secrets" in data:
                return Result("unknown", f"kubectl may not read Secret {name} in {ns}", fix=data)
            return Result("unknown", f"Secret {name} in {ns} could not be read", fix=data)
        keys = tuple(need.params.get("keys", []))
        shapes = {k: SHAPES[s] for k, s in need.params.get("shapes", {}).items()}
        bad = k8s.check_keys(name, [k8s.Need(ns, name, f"need {need.id}", keys, shapes=shapes)], data)
        if bad:
            return Result("drift", bad[0], fix=f"put the right value in Secret {name} in {ns}, "
                                                f"from what fills it ({source})", details=bad)
        listed = f" has keys {', '.join(keys)}" if keys else " is there"
        return Result("ready", f"Secret {name} in {ns}{listed}")


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
    offline = True
    schema = schema({"path": {"type": "string", "pattern": r"^(?!/)(?!.*(^|/)\.\.(/|$))[A-Za-z0-9_./-]+\.ya?ml$",
                              "x-phrase": "must be a YAML file in the repository"}}, ("path",))

    def default_name(self, params: dict[str, Any]) -> str | None:
        return params.get("path")

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
