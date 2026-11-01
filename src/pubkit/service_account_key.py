"""`pubkit service-account-key create`: make a Google Cloud service account key
and put it in Bitwarden, without it ever being in a file, on a command line, or in
Terraform's state.

    pubkit service-account-key create --account NAME@PROJECT.iam.gserviceaccount.com \
        --secret SECRET-NAME [--bitwarden-project NAME] [--replace] [--dry-run]

In order, stopping at the first thing wrong, so a refusal comes before a key
exists: show the plan (where --dry-run ends), ask for a Bitwarden access token with
write access, and look for the secret in the project. Then, on a typed "yes",
`gcloud iam service-accounts keys create -` writes the key to this process's pipe,
and Bitwarden's SDK writes it, base64-encoded, to the project. If that write fails,
the new key is deleted from Google, as nobody could ever have it.

The token is only ever asked for, never read from a file or kept. It reaches bws,
which looks the names up, in its environment. A key this makes sits beside the
account's other keys, which stay until they are deleted by hand, so whatever holds
an old one can move over first.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
from pathlib import Path

from .bitwarden import WriteFailed, ensure, scrub, write
from .github_app import BWS_MISSING, BwsFailed, Stop, _listed, ask_hidden, bws

ACCOUNT = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]@(?P<project>[a-z][a-z0-9-]{4,28}[a-z0-9])\.iam\.gserviceaccount\.com")
SECRET = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
GCLOUD_MISSING = "gcloud is not installed: https://cloud.google.com/sdk/docs/install"


def bitwarden_project(root: Path, named: str | None) -> str:
    if named:
        return named
    config = root / "with-secrets.json"
    project = None
    if config.is_file():
        try:
            project = json.loads(config.read_text(encoding="utf-8")).get("project")
        except (ValueError, AttributeError):
            raise Stop("with-secrets.json is not a JSON object") from None
    if not project:
        raise Stop("no Bitwarden project: name one with --bitwarden-project, or run this where "
                   "with-secrets.json names one")
    return project


def find_project(token: str, name: str) -> tuple[str, str]:
    """The project's id and its organization's, which the SDK needs and can't look up."""
    projects = _listed(bws(token, "project", "list", "--output", "json", "--color", "no"))
    found = [p for p in projects if name in (p.get("id"), p.get("name"))]
    if len(found) != 1:
        sees = ", ".join(sorted(str(p.get("name")) for p in projects)) or "none"
        raise Stop(f"this token's machine account sees {len(found)} projects named {name}, "
                   f"not one. It sees: {sees}.")
    return found[0]["id"], found[0]["organizationId"]


def find_secret(token: str, project: str, project_name: str, name: str, replace: bool) -> str | None:
    """The secret to update, None to create it; stops if that isn't clear."""
    listed = _listed(bws(token, "secret", "list", project, "--output", "json", "--color", "no"))
    found = [secret["id"] for secret in listed if secret["key"] == name]
    if found and not replace:
        raise Stop(f"{name} is already in project {project_name}. No key was made. --replace overwrites it.")
    if len(found) > 1:
        raise Stop(f"{len(found)} secrets in project {project_name} are named {name}: "
                   "delete all but one first. No key was made.")
    return found[0] if found else None


def gcloud(account: str, project: str, *args: str) -> str:
    done = subprocess.run(["gcloud", "iam", "service-accounts", "keys", *args, f"--iam-account={account}",
                           f"--project={project}"], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise Stop(f"gcloud refused: {done.stderr.strip() or f'it exited {done.returncode}'}")
    return done.stdout


def make_key(account: str, project: str) -> tuple[str, str]:
    """The key, base64-encoded as Terraform and Google's API give it, and its id, which isn't secret."""
    printed = gcloud(account, project, "create", "-")
    try:
        key = json.loads(printed)
        return base64.b64encode(printed.encode()).decode(), key["private_key_id"]
    except (ValueError, KeyError, TypeError):
        # The new key can't be named to delete it, and what was printed may hold it.
        raise Stop("gcloud printed something other than a key, not shown here. "
                   f"Look for a new key on {account}, and delete it.") from None


def others(account: str, project: str, key_id: str) -> list[str]:
    listed = gcloud(account, project, "list", "--managed-by=user", "--format=value(name.basename())")
    return [line for line in listed.split() if line != key_id]


def create(root: Path, account: str, secret: str, named: str | None, replace: bool, dry_run: bool) -> int:
    match = ACCOUNT.fullmatch(account)
    if not match:
        raise Stop(f"{account} is not a service account's address: NAME@PROJECT.iam.gserviceaccount.com")
    if not SECRET.fullmatch(secret):
        raise Stop(f"{secret} is not a secret's name: letters, digits, - and _, starting with a letter")
    google = match["project"]
    name = bitwarden_project(root, named)
    print(f"Service account:   {account}")
    print(f"Bitwarden project: {name}")
    print(f"Secret:            {secret}{' (replacing it, if it is there)' if replace else ''}")
    if dry_run:
        print("--dry-run: no key was made and nothing was written.")
        return 0
    for tool, missing in (("bws", BWS_MISSING), ("gcloud", GCLOUD_MISSING)):
        if shutil.which(tool) is None:
            raise Stop(missing)
    if why := ensure():
        raise Stop(why)
    token = ask_hidden(f"Bitwarden access token with write access to {name} (hidden, and not kept): ")
    if not token:
        raise Stop("no access token, so no key was made")
    project, organization = find_project(token, name)
    existing = find_secret(token, project, name, secret, replace)
    print(f"To do: make a new key on {account}, and {'replace' if existing else 'create'} {secret}.", flush=True)
    try:
        answer = input('Type "yes" to do it: ')
    except EOFError:
        answer = ""
    if answer.strip() != "yes":
        print("No key was made and nothing was written.")
        return 1
    value, key_id = make_key(account, google)
    try:
        write(token, organization, project, secret, value, f"Key {key_id} of {account}", existing)
    except WriteFailed as error:
        print(f"Bitwarden refused the key: {error}")
        gcloud(account, google, "delete", key_id, "--quiet")
        print(f"Key {key_id} was deleted from Google, as nothing holds it. Nothing changed.")
        return 1
    print(f"Done: key {key_id} of {account} is {secret} in project {name}, base64-encoded.")
    if old := others(account, google, key_id):
        print(f"The account's other keys still work: {', '.join(old)}. Once whatever holds them has the "
              f"new one, delete each: gcloud iam service-accounts keys delete ID --iam-account={account}")
    return 0


def run(root: Path, account: str, secret: str, named: str | None, replace: bool, dry_run: bool) -> int:
    try:
        return create(root, account, secret, named, replace, dry_run)
    except (Stop, BwsFailed) as error:
        print(f"pubkit service-account-key: {scrub(str(error), ())}")
        return 1
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130
