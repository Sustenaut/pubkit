"""Steps for `pubkit audit prepare` and `pubkit audit draft`, against the
fixture crates in tests/fixtures/audit, each copied into a scratch git
repository with a release tag and a change after it.

`audit draft` talks to a fake Claude API: an HTTP server on 127.0.0.1 that
records each request and answers in the Messages API's event stream, so
nothing here reaches the network.
"""

from __future__ import annotations

import json
import re
import shutil
import stat
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from pubkit import audit
from pubkit.audit_draft import SCHEMA

scenarios("audit-prepare.feature")
scenarios("audit-draft.feature")

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "audit"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True,
                          env={"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.org",
                               "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.org",
                               "HOME": str(repo), "PATH": "/usr/bin:/bin"}).stdout.strip()


def crate(repo: Path, name: str, tag: str):
    shutil.copytree(FIXTURES / name, repo, dirs_exist_ok=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "the release")
    git(repo, "tag", tag)
    source = next(p for p in (repo / "src" / "lib.rs", repo / "src" / "main.rs") if p.is_file())
    source.write_text(source.read_text() + "\n// A change since the release.\n")
    git(repo, "commit", "-q", "-am", "a change since the release")


@given(parsers.parse('the crate "{name}" released as "{tag}" and changed since, with pubrel giving "{version}"'))
def released_crate(repo, env, tmp_path, name, tag, version):
    crate(repo, name, tag)
    pubrel = tmp_path / "fake-pubrel"
    pubrel.write_text(f'#!/bin/sh\n[ "$1" = next ] && echo "{version}"\n')
    pubrel.chmod(pubrel.stat().st_mode | stat.S_IEXEC)
    env["PUBREL"] = str(pubrel)
    env["PUBKIT_TODAY"] = "2026-10-28"


@given(parsers.parse('the crate "{name}" released as "{tag}" and changed since, with no pubrel'))
def released_crate_no_pubrel(repo, env, tmp_path, name, tag):
    crate(repo, name, tag)
    env["PUBREL"] = str(tmp_path / "no-such-pubrel")


def report_text(repo, package, version) -> str:
    return (repo / "audit" / f"{package}-{version}.md").read_text()


@then(parsers.parse('the report "{package}" "{version}" has the header "{line}"'))
def has_header(repo, package, version, line):
    head = report_text(repo, package, version).split("\n---\n", 1)[0]
    assert any(l.startswith(line) for l in head.splitlines()), head


@then(parsers.parse('the report "{package}" "{version}" names the current standard and the commit audited'))
def names_standard(repo, package, version):
    fields = audit.header(report_text(repo, package, version))
    assert fields["standard"] == audit.current()
    assert fields["commit"] == git(repo, "rev-parse", "HEAD")


@then(parsers.parse('the report "{package}" "{version}" lists under "{section}" "{finding}"'))
def lists_under(repo, package, version, section, finding):
    text = report_text(repo, package, version)
    body = text.split(f"\n## {section}\n", 1)[1].split("\n## ", 1)[0]
    assert finding in body, body


@then(parsers.parse('the report "{package}" "{version}" marks "{rule}" for review, having found "{evidence}"'))
def marks_for_review(repo, package, version, rule, evidence):
    text = report_text(repo, package, version)
    block = text.split(f"### {rule}", 1)[1].split("<!-- /rule -->", 1)[0]
    assert "**[needs review]**" in block.splitlines()[0], block
    assert evidence in block, block


@given(parsers.parse('a person reviews the report "{package}" "{version}" and resolves what blocks'))
@when(parsers.parse('a person reviews the report "{package}" "{version}" and resolves what blocks'))
def person_reviews(repo, package, version):
    path = repo / "audit" / f"{package}-{version}.md"
    text = audit.PENDING.sub("[reviewed]", path.read_text())
    text = re.sub(r"<!-- rule (\S+) (review|drafted) -->", r"<!-- rule \1 reviewed -->", text)
    path.write_text(re.sub(r"^blocking: \d+$", "blocking: 0", text, count=1, flags=re.M))


# --- a fake Claude API -----------------------------------------------------------


