"""`pubkit github-app create`: make a GitHub App from a manifest, and put its
three values in Bitwarden, where `pubkit app-secrets` finds them.

The manifest is the consuming repository's own, so pubkit names nothing of
anyone's. It is what GitHub's app-manifest flow takes, plus a `pubkit` section
that GitHub never sees:

    {
      "name": "some-app",
      "url": "https://example.org",
      "public": false,
      "default_permissions": {"contents": "read"},
      "pubkit": {
        "org": "some-org",            # or "user": "someone"
        "bitwarden_project": "some-project",
        "secret_prefix": "some-app"
      }
    }

In order, stopping at the first thing wrong, so a refusal comes before an app
exists: show the plan (where --dry-run ends), ask for a Bitwarden access token
with write access, and look for the names in the project. Then a page on
127.0.0.1 posts the manifest to GitHub, GitHub redirects back with a code, and
the code becomes the app and its key. The browser opens the app's install page,
and the app's own key is used to watch for the installation, and to say which
repositories it reaches. Last, typing "yes"
writes `<prefix>-app-id`, `<prefix>-app-key` and `<prefix>-installation-id`.

The token is only ever asked for, never read from a file or kept, and reaches
bws in its environment. The key stays in this process's memory, and is never
printed or written to disk; bws has no way to take a value but its command
line, so it is there for the moment each write takes.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import html
import itertools
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .app_secrets import wrong_value

SECTION = "pubkit"
SETTINGS = {"org", "user", "bitwarden_project", "secret_prefix"}
OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")
# So that ./with-secrets can export each of the three: it skips a name that
# is not a letter and then letters, digits and "_" once "-" becomes "_".
PREFIX = re.compile(r"[a-z][a-z0-9-]*")
POLL_SECONDS = 2
# RSASSA-PKCS1-v1_5's DigestInfo for SHA-256 (RFC 8017, 9.2).
SHA256_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
BWS_MISSING = ("bws is not installed: get it from https://github.com/bitwarden/sdk-sm/releases "
               "(or cargo install bws --locked).")


class Stop(Exception):
    """Something that ends the run, with what to tell the person running it."""


class BwsFailed(Stop):
    """bws said no, in words that hold neither the token nor a value."""


@dataclass(frozen=True)
class Plan:
    """What the manifest's `pubkit` section says, and what it sends GitHub."""

    manifest: dict
    owner: str
    org: bool
    project: str
    prefix: str

    @property
    def names(self) -> dict[str, str]:
        return {slot: f"{self.prefix}-{slot}" for slot in ("app-id", "app-key", "installation-id")}

    def apps_url(self) -> str:
        """Where the owner's apps are: an organization's, or the signed-in user's."""
        account = f"organizations/{self.owner}/" if self.org else ""
        return f"https://github.com/{account}settings/apps"

    def installation_url(self, installation: int) -> str:
        """Where the owner chooses which repositories an installation reaches."""
        account = f"organizations/{self.owner}/" if self.org else ""
        return f"https://github.com/{account}settings/installations/{installation}"

    def new_app_url(self) -> str:
        return f"{self.apps_url()}/new"

    def settings_url(self, slug: str) -> str:
        return f"{self.apps_url()}/{urllib.parse.quote(slug, safe='')}"


def problems(manifest: Any) -> list[str]:
    """What is wrong with a manifest: one phrase each, none when sound."""
    if not isinstance(manifest, dict):
        return ["it must be an object"]
    out = [f'it needs "{field}"' for field in ("name", "url")
           if not (isinstance(manifest.get(field), str) and manifest[field])]
    if "redirect_url" in manifest:
        out.append('"redirect_url" is not its to set: pubkit points it at itself')
    section = manifest.get(SECTION)
    if not isinstance(section, dict):
        return [*out, f'it needs a "{SECTION}" section']
    out += [f'{key} is not a {SECTION} setting' for key in sorted(section.keys() - SETTINGS)]
    owners = [key for key in ("org", "user") if key in section]
    if len(owners) != 1:
        out.append(f'{SECTION} names its owner with one of "org" and "user"')
    elif not (isinstance(section[owners[0]], str) and OWNER.fullmatch(section[owners[0]])):
        out.append(f'{SECTION}\'s "{owners[0]}" is not an account\'s name')
    project = section.get("bitwarden_project")
    if not (isinstance(project, str) and project.strip()):
        out.append(f'{SECTION} needs "bitwarden_project", the project\'s name or id')
    prefix = section.get("secret_prefix")
    if not (isinstance(prefix, str) and PREFIX.fullmatch(prefix)):
        out.append(f'{SECTION}\'s "secret_prefix" must be lower case letters, digits and -, '
                   "starting with a letter")
    return out


