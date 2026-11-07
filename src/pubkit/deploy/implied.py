"""The needs a deploy block implies, beside those it lists.

Derived in this order, each with `implied_by` saying where it came from:

    namespace           k8s.namespace, deploy.rollout, k8s.secrets-declared, and
                        service.health when there is a host
    host                dns.site
    gate                github.gate
    secrets             a github.secret or github.variable per entry, and
                        github.deploy-settings when one is the server's
    github_app          a github.secret or github.variable per slot
    variables           a github.variable per name, which must be set
    a need's secret     the k8s.secret it fills
    the manifests       a k8s.secret per Secret they read

A need the block lists wins over an implied one with the same id. Two
implied k8s.secret needs of one name are one, with the keys of both.
"""

from __future__ import annotations

import json
from typing import Any

from .. import k8s
from .model import Need, NeedType

# The names the server's deploy job reads (k8s-deploy.yml): one of them in the
# secrets block means the repository deploys to the server, so it needs all.
DEPLOY_SETTINGS = {"SUSTENAUT_SERVER": "variable", "SUSTENAUT_KNOWN_HOSTS": "variable",
                   "SUSTENAUT_DEPLOY_SSH_KEY": "secret", "TS_GH_OAUTH_CLIENT_ID": "secret",
                   "TS_GH_OAUTH_SECRET": "secret"}
SERVER_NAMES = ("SUSTENAUT_SERVER", "SUSTENAUT_KNOWN_HOSTS", "SUSTENAUT_DEPLOY_SSH_KEY")
COMMON = ("type", "name", "after")


def github_name(entry: dict[str, Any]) -> str:
    """A GitHub secret's or variable's need name: environment/NAME in an environment."""
    return f"{entry['environment']}/{entry['to']}" if entry.get("environment") else entry["to"]


def manifest_secrets(found: k8s.Found, namespace: str) -> dict[str, dict[str, Any]]:
    """Each Secret the manifests read in `namespace` and do not hold, with
    the keys and shapes its required references name. A Secret every
    reference to which is optional is left out."""
    from .types.k8s import shape_name  # noqa: PLC0415
    out: dict[str, dict[str, Any]] = {}
    for n in found.needs:
        if n.optional or n.namespace != namespace or (n.namespace, n.secret) in found.held:
            continue
        entry = out.setdefault(n.secret, {"keys": [], "shapes": {}})
        entry["keys"] = sorted(set(entry["keys"]) | set(n.keys))
        entry["shapes"] |= {key: name for key, shape in n.shapes.items() if (name := shape_name(shape))}
    return out


class Builder:
    """Needs by id, in the order they are added, merged as the module says."""

    def __init__(self) -> None:
        self.needs: dict[str, Need] = {}

    def add(self, kind: str, name: str, params: dict[str, Any], implied_by: str) -> None:
        need = Need(kind, name, params, implied_by)
        had = self.needs.get(need.id)
        if had is None:
            self.needs[need.id] = need
            return
        by = had.implied_by if had.implied_by is None or implied_by in had.implied_by.split(", ") \
            else f"{had.implied_by}, {implied_by}"
        merged = had.params
        if kind == "k8s.secret":
            keys = sorted(set(had.params.get("keys", [])) | set(params.get("keys", [])))
            shapes = {**params.get("shapes", {}), **had.params.get("shapes", {})}
            merged = {**had.params, "keys": keys} | ({"shapes": shapes} if shapes else {})
        self.needs[need.id] = Need(kind, had.name, merged, by, had.after)


def build(config: dict[str, Any], types: dict[str, NeedType], root: Any = None, namespace: str | None = None,
          found: k8s.Found | None = None) -> tuple[dict[str, Need], list[str]]:
    """Every need of a sound block, and what is wrong with them together: a
    need declared twice. `found` is what the rendered manifests read."""
    block = config["deploy"]
    ns = namespace or block["namespace"]
    problems = []
    out = Builder()
    written: dict[str, str] = {}
    for i, entry in enumerate(block.get("needs", [])):
        kind = types[entry["type"]]
        params = {k: v for k, v in entry.items() if k not in COMMON}
        name = entry.get("name") or kind.default_name(params) or ns
        need = Need(entry["type"], name, params, None, tuple(entry.get("after", [])))
        text = json.dumps(entry, sort_keys=True)
        if need.id in written:
            if written[need.id] != text:
                problems.append(f"deploy: needs[{i}]: {need.id} is declared twice")
            continue
        written[need.id] = text
        out.needs[need.id] = need
    explicit = list(out.needs.values())

    out.add("k8s.namespace", ns, {}, "namespace")
    out.add("deploy.rollout", ns, {}, "namespace")
    if block.get("host"):
        out.add("service.health", ns, {}, "namespace")
    out.add("k8s.secrets-declared", ns, {}, "namespace")
    if block.get("host"):
        out.add("dns.site", block["host"], {"host": block["host"]}, "host")
    if block.get("gate"):
        out.add("github.gate", ns, {"variable": block["gate"]}, "gate")

    from ..app_secrets import BLOCK as APP, OTHERS, config_problems, plan  # noqa: PLC0415
    if (APP in config or OTHERS in config) and not config_problems(config):
        for item in plan(config):
            params = {k: item[k] for k in ("from", "to", "environment") if k in item}
            out.add(f"github.{item['kind']}", github_name(item), params,
                    "github_app" if item["check"] else "secrets")
        if any(e["to"] in SERVER_NAMES for e in config.get(OTHERS, [])):
            out.add("github.deploy-settings", ns, {}, "secrets")
    variables = config.get("variables")
    if isinstance(variables, dict) and isinstance(variables.get("names"), list):
        for name in variables["names"]:
            if isinstance(name, str):
                out.add("github.variable", name, {"declared": True}, "variables")

    for need in explicit:
        secret = need.params.get("secret")
        if isinstance(secret, dict):
            shape = getattr(types[need.type], "secret_shape", lambda _p: None)(need.params)
            key = secret.get("key")
            params: dict[str, Any] = {"keys": [key] if key else []}
            if shape and key:
                params["shapes"] = {key: shape}
            out.add("k8s.secret", secret["name"], params, f"secret of {need.id}")
    if found is not None:
        for name, params in manifest_secrets(found, ns).items():
            out.add("k8s.secret", name, params, "manifests")
    return out.needs, problems


def declarers(block: dict[str, Any], types: dict[str, NeedType], namespace: str
              ) -> dict[str, list[tuple[str, list[str] | None]]]:
    """By Secret name, each need the block lists that fills it, with the keys
    it provides: None for any. A need's secret provides its key, or any
    without one; a k8s.secret need its keys, or any when it lists none.
    From the needs as written, so no merge widens what one provides."""
    out: dict[str, list[tuple[str, list[str] | None]]] = {}
    for entry in block.get("needs", []):
        kind = types.get(entry.get("type"))
        if kind is None:
            continue
        params = {k: v for k, v in entry.items() if k not in COMMON}
        need_id = f"{entry['type']}:{entry.get('name') or kind.default_name(params) or namespace}"
        if isinstance(entry.get("secret"), dict):
            key = entry["secret"].get("key")
            out.setdefault(entry["secret"]["name"], []).append((need_id, [key] if key else None))
        if entry["type"] == "k8s.secret":
            out.setdefault(need_id.split(":", 1)[1], []).append((need_id, list(entry.get("keys", [])) or None))
    return out
