"""Check mode: run every need's check, in order, and keep what each found.

Each check runs in a thread the walk waits for up to the type's budget; one
that takes longer is left behind and its need is `unknown`, "took longer than
N s". A check that raises is `unknown` too, named by the exception's type
alone, since its message might hold a value. A need is still checked when
something it waits for is not ready, since checks are read-only and cheap:
its own result stands, unless it is `missing` while a need it waits for is
not ready, and then it is `blocked`, "waits for" that need.

Fulfil mode (`walk --fulfil`) runs every check first, then `planned` lists
the needs it will act on: each missing, drifted or blocked one whose grade is
auto, whose finding its fulfil can make, whose context has what the fulfil
needs, and whose dependencies are ready or are acted on before it. Once the
plan is confirmed, `fulfil` visits them in order: a need whose dependency was
fulfilled is checked again first, then, while every dependency is ready, it
is fulfilled within its fulfil_budget and checked again, and that check is
its result, `fulfilled`. A fulfil that fails leaves the need as it was, with
the failure as a detail, and the walk goes on with what does not wait for it.
A propose or human need is never acted on: a propose type's fulfil, which
changes nothing, gives its proposal as the fix.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping

from .model import FULFIL_BUDGET, STATES, Context, Journal, Need, NeedType, Result, no_server
from .order import edges, topological

ACTING = ("missing", "drift", "blocked")
# What a fulfil may need of its context, and the fix when the context lacks it.
LACKS = {"server": "needs the admin channel (--server HOST) or the plane",
         "kubectl-write": "needs the admin channel or the plane",
         "write-token": "needs a Bitwarden write token: run the walk at a terminal"}


def stamp(ctx: Context) -> str:
    return ctx.now().strftime("%Y-%m-%dT%H:%M:%SZ")


def timed(kind: NeedType, need: Need, ctx: Context, step: str = "check") -> Result:
    """One check, or one fulfil, within its budget."""
    box: list[Result | BaseException] = []
    budget = kind.budget if step == "check" else fulfil_budget(kind)
    method = kind.check if step == "check" else kind.fulfil

    def run() -> None:
        try:
            box.append(method(need, ctx))
        except BaseException as e:  # noqa: BLE001 - any failure is the need's, not the walk's
            box.append(e)

    started = time.monotonic()
    worker = threading.Thread(target=run, name=f"{step} {need.id}", daemon=True)
    worker.start()
    worker.join(budget)
    seconds = time.monotonic() - started
    if not box:
        result = Result("unknown", f"the {step} took longer than {budget:g} s, and was left",
                        fix=f"run it again; if it is always slow, see what {need.type} waits on")
    elif isinstance(box[0], BaseException):
        result = Result("unknown", f"the {step} failed ({type(box[0]).__name__})",
                        fix=f"this is a fault in {need.type}'s {step}: report it")
    elif (result := foreign(box[0])) is None:
        result = Result("unknown", f"the {step} returned no result ({type(box[0]).__name__})",
                        fix=f"this is a fault in {need.type}'s {step}: report it")
    else:
        if seconds > budget:
            result.details.append(f"took {seconds:.1f} s, over its budget of {budget:g} s")
    result.seconds = seconds
    return result


def foreign(given: object) -> Result | None:
    """A Result, from pubkit's or from any object with its state and summary,
    as a provider that imports nothing of pubkit's returns."""
    if isinstance(given, Result):
        return given
    state, summary = getattr(given, "state", None), getattr(given, "summary", None)
    if state not in STATES or not isinstance(summary, str):
        return None
    fix, details = getattr(given, "fix", ""), getattr(given, "details", [])
    return Result(state, summary, fix if isinstance(fix, str) else "",
                  [str(d) for d in details] if isinstance(details, list | tuple) else [],
                  fixable=getattr(given, "fixable", True) is not False, step=str(getattr(given, "step", "") or ""))


def fulfil_budget(kind: NeedType) -> float:
    return float(getattr(kind, "fulfil_budget", FULFIL_BUDGET))


def grade_of(kind: NeedType, need: Need) -> str:
    """The need's own grade: its type's, unless the type grades by params."""
    by = getattr(kind, "grade_for", None)
    return by(need) if callable(by) else kind.grade


def lacking(kind: NeedType, need: Need, ctx: Context, token_coming: bool = False) -> str | None:
    """What the need's fulfil needs that the context lacks, as the fix."""
    required = getattr(kind, "requires", lambda _need: ())(need)
    for what in required:
        if what == "server" and ctx.server is no_server:
            return LACKS[what]
        if what == "kubectl-write" and ctx.channel == "deploy":
            return LACKS[what]
        if what == "write-token" and ctx.bitwarden_writer is None and not token_coming:
            return LACKS[what]
    return None


def action(kind: NeedType, need: Need, ctx: Context) -> str:
    said = getattr(kind, "action", None)
    return f"{need.id}: {said(need, ctx) if callable(said) else 'fulfil it'}"


def checked(kind: NeedType, need: Need, ctx: Context, waits: set[str], order: list[str]) -> Result:
    """A check, stamped, and blocked when missing while a need it waits for is not ready."""
    if ctx.offline and not kind.offline:
        result = Result("unknown", "not checked offline")
    else:
        result = timed(kind, need, ctx)
    result.checked_at = stamp(ctx)
    late = [d for d in order if d in waits and ctx.results.get(d, Result("ready", "")).state != "ready"]
    if result.state == "missing" and late:
        result = Result("blocked", f"waits for {late[0]}: {result.summary}", result.fix, result.details,
                        result.checked_at, result.seconds, fixable=result.fixable, step=result.step)
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
        journal.grades[i] = grade_of(kind, need)
        result = checked(kind, need, ctx, waits[i], order)
        ctx.results[i] = result
        journal.entries.append((need, result))
    journal.walk["seconds"] = round(time.monotonic() - started, 3)
    return journal


