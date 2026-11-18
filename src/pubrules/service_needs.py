"""A service's needs from GitHub Actions: a workflow run asks the convergence engine to
check a unit's needs (read-only), or to fulfil them once a person approves the plan on the
engine's page, and the run waits for the end.

    pubrules service-needs --unit UNIT [--kind check|fulfil|gate] [--plan TEXT] [--minutes 300] [--engine URL]
                     [--stage both|ask|wait] [--request ID]

The run's identity is its GitHub Actions OIDC token, for the engine's own address
as audience: the job needs `id-token: write`, and nothing else, no secret. The
token is asked for afresh for every call, since a wait outlives one; it is masked
in the log and never printed. The engine takes

    POST ENGINE/requests        {"kind": "fulfil", "unit": UNIT}
    GET  ENGINE/requests/ID     the request's state, for the run that asked

and the run polls every 15 seconds, waiting longer after an answer of 5xx or no
answer at all, until the request ends or the time is up:

- done (or a gate approved): success, each need's end state in the job summary;
- declined, expired or failed (a plan that changed since its approval among them):
  failure, with the engine's reason;
- still awaiting, or still running, when the time is up: success, saying so: the
  engine runs it when a person approves it, whether or not the run still waits.

Each state is printed once. The workflow runs it as two steps: `--stage ask` asks and
follows the request until its plan is walked, and when it awaits a person writes a box
to the job summary (and a notice) saying where to approve it, then ends, so the box
shows on the run's page while `--stage wait` follows the request to its end, and no email (the engine names none to a caller) or
other value is ever printed: what the engine says is printed with any email-shaped
text taken out.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

ENGINE = "https://convergence.sustenaut.eu"
EVERY = 15.0  # seconds between polls
LONGEST_WAIT = 120.0  # the most a poll waits after a failed call
KINDS = ("check", "fulfil", "gate")
ENDED = {"done": 0, "declined": 1, "expired": 1, "failed": 1}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
LINK = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.I)
SAID = {"planning": "the engine is walking the unit read-only for its plan",
        "awaiting": "awaiting a person's approval",
        "approved": "approved",
        "running": "the engine is fulfilling the unit",
        "done": "done",
        "declined": "declined",
        "expired": "expired: nobody decided in time",
        "failed": "failed"}


CALLER = ".github/workflows/service-needs.yml"
OLD_CALLER = ".github/workflows/go-live.yml"  # pubrules 0.43.2 wrote it; sync takes it away
OLD_BLOCK = "go-live"


def renamed_block(text: str) -> str:
    """pubrules.json's text with its top-level go-live key named service-needs: nothing else
    of it changes, byte for byte."""
    data = json.loads(text)
    if OLD_BLOCK not in data:
        return text
    if BLOCK in data:
        raise SystemExit(f"pubrules: pubrules.json has both a {OLD_BLOCK} and a {BLOCK} block: keep {BLOCK}")
    out = re.sub(r'"go-live"(\s*:)', r'"service-needs"\1', text, count=1)
    if json.loads(out) != {(BLOCK if k == OLD_BLOCK else k): v for k, v in data.items()}:
        return json.dumps({(BLOCK if k == OLD_BLOCK else k): v for k, v in data.items()}, indent=2) + "\n"
    return out
HEADER = ("# Written by `pubrules sync` from pubrules.json's deploy block. Do not edit:\n"
          "# change pubrules.json and run `pubrules sync`.\n")
BLOCK = "service-needs"
BLOCK_KEYS = ("engine", "timeout-minutes", "claude-dispatch")


def block_problems(block: Any) -> list[str]:
    """What is wrong with pubrules.json's service-needs block, each a phrase."""
    if not isinstance(block, dict):
        return ['it must be an object, such as {"claude-dispatch": false}']
    out = [f"{key} is not a setting; it takes {', '.join(BLOCK_KEYS)}" for key in sorted(set(block) - set(BLOCK_KEYS))]
    if "engine" in block and not (isinstance(block["engine"], str)
                                  and re.fullmatch(r"https://[A-Za-z0-9.-]+(:[0-9]+)?", block["engine"])):
        out.append("engine must be the convergence engine's https:// address, with no path")
    if "timeout-minutes" in block and not (isinstance(block["timeout-minutes"], int)
                                           and not isinstance(block["timeout-minutes"], bool)
                                           and 1 <= block["timeout-minutes"] <= 340):
        out.append("timeout-minutes must be a whole number of minutes, 1 to 340")
    if "claude-dispatch" in block and not isinstance(block["claude-dispatch"], bool):
        out.append("claude-dispatch must be true or false")
    return out


