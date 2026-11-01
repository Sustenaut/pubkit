"""Steps for `pubkit github-app create`, end to end against three fakes: a bws
on PATH, which logs its arguments and environment and reads a key the way the
real one does; a browser, named by BROWSER, which follows the page pubkit
serves; and GitHub's API, an HTTP server on 127.0.0.1. Nothing reaches the network.

A scenario runs in a session of its own, with no controlling terminal, so that
the hidden prompt reads its standard input wherever the suite is run.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import pubkit
from pytest_bdd import given, parsers, scenarios, then, when

from pubkit.plugin import Completed

scenarios("github-app.feature")

PROJECT_ID = "11111111-2222-3333-4444-555555555555"
APP_ID = 5551234
INSTALLATION_ID = 7770001
INSTALLATION_TOKEN = "ghs_the-installation-token"

FAKE_BWS = f"""#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
token = os.environ.get("BWS_ACCESS_TOKEN", "")
with open(os.environ["BWS_LOG"], "a") as log:
    log.write(json.dumps({{"args": args, "token": token}}) + "\\n")
# The real one's parser: a value that starts with "-", unless after "--" or
# in "--value=", is an unexpected argument, and it prints it back.
for arg in args[:args.index("--")] if "--" in args else args:
    if arg.startswith("-----"):
        sys.stderr.write(f"error: unexpected argument '{{arg}}' found\\n")
        sys.exit(2)
if args[:2] == ["project", "list"]:
    print(json.dumps([{{"id": "{PROJECT_ID}", "name": "the-project"}}]))
elif args[:2] == ["secret", "list"]:
    print(json.dumps(json.loads(os.environ.get("BWS_EXISTING", "[]"))))
elif token == os.environ.get("BWS_REFUSE"):
    sys.stderr.write("403 Forbidden, for " + " ".join(args) + " with " + token + "\\n")
    sys.exit(1)
"""

FAKE_BROWSER = f"""#!{sys.executable}
import html, json, os, re, sys, urllib.error, urllib.parse, urllib.request
url = sys.argv[1]
record = {{"url": url}}
if url.startswith("http://127.0.0.1"):
    page = urllib.request.urlopen(url).read().decode()
    action = html.unescape(re.search(r'action="([^"]*)"', page).group(1))
    manifest = json.loads(html.unescape(re.search(r'name="manifest" value="([^"]*)"', page).group(1)))
    split = urllib.parse.urlsplit(action)
    state = urllib.parse.parse_qs(split.query)["state"][0]
    record |= {{"posted_to": f"{{split.scheme}}://{{split.netloc}}{{split.path}}", "manifest": manifest}}
    back = manifest["redirect_url"]
    if os.environ.get("BROWSER_WRONG_STATE"):
        try:
            urllib.request.urlopen(back + "?code=the-code&state=wrong")
        except urllib.error.HTTPError as error:
            record["wrong_state"] = error.code
    urllib.request.urlopen(back + "?" + urllib.parse.urlencode({{"code": "the-code", "state": state}}))
with open(os.environ["BROWSER_LOG"], "a") as log:
    log.write(json.dumps(record) + "\\n")
"""


@pytest.fixture(scope="session")
def key(tmp_path_factory):
    """A throwaway key, and a file of its public half to check signatures with."""
    made = subprocess.run(["openssl", "genrsa", "-traditional", "2048"], check=True,
                          capture_output=True, text=True).stdout
    public = tmp_path_factory.mktemp("key") / "public.pem"
    subprocess.run(["openssl", "rsa", "-pubout", "-out", str(public)], input=made, check=True,
                   capture_output=True, text=True)
    return {"pem": made, "public": public}


def verified(jwt: str, public, tmp_path) -> bool:
    """Whether openssl, not pubkit's own code, finds it signed by the key, for this app and not expired."""
    head, claims, signature = jwt.split(".")
    decode = lambda part: base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))  # noqa: E731
    sig = tmp_path / "jwt.sig"
    sig.write_bytes(decode(signature))
    done = subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(public), "-signature", str(sig)],
                          input=f"{head}.{claims}".encode(), capture_output=True, check=False)
    told = json.loads(decode(claims))
    return (done.returncode == 0 and json.loads(decode(head))["alg"] == "RS256"
            and told["iss"] == str(APP_ID) and told["exp"] - told["iat"] <= 600)