def planned(journal: Journal, types: Mapping[str, NeedType], ctx: Context,
            token_coming: bool = False) -> list[tuple[str, str]]:
    """The needs fulfil acts on, in walk order, each with its plan's line.
    `token_coming`: a write token will be asked for, so the needs that want
    one are planned."""
    waits = edges(ctx.needs, types)
    expected = {n.id for n, r in journal.entries if r.state == "ready"}
    out = []
    for need, result in journal.entries:
        kind = types[need.type]
        if (result.state not in ACTING or not result.fixable or grade_of(kind, need) != "auto"
                or lacking(kind, need, ctx, token_coming) or not waits[need.id] <= expected):
            continue
        expected.add(need.id)
        out.append((need.id, action(kind, need, ctx)))
    return out


def wants_token(plan: list[tuple[str, str]], types: Mapping[str, NeedType], ctx: Context) -> bool:
    return any("write-token" in getattr(types[ctx.needs[i].type], "requires", lambda _n: ())(ctx.needs[i])
               for i, _ in plan)


def unable(journal: Journal, types: Mapping[str, NeedType], ctx: Context, declined: bool = False) -> None:
    """Each auto need not ready whose fulfil needs what the context lacks
    becomes unknown, saying what it needs, and what its check found. When
    the plan was declined, no token was asked for: a need wanting one keeps
    what its check found."""
    for k, (need, result) in enumerate(journal.entries):
        kind = types[need.type]
        if result.state not in ACTING or not result.fixable or grade_of(kind, need) != "auto":
            continue
        why = lacking(kind, need, ctx)
        if why == LACKS["write-token"] and declined:
            continue
        if why is not None:
            new = Result("unknown", f"not fulfilled: {why.split(':')[0]}", why,
                         [f"found {result.state}: {result.summary}", *result.details], result.checked_at,
                         result.seconds)
            journal.entries[k] = (need, new)
            ctx.results[need.id] = new


def fulfil(journal: Journal, types: Mapping[str, NeedType], ctx: Context, approved: set[str]) -> Journal:
    """Act on the approved needs, in order, as the module says; propose needs give their proposal."""
    waits = edges(ctx.needs, types)
    order = [n.id for n, _ in journal.entries]
    at = {n.id: k for k, (n, _) in enumerate(journal.entries)}
    started = time.monotonic()
    journal.walk["mode"] = "fulfil"
    done: set[str] = set()

    def keep(i: str, result: Result) -> None:
        ctx.results[i] = result
        journal.entries[at[i]] = (ctx.needs[i], result)

    for i in order:
        need, kind = ctx.needs[i], types[ctx.needs[i].type]
        result = ctx.results[i]
        if result.state == "ready":
            continue
        if waits[i] & done:
            result = checked(kind, need, ctx, waits[i], order)
            keep(i, result)
        grade = grade_of(kind, need)
        if result.state not in ACTING:
            continue
        if grade == "propose":
            proposal = timed(kind, need, ctx, "fulfil")
            if proposal.fix and proposal.state != "unknown":
                result.fix = proposal.fix
            continue
        if grade != "auto" or i not in approved or not result.fixable:
            continue
        late = [d for d in order if d in waits[i] and ctx.results[d].state != "ready"]
        if late:
            result.details.append(f"not fulfilled: waits for {late[0]}, which is {ctx.results[late[0]].state}")
            continue
        made = timed(kind, need, ctx, "fulfil")
        if made.state != "ready":
            result.details.append(f"fulfil failed: {made.summary}" + (f"; {made.fix}" if made.fix else ""))
            result.details += made.details
            continue
        again = checked(kind, need, ctx, waits[i], order)
        again.fulfilled = True
        again.details[:0] = [f"fulfilled: {made.summary}", *made.details]
        again.seconds += made.seconds
        keep(i, again)
        done.add(i)
    journal.walk["seconds"] = round(journal.walk["seconds"] + time.monotonic() - started, 3)
    return journal


def counted(journal: Journal, types: Mapping[str, NeedType], offline: bool) -> list[tuple[Need, Result]]:
    """The entries the exit code counts: under --offline, only the offline types'."""
    return [(n, r) for n, r in journal.entries if not offline or types[n.type].offline]


def lines(journal: Journal) -> list[str]:
    """The human form: a line per need, then what to do about each one not ready, then the count."""
    out = []
    for need, result in journal.entries:
        made = " (fulfilled)" if result.fulfilled else ""
        out.append(f"{result.state:<7} {need.id}  {result.summary}{made}")
        out += [f"          {d}" for d in result.details]
    waiting = [(n, r) for n, r in journal.entries if r.state != "ready" and r.fix]
    if waiting:
        out.append("")
        out.append("to do:")
        for need, result in waiting:
            out.append(f"  {need.id}")
            out += [f"    {line}" for line in result.fix.splitlines()]
    c = journal.counts()
    out.append(f"deploy: {len(journal.entries)} needs, {c['ready']} ready, {c['missing']} missing, "
               f"{c['drift']} drift, {c['blocked']} blocked, {c['unknown']} unknown")
    return out