def is_caller(text: str) -> bool:
    return text.startswith(HEADER)


def units(deploy: dict[str, Any]) -> list[str]:
    """The deploy block's units, as pubdeploy names them: the top level's (its
    namespace) first, then `units`, in name order."""
    return [deploy["namespace"], *sorted(deploy.get("units") or {})]


def caller(config: dict[str, Any], owner: str, pin: str) -> str | None:
    """The service-needs workflow sync writes in a repository with a deploy block, or None
    without one: run by hand, it calls the reusable service-needs.yml at this pubrules'
    release for the unit chosen. `pin` is what follows @, the release's commit and
    version."""
    deploy = config.get("deploy")
    if not isinstance(deploy, dict) or not isinstance(deploy.get("namespace"), str):
        return None
    given = config.get(BLOCK) or {}
    ci = config.get("ci") or {}
    runs_on = ci.get("runs-on")
    label = runs_on if isinstance(runs_on, str) else (runs_on or {}).get("default")
    inputs = {"unit": "${{ inputs.unit }}", "kind": "${{ inputs.action }}"}
    if "engine" in given:
        inputs["engine"] = given["engine"]
    if "timeout-minutes" in given:
        inputs["timeout-minutes"] = given["timeout-minutes"]
    if label:
        inputs["runs-on"] = label
    options = ", ".join(json.dumps(u) for u in units(deploy))
    dispatch = ("# service-needs.claude-dispatch is true: Claude may start this workflow too.\n"
                if given.get("claude-dispatch") else "")
    return (HEADER + dispatch
            + "name: service-needs\n\n"
            "on:\n  workflow_dispatch:\n    inputs:\n      action:\n"
            "        description: \"check: each need's state, read-only; fulfil: the engine makes what is missing, "
            "once a person approves its plan\"\n"
            "        type: choice\n        required: true\n        default: check\n        options: [\"check\", \"fulfil\"]\n"
            "      unit:\n"
            "        description: The deploy unit whose needs the convergence engine checks or fulfils\n"
            f"        type: choice\n        required: true\n        options: [{options}]\n\n"
            "permissions:\n  contents: read\n\n"
            "jobs:\n  service-needs:\n"
            f"    uses: {owner}/pubrules/.github/workflows/service-needs.yml@{pin}\n"
            f"    with: {json.dumps(inputs)}\n"
            "    permissions:\n      contents: read\n      id-token: write\n")


class Unanswered(Exception):
    """The engine did not answer, or answered 5xx: worth asking again."""


class Refused(Exception):
    """The engine refused the run (4xx): asking again would not help."""


@dataclass
class Http:
    """The calls the poll makes, so a test can give fakes for each."""
    send: Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]
    token: Callable[[str], str]
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    out: Callable[[str], None] = print
    summary: Callable[[str], None] = lambda _text: None
    output: Callable[[str, str], None] = lambda _name, _value: None  # a step output (GITHUB_OUTPUT)


@dataclass
class Poll:
    """What the run saw: each state once, and the last answer."""
    states: list[str] = field(default_factory=list)
    answer: dict[str, Any] = field(default_factory=dict)


def clean(text: Any) -> str:
    """The engine's words, as a log line may hold them: no email, one line."""
    return EMAIL.sub("[someone]", " ".join(str(text).split()))[:300]


def said(text: Any) -> str:
    """What the engine found, as a public log may print it: no email and no link either
    (the engine takes them out too; this is the second guard)."""
    return LINK.sub("[link]", clean(text))