@pytest.fixture
def api(key, tmp_path, env):
    """A fake GitHub API, which records what it was asked."""
    state = {"codes": [], "jwt_ok": [], "looks": 0, "answers": [], "refuse": False,
             "selection": "all", "repositories": [], "refuse_token": False, "listed_with": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            pass

        def reply(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):  # noqa: N802
            if self.path == f"/app/installations/{INSTALLATION_ID}/access_tokens":
                jwt = self.headers.get("Authorization", "").removeprefix("Bearer ")
                state["jwt_ok"].append(verified(jwt, key["public"], tmp_path))
                if state["refuse_token"]:
                    self.reply(403, {"message": "Forbidden"})
                else:
                    self.reply(201, {"token": INSTALLATION_TOKEN})
                return
            state["codes"].append(self.path)
            if state["refuse"] or self.path != "/app-manifests/the-code/conversions":
                self.reply(404, {"message": "Not Found"})
                return
            self.reply(201, {"id": APP_ID, "slug": "the-app", "pem": key["pem"],
                             "client_id": "Iv1.x", "client_secret": "the-client-secret",
                             "webhook_secret": "the-webhook-secret"})

        def do_GET(self):  # noqa: N802
            url = urllib.parse.urlsplit(self.path)
            if url.path == "/installation/repositories":
                state["listed_with"].append(self.headers.get("Authorization", ""))
                query = urllib.parse.parse_qs(url.query)
                size, page = int(query["per_page"][0]), int(query["page"][0])
                names = state["repositories"][(page - 1) * size:page * size]
                self.reply(200, {"total_count": len(state["repositories"]),
                                 "repositories": [{"full_name": name} for name in names]})
                return
            jwt = self.headers.get("Authorization", "").removeprefix("Bearer ")
            state["jwt_ok"].append(verified(jwt, key["public"], tmp_path))
            answers = state["answers"]
            ready = [{"id": 1, "account": {"login": "someone-else"}},
                     {"id": INSTALLATION_ID, "account": {"login": state["owner"].upper()},
                      **({"repository_selection": state["selection"]} if state["selection"] else {})}]
            self.reply(200, answers.pop(0) if answers else ready)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env["PUBKIT_GITHUB_API_URL"] = f"http://127.0.0.1:{server.server_address[1]}"
    yield state
    server.shutdown()
    server.server_close()


@pytest.fixture
def fakes(tmp_path, env, api):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in (("bws", FAKE_BWS), ("browser", FAKE_BROWSER)):
        path = bin_dir / name
        path.write_text(text)
        path.chmod(0o755)
    env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    env["BROWSER"] = str(bin_dir / "browser")
    env["BROWSER_LOG"] = str(tmp_path / "browser.log")
    env["BWS_LOG"] = str(tmp_path / "bws.log")
    return tmp_path


def _write(repo, api, section, name="the-app"):
    api["owner"] = section.get("org") or section.get("user") or ""
    (repo / "app.json").write_text(json.dumps({
        "name": name, "url": "https://example.org", "public": False,
        "default_permissions": {"contents": "read"},
        "pubkit": {"bitwarden_project": "the-project", "secret_prefix": "the-app", **section}}))


@given(parsers.parse('a manifest for the org "{owner}"'))
def manifest_org(repo, api, fakes, owner):
    _write(repo, api, {"org": owner})


@given(parsers.parse('a manifest for the user "{owner}"'))
def manifest_user(repo, api, fakes, owner):
    _write(repo, api, {"user": owner})


@given(parsers.parse('a manifest whose project is "{project}"'))
def manifest_project(repo, api, fakes, project):
    _write(repo, api, {"org": "example-org", "bitwarden_project": project})


@given(parsers.parse("the manifest reads {text}"))
def manifest_text(repo, fakes, text):
    (repo / "app.json").write_text(text)


@given("the first look finds no installation, and the next one on another account too")
def late_installation(api):
    api["answers"] = [[], [{"id": 1, "account": {"login": "someone-else"}}]]


@given("the browser first comes back with the wrong state")
def wrong_state(env):
    env["BROWSER_WRONG_STATE"] = "1"


def _existing(env, name, times):
    env["BWS_EXISTING"] = json.dumps([{"id": f"secret-{i}-{name}", "key": name, "value": "old-value"}
                                      for i in range(times)])


@given(parsers.parse('Bitwarden already has "{name}"'))
def has_one(env, name):
    _existing(env, name, 1)


@given(parsers.parse('Bitwarden already has "{name}" twice'))
def has_two(env, name):
    _existing(env, name, 2)


@given(parsers.parse('bws refuses writes with the token "{token}", printing what it was given'))
def refuses(env, token):
    env["BWS_REFUSE"] = token


@given(parsers.parse('the app is installed on the selected repositories "{first}" and "{second}"'))
def selected(api, first, second):
    api["selection"] = "selected"
    api["repositories"] = [first, second]


@given(parsers.parse("the app is installed on {count:d} selected repositories"))
def many_selected(api, count):
    api["selection"] = "selected"
    api["repositories"] = [f"example-org/repo-{i}" for i in range(count)]


@given("GitHub refuses the installation token")
def refuses_token(api):
    api["refuse_token"] = True


@given("GitHub does not say which repositories the app reaches")
def no_selection(api):
    api["selection"] = None


@given("GitHub refuses the code")
def refuses_code(api):
    api["refuse"] = True


@given("bws is not installed")
def no_bws(tmp_path, env):
    (tmp_path / "bin" / "bws").unlink()
    env["PATH"] = str(tmp_path / "bin")


def _create(repo, env, flags, lines):
    argv = [pubkit(), "github-app", "create", *flags, "app.json"]
    done = subprocess.run(argv, input="".join(f"{line}\n" for line in lines).encode(), cwd=repo,
                          env={**os.environ, **env}, capture_output=True, timeout=60,
                          start_new_session=True, check=False)
    return Completed(argv, done.returncode, done.stdout, done.stderr)


@when(parsers.parse('I create the app, giving the token "{token}" and answering "{answer}"'), target_fixture="result")
def create(repo, env, token, answer):
    return _create(repo, env, [], [token, answer])


@when(parsers.parse('I create the app with "{flag}", giving the token "{token}" and answering "{answer}"'), target_fixture="result")
def create_flag(repo, env, flag, token, answer):
    return _create(repo, env, [flag], [token, answer])


@when("I create the app, giving no token", target_fixture="result")
def create_no_token(repo, env):
    return _create(repo, env, [], ["", "yes"])


@when(parsers.parse('I create the app with "{flag}", giving nothing'), target_fixture="result")
def create_nothing(repo, env, flag):
    return _create(repo, env, [flag], [])


@when(parsers.parse('I create the app, giving the tokens "{first}" and "{second}" and answering "{answer}"'), target_fixture="result")
def create_two(repo, env, first, second, answer):
    return _create(repo, env, [], [first, answer, second])


def _lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def browser(tmp_path):
    return _lines(tmp_path / "browser.log")


def bws_calls(tmp_path):
    return _lines(tmp_path / "bws.log")


def writes(tmp_path):
    return [call for call in bws_calls(tmp_path) if call["args"][:2] in (["secret", "create"], ["secret", "edit"])]


def _succeeded(tmp_path, token):
    """The calls that got through: bws took them and did not refuse the token."""
    return [call for call in writes(tmp_path) if call["token"] == token]


def created(tmp_path, name):
    for call in writes(tmp_path):
        args = call["args"]
        if args[1] == "create" and args[args.index("--") + 1] == name:
            return args[args.index("--") + 2:]
    raise AssertionError(f"{name} was not created: {writes(tmp_path)}")


@then(parsers.parse('the browser posted to "{url}"'))
def posted_to(tmp_path, url):
    assert browser(tmp_path)[0]["posted_to"] == url


@then("GitHub was sent the manifest with a redirect to 127.0.0.1 and no pubkit section")
def sent_manifest(tmp_path):
    manifest = browser(tmp_path)[0]["manifest"]
    assert "pubkit" not in manifest
    assert manifest["name"] == "the-app"
    assert manifest["default_permissions"] == {"contents": "read"}
    assert manifest["redirect_url"].startswith("http://127.0.0.1:")
    assert manifest["redirect_url"].endswith("/callback")


@then(parsers.parse('the browser opened "{url}"'))
def opened(tmp_path, url):
    assert url in [record["url"] for record in browser(tmp_path)]


@then("no browser opened")
def none_opened(tmp_path):
    assert browser(tmp_path) == []


@then("GitHub was asked for the installations with a JWT signed by the new key")
def signed(api):
    assert api["jwt_ok"] and all(api["jwt_ok"])


@then(parsers.parse('bws created "{name}" as "{value}"'))
def created_as(tmp_path, name, value):
    assert created(tmp_path, name) == [value, PROJECT_ID]


@then(parsers.parse('bws created "{name}" as the key'))
def created_key(tmp_path, key, name):
    assert created(tmp_path, name) == [key["pem"], PROJECT_ID]


@then(parsers.parse('bws edited the existing "{name}" to the key'))
def edited(tmp_path, key, name):
    edits = [call["args"] for call in writes(tmp_path) if call["args"][1] == "edit"]
    assert len(edits) == 1
    assert f"--value={key['pem']}" in edits[0]
    assert edits[0][-1] == f"secret-0-{name}"


@then(parsers.parse('bws wrote {count:d} secrets with the token "{token}"'))
def wrote(tmp_path, count, token):
    assert len(_succeeded(tmp_path, token)) == count, writes(tmp_path)


@then(parsers.parse("bws was called {count:d} times"))
def called(tmp_path, count):
    assert len(bws_calls(tmp_path)) == count


@then("neither the key nor the token is printed")
def nothing_printed(result, key):
    printed = result.stdout + result.stderr
    for line in key["pem"].splitlines()[1:-1]:
        assert line not in printed
    assert "PRIVATE KEY" not in printed
    assert "the-client-secret" not in printed
    assert "the-webhook-secret" not in printed
    assert "-token" not in printed


@then("the token is on no command line")
def not_on_command_line(tmp_path):
    for call in bws_calls(tmp_path):
        assert not any(call["token"] in arg for arg in call["args"])


@then("no file under the repository holds the key")
def not_on_disk(repo, key):
    body = key["pem"].splitlines()[1]
    for path in repo.rglob("*"):
        if path.is_file():
            assert body not in path.read_text(errors="replace"), path


@then("the list was asked for with the installation's own token")
def listed_with_token(api):
    assert api["listed_with"] == [f"Bearer {INSTALLATION_TOKEN}"]


@then("the wrong state was answered with 400")
def wrong_answered(tmp_path):
    assert browser(tmp_path)[0]["wrong_state"] == 400


@then("GitHub was given the code once")
def code_once(api):
    assert api["codes"] == ["/app-manifests/the-code/conversions"]