class FakeAPI:
    def __init__(self):
        self.requests: list[dict] = []
        self.mode = ("draft", 200, "")
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                api.requests.append({"path": self.path, "body": body,
                                     "headers": {k.lower(): v for k, v in self.headers.items()}})
                mode, status, message = api.mode
                if status != 200:
                    payload = json.dumps({"type": "error", "error": {"type": "authentication_error",
                                                                     "message": message}}).encode()
                    self.send_response(status)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                self.wfile.write(api.events(body, mode).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def events(self, body: dict, mode: str) -> str:
        ids = re.findall(r'<rule id="([^"]+)"', body["messages"][0]["content"])
        drafts = [{"id": key, "verdict": "falls short" if i == 0 else "met",
                   "rationale": f"The fake API's reasoning on §{key}.",
                   "findings": [{"severity": "blocks", "file": "src/lib.rs", "line": 1,
                                 "text": f"drafted for §{key}"}] if i == 0 else []}
                  for i, key in enumerate(ids)]
        text = json.dumps({"rules": drafts})
        half = len(text) // 2
        stop = "refusal" if mode == "refuse" else "end_turn"
        chunks = [] if mode == "refuse" else [text[:half], text[half:]]
        events = [("message_start", {"type": "message_start", "message": {"model": body["model"]}}),
                  ("ping", {"type": "ping"})]
        events += [("content_block_delta", {"type": "content_block_delta", "index": 0,
                                            "delta": {"type": "text_delta", "text": c}}) for c in chunks]
        events += [("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop}}),
                   ("message_stop", {"type": "message_stop"})]
        return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_api():
    api = FakeAPI()
    yield api
    api.close()


@given("a fake Claude API that drafts every rule it is sent")
def fake_claude(fake_api, env, repo):
    env["ANTHROPIC_BASE_URL"] = fake_api.url
    env["ANTHROPIC_API_KEY"] = "test-key"
    env["PUBKIT_AUDIT_MODEL"] = None
    blocking = audit.header(report_text(repo, "demo", "0.1.1"))["blocking"]
    env["resolved"] = {"blocking": int(blocking)}


@given("the fake Claude API declines")
def api_declines(fake_api):
    fake_api.mode = ("refuse", 200, "")


@given(parsers.parse('the fake Claude API answers {status:d} "{message}"'))
def api_errors(fake_api, status, message):
    fake_api.mode = ("draft", status, message)


@given(parsers.parse('PUBKIT_AUDIT_MODEL is "{model}"'))
def audit_model(env, model):
    env["PUBKIT_AUDIT_MODEL"] = model


@given("no ANTHROPIC_API_KEY")
def no_key(env):
    env["ANTHROPIC_API_KEY"] = None


@then("the report's blocking count rose by the drafted findings that block")
def blocking_rose(repo, env):
    fields = audit.header(report_text(repo, "demo", "0.1.1"))
    assert int(fields["blocking"]) == env["resolved"]["blocking"] + 1, fields


@then(parsers.parse('the API was asked by model "{model}" with the key "{key}"'))
def asked_by(fake_api, model, key):
    (request,) = fake_api.requests
    assert request["path"] == "/v1/messages"
    assert request["body"]["model"] == model
    assert request["headers"]["x-api-key"] == key
    assert request["headers"]["anthropic-version"]
    assert request["body"]["stream"] is True


@then("the API was asked for JSON matching the draft schema")
def asked_for_json(fake_api):
    (request,) = fake_api.requests
    assert request["body"]["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}


@then(parsers.parse('the API was sent the rule "{judged}" and not the checked rule "{checked}"'))
def sent_rules(fake_api, judged, checked):
    content = fake_api.requests[0]["body"]["messages"][0]["content"]
    assert f'<rule id="{judged}"' in content
    assert checked not in content.split("<diff>", 1)[0]


@then(parsers.parse('the API was sent the diff since "{tag}"'))
def sent_diff(fake_api, tag):
    content = fake_api.requests[0]["body"]["messages"][0]["content"]
    assert f"Its last release is {tag}" in content
    diff = content.split("<diff>", 1)[1]
    assert "+// A change since the release." in diff, diff
    assert "+pub fn thrice" not in diff


@then("the API was asked nothing")
def asked_nothing(fake_api):
    assert fake_api.requests == []


@given(parsers.parse('the audit of "{package}" has been prepared'))
def prepared(repo, run, env, package):
    from conftest import pubkit
    variables = {k: v for k, v in env.items() if k != "resolved"}
    done = run(pubkit(), "audit", "prepare", "--package", package, "--quick", cwd=repo, env=variables)
    assert done.code == 0, done


@then(parsers.parse('the report "{package}" "{version}" accounts for the docs.rs build and the MSRV build'))
def accounts_for_slow(repo, package, version):
    # Each ran, or the report says what was missing: a nightly toolchain, or
    # the crate's rust-version.
    text = report_text(repo, package, version)
    assert "docs.rs build" in text, text
    assert "(MSRV)" in text or "MSRV build (Rust 1.85)" in text, text
