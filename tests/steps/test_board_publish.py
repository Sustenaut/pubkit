"""Steps for `pubkit board-publish`, with a fake storage service in this process, and for
the repos file `pubkit state --repos-for` makes from a state file."""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from conftest import pubkit

scenarios("board_publish.feature")

TOKEN = "the-oidc-token"
REPO = "example-org/secretly-named"


def board(grade="A", built="2026-10-30T10:00:00Z"):
    return json.dumps({
        "built": built, "pubkit": "0.23.0", "rules": "r", "summary": f"Built {built}", "problems": [],
        "repos": [{"repo": REPO, "overall": grade,
                   "grades": {"quality": {"letter": grade, "steps": [{"text": "x", "sources": [built]}]}},
                   "facts": {"sonar": {"value": grade, "sources": [built]}}}],
        "calls": [{"id": built, "at": built}]}).encode()


class Fake:
    """What the fake storage holds and was asked."""

    def __init__(self):
        self.tenants = {}
        self.uploads = []
        self.moves = []
        self.logins = []
        self.refuse_read = None
        self.stolen = False


def _handler(fake):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _send(self, status, body=b"{}", headers=None):
            self.send_response(status)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/oidc"):
                fake.logins.append(self.path)
                return self._send(200, json.dumps({"value": TOKEN}).encode())
            assert self.headers["Authorization"] == f"Bearer {TOKEN}"
            if "Python" in self.headers["User-Agent"]:  # as Cloudflare refuses it
                return self._send(403, b"error code: 1010")
            if fake.refuse_read:
                return self._send(fake.refuse_read, b'{"error": "no"}')
            tenant = self.path.split("/")[1]
            held = fake.tenants.get(tenant)
            if not held:
                return self._send(404)
            return self._send(200, held, {"ETag": f'"{hashlib.sha256(held).hexdigest()}"'})

        def do_POST(self):
            length = int(self.headers["Content-Length"])
            body = self.rfile.read(length)
            tenant = self.path.split("/")[1]
            content = body.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0]
            fake.uploads.append((tenant, content))
            return self._send(200, json.dumps({"id": hashlib.sha256(content).hexdigest()}).encode())

        def do_PUT(self):
            length = int(self.headers["Content-Length"])
            tenant = self.path.split("/")[1]
            wanted = json.loads(self.rfile.read(length))["hash"]
            fake.moves.append(dict(self.headers))
            if fake.stolen:
                return self._send(412, b'{"error": "moved"}')
            return self._send(200, json.dumps({"hash": wanted, "tenant": tenant}).encode())

    return Handler


@pytest.fixture
def fake():
    return Fake()


@pytest.fixture
def storage(fake):
    server = HTTPServer(("127.0.0.1", 0), _handler(fake))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _publish(repo, run, storage, tenant, content, job=True):
    if content is not None:
        (repo / "site").mkdir(exist_ok=True)
        (repo / "site" / "board.json").write_bytes(content)
    variables = {"ACTIONS_ID_TOKEN_REQUEST_URL": f"{storage}/oidc?x=1",
                 "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request"} if job else {}
    return run(pubkit(), "board-publish", "--url", storage, "--tenant", tenant, cwd=repo, env=variables)


@given(parsers.parse('storage has no board for "{tenant}"'))
def no_board(fake, tenant):
    fake.tenants.pop(tenant, None)


@given(parsers.parse('storage\'s board for "{tenant}" has the grade "{grade}"'))
def has_board(fake, tenant, grade):
    fake.tenants[tenant] = board(grade)


@given("another writer moves the head first")
def stolen(fake):
    fake.stolen = True


@given(parsers.parse("storage refuses to read the head with {status:d}"))
def refuses(fake, status):
    fake.refuse_read = status


@when(parsers.re(r'I publish a board to "(?P<tenant>[^"]+)"$'), target_fixture="result")
def publish(repo, run, storage, tenant):
    return _publish(repo, run, storage, tenant, board())


@when(parsers.parse('I publish a board to "{tenant}" with the grade "{grade}", read later'),
      target_fixture="result")
def publish_later(repo, run, storage, tenant, grade):
    return _publish(repo, run, storage, tenant, board(grade, "2026-10-30T11:00:00Z"))


@when(parsers.parse('I publish a board to "{tenant}" outside a job'), target_fixture="result")
def publish_outside(repo, run, storage, tenant):
    return _publish(repo, run, storage, tenant, board(), job=False)


@when(parsers.parse('I publish a missing board to "{tenant}"'), target_fixture="result")
def publish_missing(repo, run, storage, tenant):
    return _publish(repo, run, storage, tenant, None)


@then(parsers.re(r'it uploaded (?P<count>\d+) files? to "(?P<tenant>[^"]+)"'))
def uploaded(fake, count, tenant):
    assert len([u for u in fake.uploads if u[0] == tenant]) == int(count)


@then('the head moved with "If-None-Match: *"')
def first_move(fake):
    assert fake.moves[0]["If-None-Match"] == "*"
    assert "If-Match" not in fake.moves[0]


@then("the head moved with If-Match on the old hash")
def later_move(fake):
    assert fake.moves[0]["If-Match"] == f'"{hashlib.sha256(board("A")).hexdigest()}"'


@then("the head did not move")
def no_move(fake):
    assert not fake.moves


@then("it signed in with the job's token for storage's host name")
def signed_in(fake):
    assert fake.logins == ["/oidc?x=1&audience=127.0.0.1"]


@then("its output names no repository and no token")
def quiet(result):
    for text in (REPO, TOKEN, "request"):
        assert text not in result.stdout + result.stderr


@given(parsers.parse('a state file for "{owner}"'), target_fixture="state")
def state_file(owner):
    return {"owner": owner, "repos": {
        "one": {"project": "tools", "board": True, "protect": True, "govern": False},
        "two": {"board": True, "protect": False, "govern": True},
        "three": {"board": False, "protect": False, "govern": True}}}


@when(parsers.parse('I turn it into the repos file for "{role}"'), target_fixture="repos")
def turn(repo, run, state, role):
    (repo / "state.json").write_text(json.dumps(state))
    out = run(pubkit(), "state", "--repos-for", role, cwd=repo)
    assert out.code == 0, out.stderr
    return json.loads(out.stdout)["repositories"]


@then(parsers.parse('it lists "{names}"'))
def lists(repos, names):
    assert [r["repo"] for r in repos] == names.split(", ")


@then(parsers.parse('"{repo}" has the project "{project}", protect on and govern off'))
def has_project(repos, repo, project):
    assert next(r for r in repos if r["repo"] == repo) == {
        "repo": repo, "protect": True, "govern": False, "project": project}


@then(parsers.parse('"{repo}" has no project'))
def no_project(repos, repo):
    assert "project" not in next(r for r in repos if r["repo"] == repo)