def load(path: Path) -> Plan:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise Stop(f"cannot read {path}: {error.strerror}") from None
    except ValueError as error:
        raise Stop(f"{path} is not JSON: {error}") from None
    if bad := problems(manifest):
        raise Stop(f"{path}: {'; '.join(bad)}")
    section = manifest.pop(SECTION)
    org = "org" in section
    return Plan(manifest, section["org" if org else "user"], org, section["bitwarden_project"],
                section["secret_prefix"])


def scrub(text: str, hidden: tuple[str, ...]) -> str:
    """`text` with every hidden value, and each line of one, taken out."""
    for value in hidden:
        for part in [value, *value.splitlines()]:
            if part:
                text = text.replace(part, "[hidden]")
    return text


def bws(token: str, *args: str, hide: tuple[str, ...] = ()) -> str:
    """Run bws with the token in its environment, and return what it printed."""
    done = subprocess.run(["bws", *args], env={**os.environ, "BWS_ACCESS_TOKEN": token},
                          capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise BwsFailed(scrub(done.stderr, (token, *hide)).strip() or f"bws exited {done.returncode}")
    return done.stdout


def project_id(token: str, plan: Plan) -> str:
    projects = _listed(bws(token, "project", "list", "--output", "json", "--color", "no"))
    found = [p["id"] for p in projects if plan.project in (p.get("id"), p.get("name"))]
    if len(found) != 1:
        sees = ", ".join(sorted(str(p.get("name")) for p in projects)) or "none"
        raise Stop(f"this token's machine account sees {len(found)} projects named {plan.project}, "
                   f"not one. It sees: {sees}.")
    return found[0]


def existing_ids(token: str, project: str) -> dict[str, list[str]]:
    """Each name in the project and its secrets' ids. bws sends the values too; they go no further."""
    out: dict[str, list[str]] = {}
    for secret in _listed(bws(token, "secret", "list", project, "--output", "json", "--color", "no")):
        out.setdefault(secret["key"], []).append(secret["id"])
    return out


def _listed(printed: str) -> list[dict]:
    try:
        listed = json.loads(printed)
    except ValueError:
        raise Stop("bws printed something other than JSON") from None
    if not isinstance(listed, list):
        raise Stop("bws printed something other than a list")
    return listed


def targets(plan: Plan, existing: dict[str, list[str]], replace: bool) -> dict[str, str | None]:
    """Each name and the secret to edit, None to create it; stops if that isn't clear."""
    names = list(plan.names.values())
    if not replace and (taken := [name for name in names if name in existing]):
        raise Stop(f"already in project {plan.project}: {', '.join(taken)}. "
                   "No app was made. --replace overwrites them.")
    if twice := [name for name in names if len(existing.get(name, [])) > 1]:
        raise Stop(f"more than one secret in project {plan.project} has the name "
                   f"{', '.join(twice)}, so which to replace is not clear. No app was made.")
    return {name: existing[name][0] if name in existing else None for name in names}


def github(method: str, path: str, what: str, bearer: str | None = None) -> Any:
    """One call to GitHub's API. `what` names it in errors: a path can hold the one-time code."""
    base = os.environ.get("PUBKIT_GITHUB_API_URL", "https://api.github.com").rstrip("/")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "pubkit"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    request = urllib.request.Request(f"{base}{path}", method=method, headers=headers,
                                     data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            said = json.loads(error.read()).get("message", "")
        except (ValueError, AttributeError):
            said = ""
        raise Stop(f"GitHub answered {what} with {error.code} {said}".rstrip()) from None
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise Stop(f"could not reach GitHub for {what}: {error}") from None


def await_code(plan: Plan) -> str:
    """Send the browser through GitHub's form, and return the code it comes back with."""
    state = secrets.token_urlsafe(32)
    arrived = threading.Event()
    code: list[str] = []
    page: list[bytes] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            """The request line holds the code: it stays out of the terminal."""

        def answer(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            # Only what this machine's browser addresses to this server, so a
            # site cannot reach it by rebinding a name of its own to 127.0.0.1.
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_address[1]}":
                self.answer(403, b"Not for this host.")
                return
            url = urllib.parse.urlsplit(self.path)
            if url.path == "/":
                self.answer(200, page[0])
                return
            if url.path != "/callback":
                self.answer(404, b"Nothing here.")
                return
            query = urllib.parse.parse_qs(url.query)
            given = query.get("state", [""])[0]
            if not hmac.compare_digest(given.encode(), state.encode()) or not query.get("code"):
                self.answer(400, b"This did not come from the form pubkit sent. Still waiting.")
                return
            code.append(query["code"][0])
            self.answer(200, b"GitHub has made the app. Go back to the terminal.")
            arrived.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    sent = json.dumps({**plan.manifest, "redirect_url": f"http://127.0.0.1:{port}/callback"})
    action = f"{plan.new_app_url()}?{urllib.parse.urlencode({'state': state})}"
    page.append(f"""<!doctype html><meta charset="utf-8"><title>pubkit github-app</title>
<form method="post" action="{html.escape(action)}">
<input type="hidden" name="manifest" value="{html.escape(sent)}">
<button>Create the GitHub App</button></form>
<script>document.forms[0].submit()</script>
""".encode())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        local = f"http://127.0.0.1:{port}/"
        print(f"Opening {local} , which sends the manifest to GitHub. Open it yourself if no browser "
              "comes up, and check and confirm the app there.", flush=True)
        webbrowser.open(local)
        arrived.wait()
        return code[0]
    finally:
        server.shutdown()
        server.server_close()


def rsa_numbers(pem: str) -> tuple[int, int]:
    """The modulus and private exponent of a PKCS#1 key, the form GitHub gives."""

    def items(data: bytes):
        at = 0
        while at < len(data):
            size = data[at + 1]
            at += 2
            if size & 0x80:
                count = size & 0x7f
                size = int.from_bytes(data[at:at + count])
                at += count
            yield data[at:at + size]
            at += size

    try:
        if "BEGIN RSA PRIVATE KEY" not in pem:
            raise ValueError
        der = base64.b64decode("".join(line for line in pem.splitlines() if not line.startswith("-----")))
        numbers = [int.from_bytes(item) for item in items(next(items(der)))]
        return numbers[1], numbers[3]
    except (ValueError, IndexError, StopIteration):
        raise Stop("GitHub's key is not a PKCS#1 RSA key, so pubkit cannot sign with it") from None


def app_jwt(app_id: int, pem: str, now: int | None = None) -> str:
    """A token for GitHub's /app endpoints, RS256 with no dependency: this signs
    a few requests on the owner's own machine, so its timing shows nobody anything."""
    issued = int(time.time()) if now is None else now

    def part(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    claims = {"iat": issued - 60, "exp": issued + 540, "iss": str(app_id)}
    signed = (part(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()) + "."
              + part(json.dumps(claims).encode()))
    modulus, exponent = rsa_numbers(pem)
    size = (modulus.bit_length() + 7) // 8
    digest = SHA256_INFO + hashlib.sha256(signed.encode()).digest()
    padded = b"\x00\x01" + b"\xff" * (size - len(digest) - 3) + b"\x00" + digest
    signature = pow(int.from_bytes(padded), exponent, modulus).to_bytes(size)
    return f"{signed}.{part(signature)}"


def await_installation(plan: Plan, app: dict) -> dict:
    """Open the install page, and wait until the owner has installed the app.

    An error only says so and waits on: giving up would throw away a key that
    has been seen once. Ctrl-C ends it, and the run has already said what's left.
    """
    slug = urllib.parse.quote(app["slug"], safe="")
    url = f"https://github.com/apps/{slug}/installations/new"
    print(f"Opening {url} : install the app on {plan.owner}. Waiting for that (Ctrl-C to stop).", flush=True)
    webbrowser.open(url)
    said = None
    while True:
        try:
            installs = github("GET", "/app/installations?per_page=100", "the list of installations",
                              bearer=app_jwt(app["id"], app["pem"]))
        except Stop as error:
            if str(error) != said:
                print(f"  {error}. Still trying.", flush=True)
            said = str(error)
        else:
            for install in installs:
                if str(install.get("account", {}).get("login", "")).casefold() == plan.owner.casefold():
                    return install
        time.sleep(POLL_SECONDS)


def selected_repositories(app: dict, installation: int) -> list[str]:
    """The repositories an installation reaches, which only its own token can
    list. That token lasts an hour and goes no further than this function."""
    token = github("POST", f"/app/installations/{installation}/access_tokens",
                   "the request for an installation token", bearer=app_jwt(app["id"], app["pem"]))["token"]
    names: list[str] = []
    for page in itertools.count(1):
        listed = github("GET", f"/installation/repositories?per_page=100&page={page}",
                        "the list of repositories", bearer=token)["repositories"]
        names += [repository["full_name"] for repository in listed]
        if len(listed) < 100:
            return names


def report_reach(plan: Plan, app: dict, install: dict) -> None:
    """Say which repositories the app reached. Nothing here stops the run: the
    key is not written yet, and the reach can be changed at any time."""
    print(f"Installed on {plan.owner}, installation id {install['id']}.")
    selection = install.get("repository_selection")
    narrow = f"Change that at {plan.installation_url(install['id'])}"
    if selection == "selected":
        try:
            names = selected_repositories(app, install["id"])
        except (Stop, KeyError, TypeError) as error:
            print(f"It reaches selected repositories, but pubkit could not list them: {error}. {narrow}")
            return
        print(f"It reaches {len(names)} selected repositories: {', '.join(sorted(names)) or 'none'}. {narrow}")
    elif selection == "all":
        print(f"It reaches ALL of {plan.owner}'s repositories, and any made later. {narrow}")
    else:
        print(f"GitHub did not say which repositories it reaches. Check at {plan.installation_url(install['id'])}")


def make_app(plan: Plan) -> dict:
    code = await_code(plan)
    app = github("POST", f"/app-manifests/{urllib.parse.quote(code, safe='')}/conversions",
                 "the manifest's code")
    if not isinstance(app, dict) or not all(app.get(field) for field in ("id", "slug", "pem")):
        raise Stop("GitHub's answer has no app id, slug and key")
    if (wrong := wrong_value("key", str(app["pem"]))) is not None:
        raise Stop(f"GitHub's key {wrong}")
    rsa_numbers(app["pem"])
    return app


def ask_hidden(prompt: str) -> str:
    try:
        return getpass.getpass(prompt).strip()
    except EOFError:
        return ""


def write_one(token: str, project: str, name: str, value: str, secret: str | None, hide: tuple[str, ...]) -> None:
    """bws reads a value starting with "-" as an option, and prints it back as
    a mistake, which a key's first line would be: so "--" and "--value=", always."""
    quiet = ("--output", "none", "--color", "no")
    if secret is None:
        bws(token, "secret", "create", *quiet, "--", name, value, project, hide=hide)
    else:
        bws(token, "secret", "edit", *quiet, f"--value={value}", "--", secret, hide=hide)


def write_all(token: str, project: str, values: dict[str, str], ids: dict[str, str | None], pem: str) -> list[str]:
    """Write each value. On a failure, a key that was seen once is worth another
    token: the ones left are tried until they go or nobody offers one. Returns the ones left."""
    left = list(values)
    while True:
        for name in list(left):
            try:
                write_one(token, project, name, values[name], ids[name], (pem,))
            except BwsFailed as error:
                print(f"  FAILED   {name}: {error}")
            else:
                print(f"  wrote    {name}")
                left.remove(name)
        if not left:
            return []
        print(f"{len(left)} not written: {', '.join(left)}.", flush=True)
        token = ask_hidden("Another Bitwarden access token to try those with (Enter to stop): ")
        if not token:
            return left


def create(path: Path, replace: bool, dry_run: bool) -> int:
    if shutil.which("bws") is None:
        raise Stop(BWS_MISSING)
    plan = load(path)
    print(f"App:               {plan.manifest['name']}, owned by {plan.owner} "
          f"({'organization' if plan.org else 'user'})")
    print(f"Bitwarden project: {plan.project}")
    print("Secrets:")
    for name in plan.names.values():
        print(f"  {name}")
    if dry_run:
        print("--dry-run: no app was made and nothing was written.")
        return 0
    token = ask_hidden(f"Bitwarden access token with write access to {plan.project} "
                       "(hidden, and not kept): ")
    if not token:
        raise Stop("no access token, so no app was made")
    project = project_id(token, plan)
    ids = targets(plan, existing_ids(token, project), replace)

    app = make_app(plan)
    print(f"GitHub made the app {app['slug']}, id {app['id']}. If this run ends before its key is "
          f"written, the key is gone: make a new one, or delete the app, at {plan.settings_url(app['slug'])}",
          flush=True)
    if unkept := [field for field in ("client_secret", "webhook_secret") if app.get(field)]:
        print(f"Not kept: {' and '.join(unkept)}. Make new ones in the app's settings if it needs them.")
    install = await_installation(plan, app)
    report_reach(plan, app, install)

    names = plan.names
    values = {names["app-id"]: str(app["id"]), names["app-key"]: app["pem"],
              names["installation-id"]: str(install["id"])}
    print(f"To write in project {plan.project}:")
    for name, secret in ids.items():
        print(f"  {'replace' if secret else 'create '}  {name}")
    try:
        answer = input('Type "yes" to write them: ')
    except EOFError:
        answer = ""
    if answer.strip() != "yes":
        print(f"Nothing was written, and the key is gone with this run. Make a new one, or delete the "
              f"app, at {plan.settings_url(app['slug'])}")
        return 1
    if left := write_all(token, project, values, ids, app["pem"]):
        print(f"Not written: {', '.join(left)}. Any key among them is gone with this run: make a new "
              f"one at {plan.settings_url(app['slug'])}")
        return 1
    print(f"Done: {len(values)} secrets in project {plan.project}.")
    return 0


def run(path: Path, replace: bool, dry_run: bool) -> int:
    try:
        return create(path, replace, dry_run)
    except Stop as error:
        print(f"pubkit github-app: {error}")
        return 1
    except KeyboardInterrupt:
        print("\nStopped. Nothing more was written.")
        return 130
