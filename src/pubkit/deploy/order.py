"""The order needs are walked in: each after the needs it waits for.

The built-in rules: everything waits for k8s.namespace but the needs outside
the cluster (github.*, bitwarden.*, dns.site, upstream) and
k8s.secrets-declared, which reads only the manifests; a k8s.secret waits for
the need whose secret fills it; github.gate waits for everything but
deploy.rollout and service.health; deploy.rollout waits for the gate, or,
without one, for everything; service.health waits for deploy.rollout. A
type may add its own, and a need its `after`.

The order is Kahn's algorithm with ties broken by id, so it is the same on
every run.
"""

from __future__ import annotations

import heapq
from collections.abc import Mapping

from .model import Need, NeedType

OUTSIDE = ("github.", "bitwarden.")
OUTSIDE_TYPES = {"dns.site", "upstream", "k8s.secrets-declared", "k8s.namespace"}
LAST = {"deploy.rollout", "service.health"}


def builtin(need: Need, needs: Mapping[str, Need]) -> set[str]:
    """The ids `need` waits for by the built-in rules."""
    out: set[str] = set()
    if not need.type.startswith(OUTSIDE) and need.type not in OUTSIDE_TYPES:
        out |= {i for i, n in needs.items() if n.type == "k8s.namespace"}
    if need.type == "k8s.secret":
        out |= {i for i, n in needs.items()
                if isinstance(n.params.get("secret"), dict) and n.params["secret"].get("name") == need.name}
    gates = {i for i, n in needs.items() if n.type == "github.gate"}
    if need.type == "github.gate":
        out |= {i for i, n in needs.items() if n.type not in LAST and n.type != "github.gate"}
    elif need.type == "deploy.rollout":
        out |= gates or {i for i, n in needs.items() if n.type not in LAST}
    elif need.type == "service.health":
        out |= {i for i, n in needs.items() if n.type == "deploy.rollout"}
    out.discard(need.id)
    return out


def edges(needs: Mapping[str, Need], types: Mapping[str, NeedType]) -> dict[str, set[str]]:
    """By id, the ids each need waits for: the built-in rules, its type's, and its after."""
    return {i: (builtin(n, needs) | set(types[n.type].depends_on(n, needs)) | set(n.after)) & set(needs) - {i}
            for i, n in needs.items()}


def unknown_after(needs: Mapping[str, Need]) -> list[str]:
    return [f"deploy: {i} is after {a}, which is no need of this deploy"
            for i, n in needs.items() for a in n.after if a not in needs]


def topological(needs: Mapping[str, Need], types: Mapping[str, NeedType]) -> list[str]:
    """Every id, each after those it waits for. ValueError naming a cycle."""
    waits = edges(needs, types)
    blocking: dict[str, set[str]] = {i: set() for i in needs}
    for i, deps in waits.items():
        for d in deps:
            blocking[d].add(i)
    left = {i: len(deps) for i, deps in waits.items()}
    ready = [i for i, n in left.items() if n == 0]
    heapq.heapify(ready)
    out = []
    while ready:
        i = heapq.heappop(ready)
        out.append(i)
        for j in blocking[i]:
            left[j] -= 1
            if left[j] == 0:
                heapq.heappush(ready, j)
    if len(out) < len(needs):
        raise ValueError(cycle({i: waits[i] for i in needs if i not in out}))
    return out


def cycle(waits: Mapping[str, set[str]]) -> str:
    """One cycle among needs that wait for each other: the first by id, around."""
    start = min(waits)
    path, seen = [start], {start}
    while True:
        nxt = min(d for d in waits[path[-1]] if d in waits)
        if nxt in seen:
            loop = path[path.index(nxt):] + [nxt]
            return f"deploy: these needs wait for each other: {' -> '.join(loop)}"
        path.append(nxt)
        seen.add(nxt)


def cycle_problems(needs: Mapping[str, Need], types: Mapping[str, NeedType]) -> list[str]:
    """An after naming no need, or needs waiting in a cycle."""
    if bad := unknown_after(needs):
        return bad
    try:
        topological(needs, types)
    except ValueError as e:
        return [str(e)]
    return []
