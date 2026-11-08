"""What a deploy needs, what a check finds, and what a check may touch.

A Need is one thing a service needs before it runs: a namespace, a Secret,
a GitHub variable, an upstream that answers. A NeedType knows how to check
one kind; its check returns a Result, in one of five states, and its fulfil
makes what is missing. A Context holds every way a check or a fulfil reaches
the world (kubectl, a command on the server, the GitHub API, gh's setters,
gcloud, Bitwarden's writer, HTTP, the environment, the clock, the rendered
manifests), so a test can give it fakes and no type reaches anything else.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

State = Literal["ready", "missing", "drift", "blocked", "unknown"]
Grade = Literal["auto", "propose", "human", "none"]
STATES: tuple[State, ...] = ("ready", "missing", "drift", "blocked", "unknown")
GRADES: tuple[Grade, ...] = ("auto", "propose", "human", "none")
SUMMARY = 120  # the longest summary, in characters
FULFIL_BUDGET = 60.0  # seconds, for a type that declares none


class Stop(Exception):
    """A step a check depends on could not be taken, such as rendering the
    overlay: its message says why, and never holds a value. `missing_tool`
    when a program is not installed, rather than failing."""

    def __init__(self, message: str, missing_tool: bool = False) -> None:
        super().__init__(message)
        self.missing_tool = missing_tool


@dataclass(frozen=True)
class Need:
    type: str
    name: str
    params: dict[str, Any]  # the need as written, validated; never mutated
    implied_by: str | None = None  # "namespace", "host", "secrets", "manifests", "secret of <id>"
    after: tuple[str, ...] = ()  # explicit ids

    @property
    def id(self) -> str:
        return f"{self.type}:{self.name}"


@dataclass
class Result:
    state: State
    summary: str  # one line, no value
    fix: str = ""  # what a person does when it is not ready: a command or a place, never a value
    details: list[str] = field(default_factory=list)
    checked_at: str = ""  # ISO 8601 UTC, set by the walk
    seconds: float = 0.0  # set by the walk
    fulfilled: bool = False  # set by the walk: this is the check after a fulfil
    # Neither is journaled. False when the type's fulfil cannot make what the
    # check found, such as an account only Terraform makes.
    fixable: bool = True
    step: str = ""  # which of its steps the type's fulfil takes, for a type with several

    def __post_init__(self) -> None:
        line = " ".join(self.summary.split())
        self.summary = line if len(line) <= SUMMARY else line[:SUMMARY - 1] + "…"


@dataclass
class Ran:
    """A command's exit code and output, as subprocess.run gives them."""
    returncode: int
    stdout: str = ""
    stderr: str = ""


class Kubectl(Protocol):
    """kubectl with `args`, `input` on its standard input: a manifest, never in argv."""

    def __call__(self, args: list[str], input: bytes | None = None) -> Ran: ...  # noqa: A002


Server = Callable[[list[str], "bytes | None"], Ran]  # a command on the server, and its standard input
Api = Callable[[str, str, Any], Any]
HttpGet = Callable[[str, float], tuple[int, bytes]]
Resolve = Callable[[str, str], "list[str] | None"]
Send = Callable[[str, str, str, str, "str | None"], "str | None"]  # app_secrets.send


class Writer(Protocol):
    """Bitwarden, with a write token the writer keeps: the token is never the type's."""

    def write(self, name: str, value: str, note: str) -> None:
        """Create or update the secret `name` in the walk's project, or raise; the error holds no value."""


def no_kubectl(_args: list[str], input: bytes | None = None) -> Ran:  # noqa: A002
    return Ran(127, "", "kubectl: not given to this check")


def no_server(_args: list[str], _input: bytes | None = None) -> Ran:
    return Ran(127, "", "no command runs on the server: not the admin channel")


def no_send(kind: str, name: str, _repo: str, _value: str, _environment: str | None = None) -> str | None:
    return f"gh {kind} set {name}: not given to this walk"


def no_gh(method: str, path: str, _body: Any = None) -> Any:
    from ..protect import ApiError  # noqa: PLC0415
    raise ApiError(f"gh api -X {method} {path}: not given to this check")


def no_http(url: str, _timeout: float) -> tuple[int, bytes]:
    raise ConnectionRefusedError(f"no HTTP in this check: {url}")


def no_resolve(_host: str, _rtype: str) -> list[str] | None:
    return None


def no_render() -> str:
    raise Stop("the overlay was not rendered", missing_tool=True)