def call(http: Http, engine: str, method: str, path: str, body: dict | None = None) -> dict[str, Any]:
    """One call to the engine, as the run: its JSON answer, or Unanswered or Refused."""
    headers = {"Authorization": f"Bearer {http.token(engine)}", "Accept": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    try:
        status, raw = http.send(method, engine + path, headers, data)
    except OSError as e:
        raise Unanswered(f"no answer ({type(e).__name__})") from None
    if status >= 500:
        raise Unanswered(f"the engine answered {status}")
    try:
        answer = json.loads(raw or b"{}")
    except ValueError:
        answer = {}
    if status >= 400:
        reason = answer.get("refused") if isinstance(answer, dict) else None
        raise Refused(f"the engine refused the request ({status}{', ' + clean(reason) if reason else ''})")
    if not isinstance(answer, dict) or not isinstance(answer.get("state"), str):
        raise Unanswered("the engine's answer is not a request")
    return answer


def retried(http: Http, deadline: float, what: Callable[[], dict[str, Any]]) -> dict[str, Any] | None:
    """`what`, asked again after Unanswered with a wait that doubles from EVERY up to
    LONGEST_WAIT, until it answers or the deadline passes (None then)."""
    wait = EVERY
    while True:
        try:
            return what()
        except Unanswered as e:
            if http.clock() + wait > deadline:
                http.out(f"service-needs: {e}; the time is up")
                return None
            http.out(f"service-needs: {e}; asking again in {wait:g} s")
            http.sleep(wait)
            wait = min(wait * 2, LONGEST_WAIT)


ICONS = {"ready": "\u2705", "missing": "\u274c", "drift": "\u274c", "blocked": "\u274c", "unknown": "\u26a0\ufe0f"}


def escaped(text: str, title: bool = False) -> str:
    """Text as a workflow command takes it: %, newlines and, in a title, : and , escaped."""
    out = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return out.replace(":", "%3A").replace(",", "%2C") if title else out


def each_need(http: Http, answer: dict[str, Any], kind: str) -> str:
    """Every need on a line of its own: a log group per need (its icon, name and state, then
    what was found and, for a fulfil, what it changes), an error annotation for a need missing,
    drifted or blocked and a warning for one unknown, and the summary's table, returned."""
    changes = {str(n.get("need")): said(n.get("change", "")) for n in answer.get("plan") or [] if isinstance(n, dict)}
    rows = []
    for n in (answer.get("result") or {}).get("needs") or []:
        if not isinstance(n, dict):
            continue
        need, state, detail = clean(n.get("need", "")), clean(n.get("state", "")), said(n.get("detail", ""))
        icon = ICONS.get(state, "\u2754")
        http.out(f"::group::{icon} {need} {state}")
        if detail:
            http.out(detail)
        if kind == "fulfil" and changes.get(need):
            http.out(f"the fulfil: {changes[need]}")
        http.out("::endgroup::")
        if state in ("missing", "drift", "blocked"):
            http.out(f"::error title={escaped(need, True)}::{escaped(detail or state)}")
        elif state == "unknown":
            http.out(f"::warning title={escaped(need, True)}::{escaped(detail or state)}")
        cells = [icon, f"`{need}`", clean(n.get("type", "")), clean(n.get("grade", "")), state,
                 detail.replace("|", "\\|")]
        rows.append("| " + " | ".join(cells) + " |\n")
    if not rows:
        return ""
    return "\n|  | Need | Type | Grade | State | Found |\n| --- | --- | --- | --- | --- | --- |\n" + "".join(rows)


def service_needs(http: Http, engine: str, unit: str, kind: str, minutes: float, plan: str = "") -> int:
    """Ask, then poll until the request ends or the time is up: the exit code. The
    workflow runs the two halves as steps of their own (ask, then wait), so the box
    `ask` writes when a person must approve shows while `wait` still runs."""
    deadline, poll = http.clock() + minutes * 60, Poll()
    asked = ask(http, engine, unit, kind, deadline, plan, poll)
    if isinstance(asked, int):
        return asked
    return follow(http, engine, unit, kind, deadline, asked, poll)


def ask(http: Http, engine: str, unit: str, kind: str, deadline: float,
        plan: str = "", poll: Poll | None = None) -> dict[str, Any] | int:
    """Ask the engine, and follow the request until its plan is walked: its answer then,
    or the exit code when it could not be asked. When it awaits a person, the job summary
    gets the box that says where to approve it, and the log a notice."""
    engine = engine.rstrip("/")
    try:
        body = {"kind": kind, "unit": unit, **({"plan": plan or f"the needs of {unit}"} if kind == "gate" else {})}
        first = retried(http, deadline, lambda: call(http, engine, "POST", "/requests", body))
    except Refused as e:
        http.out(f"service-needs: {e}")
        http.summary(f"### The needs of {clean(unit)}\n\nNot asked: {e}.\n")
        return 1
    if first is None:
        http.summary(f"### The needs of {clean(unit)}\n\nThe engine did not answer; if it took the request it is on /approvals.\n")
        return 1
    http.out(f"service-needs: asked the engine to {kind} {unit}: request {clean(first.get('id', ''))}")
    http.output("request", clean(first.get("id", "")))
    answer = watch(http, engine, unit, kind, deadline, first, poll or Poll(), lambda state: state != "planning")
    if isinstance(answer, int):  # the engine refused the run while it planned: said by watch
        return answer
    if answer["state"] == "awaiting":
        approval_box(http, unit, answer)
    # What is left of the run's time, for the wait step: one limit for both steps.
    http.output("minutes", f"{max(0.0, (deadline - http.clock()) / 60):.1f}")
    return answer


def approval_box(http: Http, unit: str, answer: dict[str, Any]) -> None:
    """The box on the run's page: a person must approve, where, and what the plan holds."""
    page = clean(answer.get("url", ""))
    http.out(f"::notice title=Approval needed::{page}")
    lines = [f"> `{clean(n.get('need', ''))}` ({clean(n.get('state', ''))}): {said(n.get('change', ''))}"
             for n in answer.get("plan") or [] if isinstance(n, dict)]
    expires = re.fullmatch(r"(\d{4}-\d\d-\d\d)T(\d\d:\d\d):\d\dZ", str(answer.get("expires_at") or ""))
    http.summary("> [!IMPORTANT]\n"
                 f"> **Approval needed.** Fulfilling the needs of {clean(unit)} waits for a person's approval on the "
                 f"engine's page: [{page}]({page})\n"
                 + (f"> It expires {expires[1]} {expires[2]} UTC, undecided.\n" if expires else "")
                 + (">\n> The plan:\n>\n" + "\n>\n".join(lines) + "\n" if lines else ""))


def wait(http: Http, engine: str, unit: str, kind: str, minutes: float, request: str) -> int:
    """Follow an asked request to its end, or until the time is up: the exit code."""
    engine = engine.rstrip("/")
    deadline = http.clock() + minutes * 60
    try:
        first = retried(http, deadline, lambda: call(http, engine, "GET", f"/requests/{urllib.parse.quote(request)}"))
    except Refused as e:
        http.out(f"service-needs: {e}")
        http.summary(f"### The needs of {clean(unit)}\n\nThe engine stopped answering this run: {e}.\n")
        return 1
    if first is None:
        return out_of_time(http, unit, "awaiting", f"{engine}/approvals/{clean(request)}")
    return follow(http, engine, unit, kind, deadline, first)


def follow(http: Http, engine: str, unit: str, kind: str, deadline: float, answer: dict[str, Any],
           poll: Poll | None = None) -> int:
    """Poll from `answer` until the request ends (a gate: until approved), or the time is up."""
    found = watch(http, engine.rstrip("/"), unit, kind, deadline, answer, poll or Poll(), lambda _state: False)
    if isinstance(found, int):
        return found
    state, page = found["state"], clean(found.get("url", ""))
    if state in ENDED or (kind == "gate" and state == "approved"):
        return ended(http, unit, found, kind)
    return out_of_time(http, unit, state, page)


def watch(http: Http, engine: str, unit: str, kind: str, deadline: float, answer: dict[str, Any], poll: Poll,
          enough: Callable[[str], bool]) -> dict[str, Any] | int:
    """Poll every EVERY seconds, printing each state once, until the request ends, `enough`
    says so, or the time is up: the last answer, or the exit code when the engine refused."""
    request = clean(answer.get("id", ""))
    while True:
        poll.answer = answer
        state = answer["state"]
        if state not in poll.states:
            poll.states.append(state)
            http.out(f"service-needs: {SAID.get(state, clean(state))}")
        if state in ENDED or (kind == "gate" and state == "approved") or enough(state):
            return answer
        if http.clock() + EVERY > deadline:
            return answer
        http.sleep(EVERY)
        try:
            later = retried(http, deadline, lambda: call(http, engine, "GET", f"/requests/{urllib.parse.quote(request)}"))
        except Refused as e:
            http.out(f"service-needs: {e}")
            http.summary(f"### The needs of {clean(unit)}\n\nThe engine stopped answering this run: {e}.\n")
            return 1
        if later is None:
            return answer
        answer = later


def ended(http: Http, unit: str, answer: dict[str, Any], kind: str = "fulfil") -> int:
    state = answer["state"]
    result = answer.get("result") or {}
    error, note = said(result.get("error") or ""), said(result.get("note") or "")
    code = ENDED.get(state, 0)
    line = f"{SAID.get(state, clean(state))}{': ' + error if error and code else ': ' + note if note else ''}"
    if kind == "check" and state == "done":
        # A check fails the run when a need is not ready that a walk can see: missing, drift, blocked.
        bad = [f"{clean(n.get('need', ''))} {clean(n.get('state', ''))}" for n in result.get("needs") or []
               if isinstance(n, dict) and n.get("state") in ("missing", "drift", "blocked")]
        code, line = (1, f"not ready: {', '.join(bad)}") if bad else (0, "every need the walk can judge is ready")
    http.out(f"service-needs: {clean(unit)} {line}")
    http.summary(f"### The needs of {clean(unit)}\n\n{line[0].upper()}{line[1:]}.\n{each_need(http, answer, kind)}")
    return code


def out_of_time(http: Http, unit: str, state: str, page: str) -> int:
    said = ("still awaiting approval: the engine runs it when approved" if state in ("planning", "awaiting")
            else "the engine is still fulfilling the unit")
    http.out(f"service-needs: the time is up, {said}")
    http.summary(f"### The needs of {clean(unit)}\n\nThe time is up, {said}: [{page}]({page})\n")
    return 0


def send(method: str, url: str, headers: dict[str, str], data: bytes | None,
         timeout: float = 30.0) -> tuple[int, bytes]:  # pragma: no cover - the network
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return answer.status, answer.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def actions_token(env: dict[str, str], get: Callable[..., tuple[int, bytes]] = send,
                  out: Callable[[str], None] = print) -> Callable[[str], str]:
    """The run's OIDC token for an audience, asked of GitHub Actions each time, and
    masked in the log the first time it is seen."""
    url, bearer = env.get("ACTIONS_ID_TOKEN_REQUEST_URL"), env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not bearer:
        raise SystemExit("service-needs: no OIDC token: the job needs `permissions: id-token: write`")

    def token(audience: str) -> str:
        joined = "&" if "?" in url else "?"
        try:
            status, raw = get("GET", f"{url}{joined}audience={urllib.parse.quote(audience, safe='')}",
                              {"Authorization": f"Bearer {bearer}", "Accept": "application/json"}, None)
            value = json.loads(raw).get("value") if status == 200 else None
        except (OSError, ValueError, AttributeError):
            value = None
        if not isinstance(value, str) or not value:
            raise Unanswered("GitHub gave no OIDC token")
        out(f"::add-mask::{value}")
        return value
    return token


def main(argv: list[str], env: dict[str, str] | None = None) -> int:  # pragma: no cover - the workflow's
    parser = argparse.ArgumentParser(prog="pubrules service-needs", description=__doc__.split("\n")[0])
    parser.add_argument("--unit", required=True, help="the deploy unit (a fulfil), or what the gate is for")
    parser.add_argument("--kind", choices=KINDS, default="check")
    parser.add_argument("--minutes", type=float, default=300.0, help="how long the run waits (300)")
    parser.add_argument("--engine", default=ENGINE, help=f"the convergence engine's address ({ENGINE})")
    parser.add_argument("--plan", default="", help="a gate's plan, which the approver reads (8 KB at most)")
    parser.add_argument("--stage", choices=("both", "ask", "wait"), default="both",
                        help="ask: ask, and follow it until its plan is walked (the approval box, and the "
                             "request's id as the step output `request`); wait: follow --request to its end")
    parser.add_argument("--request", default="", help="wait: the request `ask` made")
    args = parser.parse_args(argv)
    env = dict(os.environ) if env is None else env

    def appender(path: str | None) -> Callable[[str], None]:
        def write(text: str) -> None:
            if path:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(text + "\n")
        return write
    summary, output = appender(env.get("GITHUB_STEP_SUMMARY")), appender(env.get("GITHUB_OUTPUT"))
    http = Http(send=send, token=actions_token(env), summary=summary,
                output=lambda name, value: output(f"{name}={value}"))
    if args.stage == "ask":
        asked = ask(http, args.engine, args.unit, args.kind, http.clock() + args.minutes * 60, args.plan)
        return asked if isinstance(asked, int) else 0
    if args.stage == "wait":
        if not args.request:
            parser.error("--stage wait needs --request, the id `--stage ask` gave")
        return wait(http, args.engine, args.unit, args.kind, args.minutes, args.request)
    return service_needs(http, args.engine, args.unit, args.kind, args.minutes, args.plan)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
