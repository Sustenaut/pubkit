"""`pubkit secrets run`: run one command with the repository's secrets in its environment.

    pubkit secrets run [--cwd DIR] -- COMMAND [ARGS...]
    ./pubkit secrets run -- terraform plan

What the managed ./with-secrets does, as a pubkit command, so a repository can
run it from its pinned pubkit instead of keeping the script. The repository's
settings are its with-secrets.json, in the directory pubkit runs in (./pubkit
runs it in the repository's root). The command runs there too, or in --cwd.

The secrets are read from a Bitwarden Secrets Manager project with the bws
CLI, using a read-only machine account's access token: BWS_ACCESS_TOKEN if set,
else the line BWS_ACCESS_TOKEN=... in the repository's .env, which git must
ignore. They exist only in the command's process and what it starts: nothing is
written to disk, and the calling shell never holds them.

with-secrets.json's settings (project, terraform.dir, terraform.secrets
"declared" or "map" with terraform.names_from, and aliases) are in pubkit's
README, "Services", and the managed with-secrets' header. Every secret not sent
some other way is exported under its name in upper case, with "-" as "_".
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

CONFIG = "with-secrets.json"
USAGE = "usage: pubkit secrets run [--cwd DIR] -- COMMAND [ARGS...]"
NAME = re.compile(r"[A-Z][A-Z0-9_]*")
UPPER = str.maketrans("abcdefghijklmnopqrstuvwxyz-", "ABCDEFGHIJKLMNOPQRSTUVWXYZ_")
TOKEN_LINE = re.compile(r"(export\s+)?BWS_ACCESS_TOKEN\s*=\s*(?P<value>.*)")
VARIABLE = re.compile(r'variable "([a-z0-9_]+)".*')
LISTED = re.compile(r'\s*"([a-z0-9-]*)"\s*=\s*"[^"]*",*\s*')
BWS_MISSING = ("bws is not installed: bws from https://github.com/bitwarden/sdk-sm/releases "
               "(or cargo install bws --locked).")


class Stop(Exception):
    """Something that ends the run before the command, with what to tell the person running it."""


def _raw(value: object) -> str:
    """A JSON value as `jq -r` prints it, inside $(...), which drops trailing newlines:
    with-secrets has always exported values that way."""
    text = value if isinstance(value, str) else json.dumps(value)
    return text.rstrip("\n")


def settings(root: Path) -> tuple[str, str, str, str, dict[str, str]]:
    """The project, Terraform's directory, mode and names file, and the aliases, checked."""
    config = root / CONFIG
    if not config.is_file():
        raise Stop(f"No {config}: it names the Bitwarden project to read (see pubkit's README, "
                   "\"Services\").")
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except ValueError:
        raise Stop(f"{config} is not JSON.") from None
    if not isinstance(data, dict):
        raise Stop(f"{config} is not a JSON object.")
    terraform = data.get("terraform") or {}
    if not isinstance(terraform, dict):
        raise Stop(f"{config}: terraform is not an object.")
    project = _raw(data.get("project") or "")
    tf_dir = _raw(terraform.get("dir") or "")
    tf_mode = _raw(terraform.get("secrets") or "")
    names_from = _raw(terraform.get("names_from") or "")
    if not project:
        raise Stop(f"{config} names no project.")
    if tf_mode == "map":
        if not names_from or not (root / names_from).is_file():
            raise Stop(f'{config}: terraform.secrets "map" needs terraform.names_from, a file listing the names.')
    elif tf_mode not in ("", "declared"):
        raise Stop(f'{config}: terraform.secrets is "declared" or "map", not "{tf_mode}".')

    files = data.get("aliases") or []
    if not isinstance(files, list):
        raise Stop(f"{config}: aliases is a list of files.")
    aliases: dict[str, str] = {}
    for file in files:
        if not file:
            continue
        if not (root / file).is_file():
            raise Stop(f"{config}: the aliases file {file} does not exist.")
        try:
            more = json.loads((root / file).read_text(encoding="utf-8")).get("aliases") or {}
            if not isinstance(more, dict):
                raise TypeError
        except (ValueError, AttributeError, TypeError):
            raise Stop(f'{file} is not JSON of the form {{"aliases": {{"NAME": "secret"}}}}.') from None
        aliases.update({alias: _raw(source) for alias, source in more.items()})
    for alias in sorted(aliases):
        if not NAME.fullmatch(alias):
            raise Stop(f"alias '{alias}': not usable as an environment variable name (upper case, digits and _).")
    return project, tf_dir, tf_mode, names_from, aliases


def token(root: Path) -> str:
    """BWS_ACCESS_TOKEN, else its line in .env, with or without quotes or "export"."""
    found = os.environ.get("BWS_ACCESS_TOKEN", "")
    if not found and (root / ".env").is_file():
        for line in (root / ".env").read_text(encoding="utf-8").splitlines():
            if match := TOKEN_LINE.match(line):
                found = re.sub("[\"'\r]", "", match["value"])
                break
    if not found:
        raise Stop(f"No access token: set BWS_ACCESS_TOKEN, or put BWS_ACCESS_TOKEN=... in {root / '.env'}.")
    return found


