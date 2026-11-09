"""The need types a walk knows: pubkit's own, and any an installed package
offers under the entry-point group `sustenaut.needs`, each `module:object`
naming an object with what model.NeedType has: an instance, whose check and
fulfil the walk calls, not a class. A provider need import nothing of
pubkit's: any result with state, summary, fix and details serves.

pubsec offers the seven types that hold a secret's value (PUBSEC_TYPES),
which pubkit had built in until pubsec took them.

A type is checked as it loads: its name is dotted lower-case words, its
schema an object schema that takes no key it does not name, its budget more
than 0 and at most 10 seconds, its grade one of auto, propose, human and
none, and its fulfil_budget, when it gives one, more than 0 and at most 900
seconds. One that fails, or does not load, is left out with a warning, and
never stops `pubkit sync --check`. A type a second package offers again is
left out, naming both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points
from typing import Any

from .model import GRADES, NeedType
from .types.auth import SignInClient
from .types.github import VariableEntry
from .types.k8s import CronJob, Manifests, Namespace, SecretsDeclared
from .types.net import Health, Site, Tenant, Upstream
from .types.rollout import Gate, Rollout

GROUP = "sustenaut.needs"
TYPE = re.compile(r"[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)+")
LONGEST = 10.0
LONGEST_FULFIL = 900.0
BUILTIN: tuple[NeedType, ...] = (Namespace(), SecretsDeclared(), Manifests(), CronJob(), VariableEntry(), Gate(),
                                 Upstream(), Site(), Tenant(), Rollout(), Health(), SignInClient())
PUBKIT = "pubkit"
# The types that hold a secret's value, which pubsec offers: pubkit implies
# several (a secret's k8s.secret, the secrets block's github.*), so a plan
# with one needs pubsec installed, as deploy.providers lists it.
PUBSEC = "pubsec"
PUBSEC_TYPES = ("bitwarden.generated", "bitwarden.secret", "gcp.service-account-key", "github.deploy-settings",
                "github.secret", "github.variable", "k8s.secret")


@dataclass
class Registry:
    types: dict[str, NeedType] = field(default_factory=dict)
    origin: dict[str, str] = field(default_factory=dict)  # by type, the distribution it came from
    warnings: list[str] = field(default_factory=list)


def type_problems(kind: Any) -> list[str]:
    """What keeps `kind` from being a need type."""
    out = []
    name = getattr(kind, "type", None)
    if not isinstance(name, str) or not TYPE.fullmatch(name):
        out.append("its type must be dotted lower-case words, such as example.thing")
    schema = getattr(kind, "schema", None)
    if not isinstance(schema, dict) or schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        out.append("its schema must be an object's, with additionalProperties false")
    budget = getattr(kind, "budget", None)
    if isinstance(budget, bool) or not isinstance(budget, int | float) or not 0 < budget <= LONGEST:
        out.append(f"its budget must be more than 0 and at most {LONGEST:g} seconds")
    fulfil = getattr(kind, "fulfil_budget", 1.0)
    if isinstance(fulfil, bool) or not isinstance(fulfil, int | float) or not 0 < fulfil <= LONGEST_FULFIL:
        out.append(f"its fulfil_budget must be more than 0 and at most {LONGEST_FULFIL:g} seconds")
    if getattr(kind, "grade", None) not in GRADES:
        out.append(f"its grade must be one of {', '.join(GRADES)}")
    if not isinstance(getattr(kind, "offline", None), bool):
        out.append("its offline must be true or false")
    for method in ("default_name", "depends_on", "check", "fulfil"):
        if not callable(getattr(kind, method, None)):
            out.append(f"it has no {method}")
    return out


def load(points: list[EntryPoint] | None = None) -> Registry:
    """pubkit's types, and those the entry points offer."""
    out = Registry()
    for kind in BUILTIN:
        out.types[kind.type] = kind
        out.origin[kind.type] = PUBKIT
    found = sorted(entry_points(group=GROUP) if points is None else points, key=lambda p: (p.name, p.value))
    for point in found:
        dist = getattr(getattr(point, "dist", None), "name", None) or "an unknown package"
        label = f"{GROUP} entry point {point.name} ({point.value}, from {dist})"
        try:
            kind = point.load()
        except Exception as e:  # noqa: BLE001 - a bad provider is reported, not fatal
            out.warnings.append(f"{label} does not load ({type(e).__name__}), and is left out")
            continue
        if bad := type_problems(kind):
            out.warnings.append(f"{label} is left out: {'; '.join(bad)}")
            continue
        if kind.type in out.types:
            out.warnings.append(f"{label} offers {kind.type}, which {out.origin[kind.type]} offers too; "
                                f"{dist}'s is left out")
            continue
        out.types[kind.type] = kind
        out.origin[kind.type] = dist
    return out


def lacking(needs: Any, types: dict[str, NeedType]) -> list[str]:
    """For each type of `needs` that is pubsec's and not installed, what to do: one phrase."""
    missing = sorted({n.type for n in needs if n.type not in types and n.type in PUBSEC_TYPES})
    if not missing:
        return []
    return [f"deploy: {', '.join(missing)} {'is' if len(missing) == 1 else 'are'} {PUBSEC}'s, which is not installed: "
            f"list {PUBSEC} in deploy.providers, as `pubkit sync` does, and run ./pubkit, which runs it"]


def types() -> dict[str, NeedType]:
    return load().types
