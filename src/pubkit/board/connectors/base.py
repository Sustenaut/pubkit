"""What every connector shares: the calls it makes, and the facts that
come of them.

A connector reads its source through a `Transport` and logs each call in
the `Ledger`: its connector, method, endpoint, time and outcome, under an
id such as `c7`. What the connector returns is a `Fact`, a value with the
ids of the calls it came from, so board.json can say where every value on
the page was read.

`Network` is the network, and the only transport that reaches it; nothing
outside the connectors package imports it. `Replay`
answers from a file of recorded responses instead (the local lists
excepted), for the tests and for
re-rendering a board without asking anyone; `Recording` passes calls to
another transport and keeps what came back, for such a file. A token is
sent as a header and never recorded.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TIMEOUT = 20


@dataclass(frozen=True)
class Response:
    """What came back: a status and a body, or the error that stopped it."""

    status: int | None
    body: bytes = b""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300

    def json(self) -> Any:
        return json.loads(self.body) if self.body.strip() else None

    def message(self) -> str:
        """GitHub's or SonarCloud's own words for a refusal, else the status."""
        if self.error:
            return self.error
        try:
            data = self.json()
        except ValueError:
            data = None
        if isinstance(data, dict):
            if isinstance(data.get("message"), str):
                return f"{data['message']} (HTTP {self.status})"
            errors = data.get("errors")
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                return f"{errors[0].get('msg') or errors[0].get('message')} (HTTP {self.status})"
        return f"HTTP {self.status}"


@dataclass(frozen=True)
class Fact:
    """A value, the calls it was read from, and a note when it could not
    be read in full (the value is then None or partial)."""

    value: Any
    sources: tuple[str, ...] = ()
    note: str | None = None

    def as_json(self) -> dict:
        out: dict[str, Any] = {"value": self.value, "sources": list(self.sources)}
        if self.note:
            out["note"] = self.note
        return out


def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def stamp(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Ledger:
    """The calls of one board build, in order."""

    clock: Any = now
    calls: list[dict] = field(default_factory=list)

    def log(self, connector: str, method: str, endpoint: str, response: Response) -> str:
        call = {"id": f"c{len(self.calls) + 1}", "connector": connector, "method": method,
                "endpoint": endpoint, "at": stamp(self.clock()), "status": response.status}
        if response.error:
            call["error"] = response.error
        self.calls.append(call)
        return call["id"]


class Transport:
    """Fetches one URL: a GET, or a file read for `file:` endpoints."""

    def fetch(self, url: str, headers: dict[str, str]) -> Response:
        raise NotImplementedError


class Network(Transport):
    def fetch(self, url: str, headers: dict[str, str]) -> Response:
        if url.startswith("file:"):
            path = Path(url.removeprefix("file:"))
            try:
                return Response(200, path.read_bytes())
            except OSError as e:
                return Response(None, error=f"cannot read {path}: {e.strerror}")
        if not url.startswith("https://"):
            return Response(None, error=f"{url} is not https")
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:  # noqa: S310 - https only, above
                return Response(answer.status, answer.read())
        except urllib.error.HTTPError as e:
            return Response(e.code, e.read())
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            reason = getattr(e, "reason", e)
            return Response(None, error=f"no answer: {reason}")


class Replay(Transport):
    """Recorded responses, by URL; a URL not recorded has no answer."""

    def __init__(self, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        self.recorded_at = datetime.fromisoformat(data["recorded"].replace("Z", "+00:00"))
        self.responses = data["responses"]

    def fetch(self, url: str, headers: dict[str, str]) -> Response:
        if url.startswith("file:"):  # the lists, which are read where they are
            return Network().fetch(url, headers)
        found = self.responses.get(url)
        if found is None:
            return Response(None, error="not recorded")
        if "error" in found:
            return Response(None, error=found["error"])
        body = found["json"] if "json" in found else found.get("text", "")
        raw = body if isinstance(body, str) else json.dumps(body)
        return Response(found["status"], raw.encode("utf-8"))


class Recording(Transport):
    """`inner`'s answers, kept by URL for a replay file."""

    def __init__(self, inner: Transport):
        self.inner = inner
        self.responses: dict[str, dict] = {}

    def fetch(self, url: str, headers: dict[str, str]) -> Response:
        response = self.inner.fetch(url, headers)
        if response.error:
            self.responses[url] = {"error": response.error}
        else:
            try:
                kept: dict[str, Any] = {"status": response.status, "json": response.json()}
            except ValueError:
                kept = {"status": response.status,
                        "text": response.body.decode("utf-8", "replace")}
            self.responses[url] = kept
        return response

    def save(self, path: Path, recorded: datetime) -> None:
        path.write_text(json.dumps({"recorded": stamp(recorded), "responses": self.responses},
                                   indent=1, sort_keys=True) + "\n", encoding="utf-8")


class Connector:
    """One data source. Each public method is one named query, and the
    only way the board reads that source."""

    name = ""

    def __init__(self, ledger: Ledger, transport: Transport, headers: dict[str, str] | None = None):
        self.ledger = ledger
        self.transport = transport
        self.headers = headers or {}

    def get(self, method: str, url: str) -> tuple[Response, str]:
        """GET `url` for the query `method`; the response and its call id."""
        response = self.transport.fetch(url, self.headers)
        return response, self.ledger.log(self.name, method, url, response)