@dataclass
class Context:
    """Everything a check touches."""
    root: Path
    config: dict[str, Any]
    block: dict[str, Any]
    namespace: str
    kubectl: Kubectl = no_kubectl
    server: Server = no_server
    # How kubectl reaches the cluster: admin (over the server's root SSH),
    # deploy (the server's read-only verb), plain (a kubeconfig) or plane.
    channel: str = "plain"
    gh: Api = no_gh
    send: Send = no_send
    bitwarden_writer: Writer | None = None  # None without a write token
    http_get: HttpGet = no_http
    env: Mapping[str, str] = field(default_factory=dict)
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    rendered: Callable[[], str] = no_render
    offline: bool = False
    slug: str = ""  # OWNER/NAME of the repository on GitHub, or "" when origin is not one
    zones: dict[str, Any] | None = None  # the hosting block's zones, from config or --zones
    resolve: Resolve = no_resolve  # a host's records of a type; None without dig
    unit: str = ""  # the unit walked, by name
    sleep: Callable[[float], None] = time.sleep  # between polls, in a fulfil that waits
    # What a fulfil made in this walk, by Bitwarden name: never journaled, printed or kept.
    values: dict[str, bytes] = field(default_factory=dict)
    dispatched: dict[str, datetime] = field(default_factory=dict)  # by workflow, when this walk ran it
    # Filled in by the walk: the types, every need in the plan, and the results so far.
    types: dict[str, Any] = field(default_factory=dict)
    needs: dict[str, Need] = field(default_factory=dict)
    results: dict[str, Result] = field(default_factory=dict)

    @classmethod
    def for_tests(cls, **overrides: Any) -> Context:
        """A context that reaches nothing: each tool refuses until a test
        gives a fake for it."""
        block = overrides.pop("block", {"namespace": "example"})
        given = {"root": Path("."), "config": {"kind": "service", "deploy": block}, "block": block,
                 "namespace": block.get("namespace", "example")}
        return cls(**(given | overrides))


@runtime_checkable
class NeedType(Protocol):
    type: str  # "k8s.secret"
    schema: dict[str, Any]  # JSON schema of the need's params, additionalProperties false
    grade: Grade  # what fulfil would be; "none" when only a person can act
    budget: float  # seconds a check may take; the walk enforces it
    offline: bool  # True when check() runs under --offline (touching no network then)
    # And optionally: fulfil_budget (seconds a fulfil may take, at most 900,
    # default 60); grade_for(need), a need's own grade; requires(need), what
    # its fulfil needs of the context ("server", "kubectl-write",
    # "write-token"); action(need, ctx), the plan's line; value(need, ctx),
    # the bytes a need with a `secret` puts in it; secret_keys(params), the
    # keys it fills when its secret names none.

    def default_name(self, params: dict[str, Any]) -> str | None: ...

    def depends_on(self, need: Need, needs: Mapping[str, Need]) -> list[str]: ...

    def check(self, need: Need, ctx: Context) -> Result: ...

    def fulfil(self, need: Need, ctx: Context) -> Result:
        """Make what check found missing or drifted, and say what was done in
        `summary`, state ready. It raises nothing: a failure is a Result with
        state missing or drift and the cause in fix. A propose or human type's
        changes nothing, and gives the change to make as fix."""


SCHEMA = "deploy-journal/2"


@dataclass
class Journal:
    """One walk: who ran it, where, and each need's result, in walk order."""
    service: dict[str, str]
    walk: dict[str, Any]
    entries: list[tuple[Need, Result]] = field(default_factory=list)
    grades: dict[str, str] = field(default_factory=dict)  # by need id

    def counts(self) -> dict[str, int]:
        return {s: sum(r.state == s for _, r in self.entries) for s in STATES}

    def document(self) -> dict[str, Any]:
        """The deploy-journal/2 document: no need's params, so no value."""
        return {"schema": SCHEMA, "service": self.service, "walk": self.walk,
                "needs": [{"id": n.id, "type": n.type, "name": n.name, "implied_by": n.implied_by,
                           "after": list(n.after), "grade": self.grades.get(n.id, "none"), "state": r.state,
                           "fulfilled": r.fulfilled, "summary": r.summary, "fix": r.fix, "details": r.details,
                           "seconds": round(r.seconds, 3), "checked_at": r.checked_at}
                          for n, r in self.entries],
                "summary": self.counts()}

    def json(self) -> str:
        return json.dumps(self.document(), indent=2)
