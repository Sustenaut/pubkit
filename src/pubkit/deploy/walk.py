"""Check mode: run every need's check, in order, and keep what each found.

Each check runs in a thread the walk waits for up to the type's budget; one
that takes longer is left behind and its need is `unknown`, "took longer than
N s". A check that raises is `unknown` too, named by the exception's type
alone, since its message might hold a value. A need is still checked when
something it waits for is not ready, since checks are read-only and cheap:
its own result stands, unless it is `missing` while a need it waits for is
not ready, and then it is `blocked`, "waits for" that need.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping

from .model import Context, Journal, Need, NeedType, Result
from .order import edges, topological


def stamp(ctx: Context) -> str:
    return ctx.now().strftime("%Y-%m-%dT%H:%M:%SZ")


def timed(kind: NeedType, need: Need, ctx: Context) -> Result:
    """One check, within its budget."""
    box: list[Result | BaseException] = []

    def run() -> None:
        try:
            box.append(kind.check(need, ctx))
        except BaseException as e:  # noqa: BLE001 - any failure is the need's, not the walk's
            box.append(e)

    started = time.monotonic()
    worker = threading.Thread(target=run, name=f"check {need.id}", daemon=True)
    worker.start()
    worker.join(kind.budget)
    seconds = time.monotonic() - started
    if not box:
        result = Result("unknown", f"the check took longer than {kind.budget:g} s, and was left",
                        fix=f"run it again; if it is always slow, see what {need.type} waits on")
    elif isinstance(box[0], BaseException):
        result = Result("unknown", f"the check failed ({type(box[0]).__name__})",
                        fix=f"this is a fault in {need.type}'s check: report it")
    elif not isinstance(box[0], Result):
        result = Result("unknown", f"the check returned no result ({type(box[0]).__name__})",
                        fix=f"this is a fault in {need.type}'s check: report it")
    else:
        result = box[0]
        if seconds > kind.budget:
            result.details.append(f"took {seconds:.1f} s, over its budget of {kind.budget:g} s")
    result.seconds = seconds
    return result


def walk(needs: Mapping[str, Need], types: Mapping[str, NeedType], ctx: Context, service: dict[str, str],
         by: str = "", origin: str = "laptop") -> Journal:
    """Check every need, in order; under ctx.offline only the offline types."""
    order = topological(needs, types)
    waits = edges(needs, types)
    ctx.types = dict(types)
    ctx.needs = dict(needs)
    ctx.results = {}
    started = time.monotonic()
    journal = Journal(service=service, walk={"mode": "check", "offline": ctx.offline, "started_at": stamp(ctx),
                                             "seconds": 0.0, "by": by, "from": origin})
    for i in order:
        need, kind = needs[i], types[needs[i].type]
        if ctx.offline and not kind.offline:
            result = Result("unknown", "not checked offline")
        else:
            result = timed(kind, need, ctx)
        result.checked_at = stamp(ctx)
        late = [d for d in order if d in waits[i] and ctx.results.get(d, Result("ready", "")).state != "ready"]
        if result.state == "missing" and late:
            result = Result("blocked", f"waits for {late[0]}: {result.summary}", result.fix,
                            result.details, result.checked_at, result.seconds)
        ctx.results[i] = result
        journal.entries.append((need, result))
    journal.walk["seconds"] = round(time.monotonic() - started, 3)
    return journal


def counted(journal: Journal, types: Mapping[str, NeedType], offline: bool) -> list[tuple[Need, Result]]:
    """The entries the exit code counts: under --offline, only the offline types'."""
    return [(n, r) for n, r in journal.entries if not offline or types[n.type].offline]


def lines(journal: Journal) -> list[str]:
    """The human form: a line per need, then what to do about each one not ready, then the count."""
    out = []
    for need, result in journal.entries:
        out.append(f"{result.state:<7} {need.id}  {result.summary}")
        out += [f"          {d}" for d in result.details]
    waiting = [(n, r) for n, r in journal.entries if r.state != "ready" and r.fix]
    if waiting:
        out.append("")
        out.append("to do:")
        for need, result in waiting:
            out.append(f"  {need.id}")
            out.append(f"    {result.fix}")
    c = journal.counts()
    out.append(f"deploy: {len(journal.entries)} needs, {c['ready']} ready, {c['missing']} missing, "
               f"{c['drift']} drift, {c['blocked']} blocked, {c['unknown']} unknown")
    return out