def bws(env: dict[str, str], failed: str, *args: str) -> list[dict]:
    """What bws lists, as JSON. Its own messages go to stderr, as they are."""
    done = subprocess.run(["bws", *args, "--output", "json"], env=env, stdout=subprocess.PIPE, text=True,
                          check=False)
    if done.returncode != 0:
        raise Stop(failed)
    try:
        listed = json.loads(done.stdout)
    except ValueError:
        raise Stop(f"{failed} (it printed something other than JSON)") from None
    if not isinstance(listed, list):
        raise Stop(f"{failed} (it printed something other than a list)")
    return listed


def revision(secret: dict) -> int:
    """A secret's last change, in seconds since 1970, as jq's fromdate reads it."""
    when = re.sub(r"\.[0-9]+Z$", "Z", str(secret.get("revisionDate")))
    try:
        return int(dt.datetime.strptime(when, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC).timestamp())
    except ValueError:
        raise Stop(f"secret '{secret.get('key')}': bws gave a revision date pubkit can't read: {when}") from None


def environment(root: Path) -> dict[str, str]:
    """The command's environment: this one, the token, and the project's secrets."""
    if shutil.which("bws") is None:
        raise Stop(BWS_MISSING)
    project, tf_dir, tf_mode, names_from, aliases = settings(root)
    env = {**os.environ, "BWS_ACCESS_TOKEN": token(root)}

    # A token without access to a project lists no secrets rather than failing,
    # so check it can see the project first.
    projects = bws(env, "bws could not list projects with this access token.", "project", "list")
    project_id = next((p.get("id") for p in projects if project in (p.get("id"), p.get("name"))), None)
    if not project_id:
        sees = ", ".join(str(p.get("name")) for p in projects)
        raise Stop(f"This access token's machine account can't see the project {project}.\n"
                   f"It sees: {sees}.\n"
                   "Give the machine account read access to that project in Bitwarden Secrets Manager.")
    secrets = bws(env, f"bws could not list the secrets of project {project}.", "secret", "list", str(project_id))

    declared: set[str] = set()
    if tf_mode == "declared":
        for tf in sorted((root / (tf_dir or ".")).glob("*.tf")):
            declared.update(m[1] for line in tf.read_text(encoding="utf-8").splitlines()
                            if (m := VARIABLE.fullmatch(line)))

    names = []
    for secret in secrets:
        key, value = _raw(secret.get("key")), _raw(secret.get("value"))
        name = key.translate(UPPER)
        if not value or not NAME.fullmatch(name):
            print(f"skipping secret '{key}': empty, or not usable as an environment variable name", file=sys.stderr)
            continue
        if name.lower() in declared:
            name = f"TF_VAR_{name.lower()}"
        env[name] = value
        names.append(name)

    for alias in sorted(aliases):
        value = next((_raw(s.get("value")) for s in secrets if s.get("key") == aliases[alias]
                      and s.get("value") not in (None, False)), "")
        if not value:
            print(f"alias {alias}: no secret '{aliases[alias]}' in Bitwarden", file=sys.stderr)
            continue
        env[alias] = value
        names.append(alias)

    if tf_mode == "map":
        wanted = [m[1] for line in (root / names_from).read_text(encoding="utf-8").splitlines()
                  if (m := LISTED.fullmatch(line))]
        chosen = [s for s in secrets if s.get("key") in wanted and s.get("value") != ""]
        values = {s["key"]: s.get("value") for s in chosen}
        revisions = {s["key"]: revision(s) for s in chosen}
        env["TF_VAR_secret_values"] = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
        env["TF_VAR_secret_revisions"] = json.dumps(revisions, ensure_ascii=False, separators=(",", ":"))
        found = ", ".join(sorted(revisions))
        missing = ", ".join(name for name in wanted if name not in revisions)
        print(f"To Terraform as TF_VAR_secret_values: {found or 'none'}"
              + (f"; not in Bitwarden: {missing}" if missing else ""), file=sys.stderr)

    print(f"From Bitwarden: {' '.join(names) or 'none'}", file=sys.stderr)
    return env


def main(argv: list[str], root: Path | None = None) -> int:
    # Everything after -- is the command, given to it as it is.
    before, command = (argv[:argv.index("--")], argv[argv.index("--") + 1:]) if "--" in argv else (argv, [])
    cwd = None
    if before[:1] == ["run"] and len(before) == 3 and before[1] == "--cwd":
        cwd = before[2]
    elif before != ["run"] or not command:
        print(USAGE, file=sys.stderr)
        return 2
    root = root or Path.cwd()
    try:
        env = environment(root)
    except Stop as stop:
        print(stop, file=sys.stderr)
        return 1
    if cwd is not None:
        try:
            os.chdir(cwd)
        except OSError as error:
            print(f"pubkit secrets run: --cwd {cwd}: {error.strerror}", file=sys.stderr)
            return 1
    try:
        os.execvpe(command[0], command, env)
    except FileNotFoundError:
        print(f"pubkit secrets run: {command[0]}: not found", file=sys.stderr)
        return 127
    except PermissionError:
        print(f"pubkit secrets run: {command[0]}: permission denied", file=sys.stderr)
        return 126
