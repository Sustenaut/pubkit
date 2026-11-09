"""`pubkit`: keep a repository's shared testing files in step.

    pubkit init --kind=rust|utility|service [--bin=NAME]
    pubkit new --name=NAME --tag=PKG-XXXXXX-MM-YYYY [--owner=OWNER] [--description=TEXT]
    pubkit sync [--check]
    pubkit lock
    pubkit supply-chain [--check] CRATE...
    pubkit doc-coverage [--check] [CRATE...]
    pubkit required [--github-output]
    pubkit conform
    pubkit audit --check [PACKAGE VERSION] | --standard
    pubkit audit prepare --package NAME [--version V] [--out FILE] [--quick] [--force]
    pubkit audit draft --package NAME [--report FILE] [--model ID]
    pubkit bump
    pubkit pins --pubrel V [--no-lock]
    pubkit crates [--report FILE]
    pubkit record TEXT
    pubkit nightly --pubrel V --body FILE
    pubkit freshness [--json] (DIR... | --repos FILE [DIR])
    pubkit protect [--check] [--repos FILE | --require CHECK...] [REPO...]
    pubkit state --check [FILE]
    pubkit state --repos-for board|govern [FILE]
    pubkit board-publish --url URL --tenant TENANT [--file FILE]
    pubkit app-secrets [--check]
    pubkit variables --check|--apply [--state FILE] [--repos FILE | REPO...]
    pubkit secrets run [--cwd DIR] -- COMMAND [ARGS...]
    pubkit github-app create [--replace] [--dry-run] MANIFEST
    pubkit service-account-key create --account EMAIL --secret NAME [--bitwarden-project NAME] [--replace] [--dry-run]
    pubkit signing-key backup [--under DIR] [--dry-run]
    pubkit dns check ZONE [--record NAME/TYPE...]
    pubkit dns moves < STATE.json
    pubkit board --out DIR --repos FILE [--rules FILE]
    pubkit k8s preflight --namespace NS [--kubectl CMD] [--offline] < MANIFESTS
    pubkit k8s diagnose --namespace NS [--kubectl CMD] [--selector SEL]
    pubkit deploy walk [--fulfil] [--yes] [--unit NAME] [--kubectl CMD] [--server HOST] [--zones PATH] [--journal URL] [--json]
    pubkit deploy check [--offline] [--json] [--unit NAME] [--kubectl CMD] [--server HOST] [--zones PATH] [--namespace NS]
    pubkit deploy types

Files are either *managed* -- owned by pubkit, rewritten by `sync`, and
compared by `sync --check`, so they cannot drift -- or *seeded*: written once
by `init` and the repository's own from then on. Extensions go in seeded
files (the Makefile includes `pubkit.mk`; requirements.txt includes
requirements-pubkit.txt), so nothing managed is ever edited by hand. One
file is neither: .gitignore is the repository's own, but `init` and `sync`
add `.env` to it when it's missing (ignores.py), and `sync --check` fails
until they have.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from importlib import resources
from pathlib import Path

from . import __version__
from .plugin import bin_var

KINDS = ("rust", "utility", "service")
# Kinds pubkit no longer has, and what each became. Not accepted: a
# repository says which it is in one way only.
RENAMED_KINDS = {"python": "utility", "python-utility": "utility"}
MANAGED = {
    # The pinned pubkit, and a way to run exactly it.
    # The rules agents and people follow in every repository (AGENTS.md).
    # The one install of a hash-locked file, retried and mirrored.
    "common": ["tests/requirements-pubkit.txt", "pubkit", "AGENTS.md", "tests/install-lock"],
    "rust": ["rust-toolchain.toml", "pubkit.mk"],
    "utility": [],
    # A private repository that deploys: the one secrets wrapper every
    # service runs its secret-needing commands through.
    "service": ["with-secrets"],
}
# Managed files that are programs, written executable.
EXECUTABLE = {"with-secrets", "pubkit", "tests/install-lock"}
WITH_SECRETS = "with-secrets"
CONFIG = "pubkit.json"
LOCK = Path("tests/requirements.lock")
REQUIREMENTS = Path("tests/requirements.txt")


def templates(kind: str, layers: tuple[str, ...] | None = None) -> dict[str, str]:
    """Every template for `kind`, by repository path; later layers win."""
    out: dict[str, str] = {}
    for layer in layers or ("common", kind):
        root = resources.files("pubkit") / "templates" / layer
        if not root.is_dir():  # a kind with no templates of its own, such as utility
            continue
        stack = [(root, "")]
        while stack:
            node, prefix = stack.pop()
            for child in node.iterdir():
                path = f"{prefix}{child.name}"
                if child.is_dir():
                    stack.append((child, path + "/"))
                else:
                    out[path] = child.read_text(encoding="utf-8")
    return out


def render(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


def managed(kind: str) -> list[str]:
    return MANAGED["common"] + MANAGED[kind]


def read_config(root: Path) -> dict[str, str]:
    path = root / CONFIG
    if not path.exists():
        sys.exit(f"pubkit: no {CONFIG} here; run `pubkit init --kind={'|'.join(KINDS)}` first")
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("kind") in RENAMED_KINDS:
        sys.exit(f"pubkit: the kind \"{config['kind']}\" is now \"{RENAMED_KINDS[config['kind']]}\" "
                 f"(pubkit 0.10.0); change it in {CONFIG}")
    if config.get("kind") not in KINDS:
        sys.exit(f"pubkit: {CONFIG} must give a kind: one of {', '.join(KINDS)}")
    return config


# The launcher's wheels, each fetched once into the user's cache and checked
# against its sha256 on every run before uv is given the file: uv does not
# check a --with URL's #sha256. With a wheel mirror, a wheel GitHub fails to
# give is taken from the mirror, by its sha256; the check is the same.
FETCH = """# A wheel, fetched once into the user's cache, checked against its sha256 on every run.
fetch() {
  local dir="${XDG_CACHE_HOME:-$HOME/.cache}/pubkit/$3/$2" sum
  fetched="$dir/${1##*/}"
  if [ ! -f "$fetched" ]; then
    mkdir -p "$dir"
    curl -fsSL -o "$fetched.part" "$1"{fallback} \\
      || { rm -f "$fetched.part"; echo "pubkit: could not fetch $1" >&2; exit 1; }
    mv "$fetched.part" "$fetched"
  fi
  if command -v sha256sum >/dev/null 2>&1; then sum=$(sha256sum "$fetched"); else sum=$(shasum -a 256 "$fetched"); fi
  if [ "${sum%% *}" != "$2" ]; then
    rm -f "$fetched"
    echo "pubkit: $1 is not the wheel $4 pins (sha256 $2)" >&2
    exit 1
  fi
}
"""
FALLBACK = ' \\\n      || curl -fsSL -o "$fetched.part" "{mirror}/f/$2/${{1##*/}}"'
PROVIDER = """# The packages pubkit.json's deploy.providers names, each wheel checked against its sha256.
with=()
provider() {
  fetch "$1" "$2" providers deploy.providers
  with+=(--with "$fetched")
}
"""
# With a wheel mirror, pubkit's own wheel too, when tests/requirements.lock
# pins this version of it by its sha256.
OWN = """# pubkit's own wheel, by the sha256 tests/requirements.lock pins, from the mirror when GitHub fails.
from="https://github.com/Sustenaut/pubkit/releases/download/v{version}/pubkit-{version}-py3-none-any.whl"
pin=$(sed -n "\\#^pubkit @ $from#,/--hash=sha256:/s/.*--hash=sha256:\\([0-9a-f]\\{{64\\}}\\).*/\\1/p" tests/requirements.lock 2>/dev/null | head -n 1)
if [ -n "$pin" ]; then
  fetch "$from" "$pin" wheels tests/requirements.lock
  from="$fetched"
fi
"""


def providers(config: dict) -> list[dict[str, str]]:
    deploy = config.get("deploy")
    given = deploy.get("providers") if isinstance(deploy, dict) else None
    return [p for p in given or [] if isinstance(p, dict) and isinstance(p.get("wheel"), str)
            and isinstance(p.get("sha256"), str)]


MIRRORS = {
    # tests/install-lock and ./pubkit: <base>/f/<sha256>/<file>.whl.
    "wheels": "the base its files are at as <base>/f/<sha256>/<file>.whl",
    # The Terraform workflows' last init: a provider network mirror.
    "terraform": "a Terraform provider network mirror",
    # tests/install-lock's last try: a simple repository index at <base>/simple/.
    "pypi": "a simple repository index (PEP 503) at <base>/simple/",
}


def mirrors(config: dict) -> dict[str, str]:
    """pubkit.json's mirror block: where installs find what GitHub's release
    downloads (and the registries) fail to give, each checked against its
    pinned hash, so a mirror only has to be there, never trusted."""
    mirror = config.get("mirror")
    if mirror is None:
        return {}
    known = ", ".join(f"{k}: {v}" for k, v in MIRRORS.items())
    if not isinstance(mirror, dict) or set(mirror) - set(MIRRORS) or not all(
            isinstance(url, str) and re.fullmatch(r"https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~-]+)*", url)
            for url in mirror.values()):
        sys.exit(f'pubkit: {CONFIG}\'s mirror is {{"wheels": "https://host/path", "terraform": "https://host/path", "pypi": "https://host/path"}}, '
                 f"each an https base with no / at its end ({known})")
    return mirror


def wheel_mirror(config: dict) -> str:
    """mirror.wheels, or empty for none."""
    return mirrors(config).get("wheels", "")


def values(config: dict[str, str]) -> dict[str, str]:
    import shlex  # noqa: PLC0415
    from .new import PUBREL_PIN
    wheels = providers(config)
    mirror = wheel_mirror(config)
    fetch = FETCH.replace("{fallback}", FALLBACK.format(mirror=mirror) if mirror else "")
    launched = PROVIDER + "".join(f"provider {shlex.quote(p['wheel'])} {shlex.quote(p['sha256'])}\n"
                                  for p in wheels) + "\n" if wheels else ""
    own = OWN.format(version=__version__) if mirror else ""
    if own:
        launch = 'exec uvx --from "$from" '
    else:
        launch = f'exec uvx --from "https://github.com/Sustenaut/pubkit/releases/download/v{__version__}/pubkit-{__version__}-py3-none-any.whl" '
    return {"version": __version__, "bin_var": bin_var(config.get("bin", "app")),
            "pubrel": PUBREL_PIN, "providers": (fetch + "\n" if wheels or own else "") + launched + own,
            "launch": launch, "with": '"${with[@]}" ' if wheels else "", "mirror": mirror,
            "pypi": mirrors(config).get("pypi", "")}


def init_files(root: Path, config: dict[str, str], layers: tuple[str, ...] | None = None,
               extra: dict[str, str] | None = None, keep: set[str] | None = None) -> None:
    """Write pubkit.json and the templates of `layers`: managed files always,
    seeded ones unless already present."""
    kind = config["kind"]
    (root / CONFIG).write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    owned = set(managed(kind))
    fill = {**values(config), **(extra or {})}
    for path, text in sorted(templates(kind, layers).items()):
        target = root / path
        if target.exists() and path not in owned:
            print(f"kept     {path} (already present)")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(text, fill), encoding="utf-8")
        if (path.endswith(".py") and text.startswith("#!")) or path in EXECUTABLE:
            target.chmod(0o755)
        print(f"{'managed' if path in owned else 'seeded '}  {path}")
    from .security import DEPENDABOT_PATH, dependabot  # noqa: PLC0415
    from .ci import generated as workflows  # noqa: PLC0415
    written = workflows(config, root)
    if (generated := dependabot(config)) is not None:
        written.append((DEPENDABOT_PATH, generated))
    from .ci import review_licenses  # noqa: PLC0415
    if (licenses := review_licenses(config, root, written)) is not None:
        written.append(licenses)
    for path, text in written:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"managed  {path}")
    from .ignores import GITIGNORE, heal  # noqa: PLC0415
    if added := heal(root):
        print(f"ignored  {', '.join(added)} (in {GITIGNORE})")


def init(root: Path, kind: str, binary: str | None, project: str | None = None) -> int:
    config: dict = {"kind": kind}
    if binary:
        config["bin"] = binary
    if kind == "service" and not project:
        sys.exit("pubkit: a service names the Bitwarden Secrets Manager project its secrets are in: --project=NAME")
    # The CI is generated from the ci block (ci.py): a utility's is Node's
    # for a package.json without a pyproject.toml, Python's otherwise.
    config["ci"] = {}
    init_files(root, config, extra={"project": project or ""})
    print("next: add your requirements to tests/requirements.txt, then `pubkit lock`")
    return 0


def sync(root: Path, check: bool) -> int:
    config = read_config(root)
    from .hosting import settings  # noqa: PLC0415
    settings(config)
    if "cache" in config:
        from .cache import CacheError, check as check_cache  # noqa: PLC0415
        try:
            check_cache(config["cache"])
        except CacheError as e:
            sys.exit(f"pubkit: {e}")
    if "site" in config:
        from .ci import site  # noqa: PLC0415
        site(config, root)
    from .nightly import named_crates  # noqa: PLC0415
    named_crates(config)
    if "protect" in config:
        from .protect import entry  # noqa: PLC0415
        try:
            entry("", config, CONFIG)
        except ValueError as e:
            sys.exit(f"pubkit: {e}")
    if "signing_keys" in config:
        from .signing_keys import problems as key_problems  # noqa: PLC0415
        if bad := key_problems(config["signing_keys"]):
            sys.exit(f"pubkit: {CONFIG}: {'; '.join(bad)}")
    if "github_app" in config or "secrets" in config:
        from .app_secrets import config_problems  # noqa: PLC0415
        if bad := config_problems(config):
            sys.exit(f"pubkit: {CONFIG}: {'; '.join(bad)}")
    if "variables" in config:
        from .variables import block_problems  # noqa: PLC0415
        if bad := block_problems(config["variables"]):
            sys.exit(f"pubkit: {CONFIG}'s variables block: {'; '.join(bad)}")
    deploy_failed = False
    if "deploy" in config:
        # The block, and the offline walk: the manifests agree with the needs.
        from .deploy import offline_lines  # noqa: PLC0415
        lines, deploy_failed = offline_lines(root, config)
        for line in lines:
            print(line, file=sys.stderr)
        # The scheduled walk: seeded once, the repository's own from then on.
        from .deploy.scheduled import CALLER, caller  # noqa: PLC0415
        if not (root / CALLER).exists():
            if check:
                print(f"pubkit: no {CALLER}, the scheduled deploy walk: `pubkit sync` seeds it", file=sys.stderr)
            else:
                (root / CALLER).parent.mkdir(parents=True, exist_ok=True)
                (root / CALLER).write_text(caller(config), encoding="utf-8")
                print(f"seeded   {CALLER}")
    kind = config["kind"]
    every = templates(kind)
    stale = []
    from .security import DEPENDABOT_PATH, dependabot  # noqa: PLC0415
    from .ci import INTEGRATIONS, declared, generated as workflows, leftovers, review_licenses  # noqa: PLC0415
    owned = managed(kind)
    # Any repository with a with-secrets.json runs it through the managed with-secrets.
    if WITH_SECRETS not in owned and (root / f"{WITH_SECRETS}.json").is_file():
        owned = [*owned, WITH_SECRETS]
        every = {**every, WITH_SECRETS: templates("service", ("service",))[WITH_SECRETS]}
    wanted = [(path, render(every[path], values(config))) for path in owned]
    wanted += workflows(config, root)
    if (generated := dependabot(config)) is not None:
        wanted.append((DEPENDABOT_PATH, generated))
    if (integrations := declared(root, wanted)) is not None:
        wanted.append(integrations)
    if (licenses := review_licenses(config, root, wanted)) is not None:
        wanted.append(licenses)
    replaced = leftovers(config, root)
    from .ignores import GITIGNORE, heal, missing  # noqa: PLC0415
    unignored = missing(root) if check else heal(root)
    if unignored and not check:
        print(f"updated  {GITIGNORE} (now ignores {', '.join(unignored)})")
    for path, want in wanted:
        target = root / path
        have = target.read_text(encoding="utf-8") if target.exists() else None
        if have == want:
            continue
        stale.append(path)
        if not check:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(want, encoding="utf-8")
            if path in EXECUTABLE:
                target.chmod(0o755)
            print(f"updated  {path}")
    lock = root / LOCK
    wheel = f"/download/v{__version__}/pubkit-{__version__}-py3-none-any.whl"
    lock_stale = lock.exists() and wheel not in lock.read_text(encoding="utf-8")
    for path in replaced:
        print(f"pubkit: delete {path}: pubkit.json's ci block writes the workflows now",
              file=sys.stderr)
    if check:
        for path in stale:
            held = root / path
            if path == WITH_SECRETS and held.is_file() and "Managed by pubkit" not in held.read_text(
                    encoding="utf-8", errors="replace"):
                print(f"pubkit: {path} differs from pubkit {__version__}'s: it is an older script, which does not "
                      f"say which Bitwarden project it read (PUBKIT_SECRETS_PROJECT), so pubkit deploy finds every bitwarden.* need "
                      f"unknown through it; run `pubkit sync`, which writes the managed one", file=sys.stderr)
                continue
            if path == INTEGRATIONS:
                print(f"pubkit: {path} doesn't declare what the files pubkit writes use (the "
                      f"sonar job's token, or with-secrets.json for Bitwarden); run `pubkit sync`",
                      file=sys.stderr)
                continue
            print(f"pubkit: {path} differs from pubkit {__version__}'s; run `pubkit sync`",
                  file=sys.stderr)
        for name in unignored:
            print(f"pubkit: {GITIGNORE} doesn't ignore {name}, where local tools read secrets; "
                  f"run `pubkit sync`, which adds it", file=sys.stderr)
        if lock_stale:
            print(f"pubkit: {LOCK} does not pin pubkit {__version__}; run `pubkit lock`",
                  file=sys.stderr)
        from .refs import release_commit, stale_pins  # noqa: PLC0415
        actions, unversioned = stale_pins(root, __version__, release_commit())
        for problem in actions:
            print(f"pubkit: {problem}", file=sys.stderr)
        for use in unversioned:
            print(f"pubkit: {use} names no pubkit version, so `pubkit bump` leaves it as it is",
                  file=sys.stderr)
        if stale or lock_stale or replaced or actions or unignored or deploy_failed:
            return 1
        print(f"in step with pubkit {__version__}")
        if kind == "rust":
            if not (root / "Cargo.toml").exists():
                print("no Cargo.toml yet: the standard's checks wait for a crate")
                return 0
            return conform(root)
        return 0
    if not stale and not unignored:
        print(f"already in step with pubkit {__version__}")
    if lock_stale:
        print(f"now run `pubkit lock`: {LOCK} pins another pubkit")
    return 0


def sha256_of(url: str) -> str:
    digest = hashlib.sha256()
    with urllib.request.urlopen(url) as response:  # noqa: S310 - a URL the requirements name
        for chunk in iter(lambda: response.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


INDEX_JSON = "https://pypi.org/pypi"


def published_hashes(name: str, version: str) -> list[str]:
    """The sha256 of every file the index publishes for name==version.

    A package with compiled wheels has one per platform; pinning only the
    one this machine resolved would make the lock uninstallable everywhere
    else. PUBKIT_INDEX_JSON points at another index's JSON API.
    """
    base = os.environ.get("PUBKIT_INDEX_JSON", INDEX_JSON).rstrip("/")
    with urllib.request.urlopen(f"{base}/{name}/{version}/json") as response:  # noqa: S310
        files = json.load(response)["urls"]
    return sorted({f["digests"]["sha256"] for f in files})


def pip() -> list[str]:
    """How to run pip: the installed one, else the wheel Python's ensurepip
    carries (Debian keeps it in /usr/share/python-wheels), which runs as it
    is. `uvx` and `./pubkit` environments have no pip."""
    import importlib.util
    if importlib.util.find_spec("pip") is not None:
        return [sys.executable, "-m", "pip"]
    import ensurepip
    wheels = [wheel for where in (Path(ensurepip.__file__).parent / "_bundled", Path("/usr/share/python-wheels"))
              for wheel in sorted(where.glob("pip-*.whl"))]
    if not wheels:
        raise SystemExit("pubkit lock needs pip, and this Python has neither pip nor ensurepip's wheel of it")
    return [sys.executable, f"{wheels[0]}/pip"]


def lock(root: Path) -> int:
    """Resolve requirements.txt and pin every package, with its sha256s.

    pip's own resolver, through `--dry-run --report`, so locking needs
    nothing beyond pip; the index's JSON API then supplies the hash of every
    file of each pinned version, so the lock installs on any platform.
    """
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        subprocess.run(
            [*pip(), "install", "--dry-run", "--ignore-installed",
             "--quiet", "--report", str(report), "-r", str(root / REQUIREMENTS)],
            check=True, stdout=subprocess.DEVNULL,
        )
        items = json.loads(report.read_text(encoding="utf-8"))["install"]
    out = [f"# Generated by `pubkit lock` from {REQUIREMENTS.name}. Do not edit.",
           "# Installed with --require-hashes: a changed artifact fails the install.", ""]
    for item in sorted(items, key=lambda i: i["metadata"]["name"].lower()):
        name = item["metadata"]["name"]
        info = item["download_info"]
        digest = info.get("archive_info", {}).get("hashes", {}).get("sha256")
        if item.get("is_direct"):
            digest = digest or sha256_of(info["url"])
            out.append(f"{name} @ {info['url']} \\\n    --hash=sha256:{digest}")
        else:
            version = item["metadata"]["version"]
            digests = sorted(set(published_hashes(name, version)) | ({digest} if digest else set()))
            hashes = " \\\n    ".join(f"--hash=sha256:{d}" for d in digests)
            out.append(f"{name}=={version} \\\n    {hashes}")
    (root / LOCK).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"locked {len(items)} packages in {LOCK}")
    return 0


def conform(root: Path) -> int:
    """The standard's file and setting checks, and the declared exceptions."""
    from . import conform as checks
    config = read_config(root)
    if config["kind"] != "rust":
        sys.exit("pubkit: conform checks the Rust crate standard; this is not a Rust repository")
    lines, problems = checks.run(root, config)
    for problem in problems:
        print(f"pubkit: {problem}", file=sys.stderr)
    if problems:
        return 1
    print("\n".join(lines))
    return 0


def required(root: Path, github_output: bool, event: str = "", head_ref: str = "",
             changed_file: str | None = None) -> int:
    """The make targets each rust-ci.yml job must run here, from the standard.

    Given the event, the pull request's branch and the files it changes, a
    pull request that touches no code skips fuzz and cross, and says
    slow=false."""
    from . import conform as checks
    from . import standard
    config = read_config(root)
    if config["kind"] != "rust":
        sys.exit("pubkit: required checks come from the Rust crate standard; this is not a Rust repository")
    rules = standard.load()
    waived, _, _ = checks.exceptions(config, {r["id"] for r in rules["rules"]})
    plan = standard.required(rules, standard.has_library(root), frozenset(waived))
    changed = None
    if changed_file is not None:
        from .paths import confined
        listed = confined(changed_file, "the --changed-files file")
        changed = [line.strip() for line in listed.read_text(encoding="utf-8").splitlines()
                   if line.strip()]
    reason = standard.skip_slow(event, head_ref, changed)
    if reason:
        plan["targets"]["fuzz"] = []
        plan["jobs"] = [j for j in plan["jobs"] if j not in standard.SLOW_JOBS]
    lines = [f"{job}={' '.join(targets)}" for job, targets in plan["targets"].items()]
    lines += [f"{job}_job={'true' if job in plan['jobs'] else 'false'}"
              for job in sorted(standard.WORKFLOW_JOBS)]
    # Whether this change can change what the slow jobs test: the generated
    # CI's fuzz and cross jobs read it, and pass at once when it is false.
    lines.append(f"slow={'false' if reason else 'true'}")
    if reason:
        print(f"pubkit: {reason}", file=sys.stderr)
    if github_output:
        path = os.environ.get("GITHUB_OUTPUT")
        if not path:
            sys.exit("pubkit: --github-output needs GITHUB_OUTPUT set, as in a GitHub Actions step")
        with open(path, "a", encoding="utf-8") as out:
            out.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


# Commands that judge or write a repository by its pinned standard: run as
# any other pubkit, they would check it against rules it does not pin, or
# write files `sync --check` then rejects. Commands that move a repository
# to this pubkit (bump, pins, nightly), start one (init, new), or act on
# other repositories run as whichever pubkit is installed.
PINNED_COMMANDS = {"sync", "lock", "supply-chain", "doc-coverage", "required", "conform", "audit",
                   "record", "deploy"}


def wrong_pubkit(root: Path, command: str) -> str | None:
    """Why this pubkit must not run `command` here: the repository's lock pins
    another release. None when it pins this one or none, or with
    $PUBKIT_ANY_VERSION set."""
    if command not in PINNED_COMMANDS or os.environ.get("PUBKIT_ANY_VERSION"):
        return None
    from .freshness import pinned
    pin = pinned(root)
    if pin is None or pin == __version__:
        return None
    move = ("; `pubkit bump` moves the repository to this one"
            if _release(__version__) > _release(pin) else "")
    return (f"this repository pins pubkit {pin}, and this is pubkit {__version__}: run "
            f"`make venv` to install the pinned one, or `./pubkit {command} ...`, which runs it"
            f"{move} (PUBKIT_ANY_VERSION=1 runs this one anyway)")


def _release(version: str) -> tuple[int, ...]:
    """A release's numbers, for ordering: `0.15.2` is (0, 15, 2)."""
    return tuple(int(n) for n in version.split("+")[0].split("-")[0].split(".") if n.isdigit())


def main(argv: list[str] | None = None) -> int:
    given = sys.argv[1:] if argv is None else argv
    if given[:1] == ["cache"]:
        # Its own parser: `run` passes everything after -- to the command.
        from .cache import main as cache
        return cache(given[1:])
    if given[:1] == ["secrets"]:
        # The same: everything after -- is the command, with its own options.
        from .secrets import main as secrets
        return secrets(given[1:])
    if given[:1] == ["k8s"]:
        # Its own parser, in a file a server can run without pubkit.
        from .k8s import main as k8s
        return k8s(given[1:])
    parser = argparse.ArgumentParser(prog="pubkit", description=__doc__.split("\n")[0])
    parser.add_argument("--version", action="version", version=f"pubkit {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("cache", help="keep what a build fetched or made, by pubkit.json's cache rules "
                                      "(pubkit cache --help)")
    commands.add_parser("k8s", help="check the Secrets rendered manifests read before a deploy, or show "
                                    "why a rollout failed (pubkit k8s --help)")
    commands.add_parser("secrets", help="run one command with with-secrets.json's Bitwarden secrets in its "
                                        "environment: secrets run [--cwd DIR] -- COMMAND")
    p = commands.add_parser("init", help="write the shared files into this repository")
    p.add_argument("--kind", choices=KINDS, required=True)
    p.add_argument("--bin", help="the program under test, for <NAME>_BIN_DIR")
    p.add_argument("--project", help="service: the Bitwarden Secrets Manager project (name or id) with-secrets reads")
    p = commands.add_parser("new", help="start a new Rust package that meets the standard")
    p.add_argument("--name", required=True, help="the package's name, which is also its binary's")
    p.add_argument("--tag", required=True, help="its Section 9.1 tag, PKG-XXXXXX-MM-YYYY")
    p.add_argument("--owner", help="the GitHub owner of its repository; by default the owner "
                                   "of the origin remote")
    p.add_argument("--description", default="", help="one sentence on what it is for")
    p.add_argument("--no-key", action="store_true",
                   help="do not create the corpus workspace and release key (for tests)")
    p.add_argument("--no-lock", action="store_true",
                   help="do not lock the test requirements, which needs the network")
    p = commands.add_parser("sync", help="rewrite, or with --check verify, the managed files")
    p.add_argument("--check", action="store_true")
    commands.add_parser("lock", help="pin tests/requirements.txt with hashes")
    p = commands.add_parser("supply-chain",
                            help="write, or with --check verify, each crate's SUPPLY-CHAIN.md")
    p.add_argument("--check", action="store_true")
    p.add_argument("crates", nargs="+", metavar="CRATE")
    p = commands.add_parser("doc-coverage",
                            help="write, or with --check verify, each library's TESTING.md")
    p.add_argument("--check", action="store_true")
    p.add_argument("crates", nargs="*", metavar="CRATE",
                   help="the crates; by default every published library here")
    p = commands.add_parser("audit",
                            help="prepare, draft or check a release's quality audit (RS-REL-01)")
    p.add_argument("step", nargs="?", choices=("prepare", "draft"),
                   help="prepare: write the report with every rule a machine can decide; "
                        "draft: have Claude draft the rules left for review")
    p.add_argument("--package", help="for prepare and draft: the package being released")
    p.add_argument("--version", dest="release_version",
                   help="for prepare: the version being released; by default `pubrel next --package NAME`")
    p.add_argument("--out", help="for prepare: write here, not audit/PACKAGE-VERSION.md")
    p.add_argument("--quick", action="store_true",
                   help="for prepare: skip the docs.rs, MSRV and publish builds and the make targets")
    p.add_argument("--force", action="store_true",
                   help="for prepare: write over an existing report")
    p.add_argument("--report", help="for draft: the report to draft into; by default audit/PACKAGE-VERSION.md")
    p.add_argument("--model", help="for draft: the Claude model; by default $PUBKIT_AUDIT_MODEL, "
                                   "or claude-opus-5-5")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--check", nargs="*", metavar="PACKAGE VERSION",
                       help="pass only if audit/PACKAGE-VERSION.md is current, blocks nothing and has "
                            "nothing left to review; without them, the release pubrel prepare is "
                            "cutting ($PUBREL_PACKAGE, $PUBREL_VERSION)"),
    group.add_argument("--standard", action="store_true",
                       help="print the sha256 of the standard this pubkit ships, for a report's header")
    p = commands.add_parser("pins", help="move every outdated pub* pin to its latest release")
    p.add_argument("--pubrel", default="", help="pubrel's latest release, for its workflow pins")
    p.add_argument("--no-lock", action="store_true", help="do not relock (it needs the network)")
    p = commands.add_parser("crates",
                            help="update other crates, keeping only what cargo vet audits and cargo deny passes")
    p.add_argument("--report", type=Path, help="write what was updated and skipped, as JSON")
    p = commands.add_parser("record",
                            help="add an internal pubrel row for each package the last commit needs one for")
    p.add_argument("text", help="what changed")
    p = commands.add_parser("nightly",
                            help="commit the pins, then the audited crate updates, and write the PR body")
    p.add_argument("--pubrel", required=True)
    p.add_argument("--body", required=True, type=Path)
    commands.add_parser("bump",
                        help="move this repository to this pubkit: managed files, lock, workflow pins")
    p = commands.add_parser("freshness",
                            help="report which pinned pubkit and standard each repository meets")
    p.add_argument("--json", action="store_true", help="JSON rows instead of a Markdown table")
    p.add_argument("--repos", type=Path, metavar="FILE",
                   help="report the repositories this repos file governs, checked out in DIR")
    p.add_argument("dirs", nargs="*", metavar="DIR", type=Path)
    commands.add_parser("conform",
                        help="check the standard's required files and settings, and the exceptions")
    p = commands.add_parser("protect",
                            help="put pubkit's ruleset on the default branch of each repository")
    p.add_argument("--check", action="store_true",
                   help="change nothing: print how each differs, and fail when one does")
    p.add_argument("--repos", dest="repos_file", type=Path, metavar="FILE",
                   help="the repos file: protect those it marks, each by its own pubkit.json")
    p.add_argument("--require", action="append", metavar="CHECK",
                   help="a check the named repositories require, instead of their own "
                        "pubkit.json; repeat it")
    p.add_argument("--no-tags", dest="tags", action="store_false",
                   help="leave out the release tag ruleset, for repositories that release no tags")
    p.add_argument("--no-queue", dest="queue", action="store_false",
                   help="merge from a branch up to date with main instead of through the merge "
                        "queue, for repositories GitHub offers no merge queue (a user's, or a "
                        "private one)")
    p.add_argument("repos", nargs="*", metavar="REPO",
                   help="OWNER/NAME, read from its own pubkit.json on GitHub; by default "
                        "those the repos file marks, or this repository from ./pubkit.json")
    p = commands.add_parser("board", help="grade every listed repository A to E into "
                                          "board.json and a static index.html")
    p.add_argument("--out", type=Path, required=True, help="the directory to write them to")
    p.add_argument("--repos", type=Path, metavar="FILE", required=True,
                   help="the repos file: grade what it names")
    p.add_argument("--rules", type=Path, help="other rules than the ones pubkit ships")
    p.add_argument("--replay", type=Path,
                   help="read recorded responses from this file instead of the network")
    p.add_argument("--record", type=Path,
                   help="also keep every response in this file, for --replay")
    p = commands.add_parser("board-publish", help="keep a new board.json in storage, if a fact or "
                                                  "a grade changed, from a GitHub Actions job")
    p.add_argument("--url", required=True, help="the storage service's address")
    p.add_argument("--tenant", required=True, help="the storage tenant whose board head it moves")
    p.add_argument("--file", type=Path, default=Path("site/board.json"), metavar="FILE",
                   help="the board (default: site/board.json)")
    p = commands.add_parser("state", help="check a state repository's state.json, or make a "
                                          "repos file from it")
    what = p.add_mutually_exclusive_group(required=True)
    what.add_argument("--check", action="store_true",
                      help="check its shape, that each repository exists, and each protected one's block")
    what.add_argument("--repos-for", choices=("board", "govern"), metavar="ROLE",
                      help="print the repos file for a role, board or govern")
    p.add_argument("file", nargs="?", type=Path, default=Path("state.json"), metavar="FILE")
    p = commands.add_parser("app-secrets", help="send the secrets this repository's pubkit.json "
                                                "names to it, through ./with-secrets")
    p.add_argument("--check", action="store_true", help="check the values, and send nothing")
    p = commands.add_parser("variables", help="keep repositories' GitHub variables to the values "
                                              "a state file gives them")
    what = p.add_mutually_exclusive_group(required=True)
    what.add_argument("--check", action="store_true",
                      help="change nothing: print how each differs, and fail when one does")
    what.add_argument("--apply", action="store_true",
                      help="set those that differ, then run each changed repository's deploy workflow")
    p.add_argument("--state", type=Path, default=Path("state.json"), metavar="FILE",
                   help="the state file (default: state.json)")
    p.add_argument("--repos", dest="repos_file", type=Path, metavar="FILE",
                   help="the repos file: act on those it lists that the state gives variables")
    p.add_argument("repos", nargs="*", metavar="REPO",
                   help="NAME or OWNER/NAME; by default every repository the state gives variables")
    p = commands.add_parser("github-app", help="make a GitHub App from a manifest, and put its "
                                               "values in Bitwarden")
    github_app = p.add_subparsers(dest="github_app_command", required=True)
    p = github_app.add_parser("create", help="go through GitHub's app-manifest flow, in a browser, "
                                             "then write the app's id, key and installation id")
    p.add_argument("manifest", type=Path, metavar="MANIFEST",
                   help="the app's manifest, with a pubkit section naming its owner, "
                        "Bitwarden project and secret prefix")
    p.add_argument("--replace", action="store_true",
                   help="overwrite secrets that already have those names, instead of refusing")
    p.add_argument("--dry-run", action="store_true", help="show the plan, and do nothing")
    p = commands.add_parser("service-account-key", help="make a Google Cloud service account key, and put it "
                                                        "in Bitwarden, with no file or command line holding it")
    service_account_key = p.add_subparsers(dest="service_account_key_command", required=True)
    p = service_account_key.add_parser("create", help="make a new key beside the account's others, and write it "
                                                      "to a secret")
    p.add_argument("--account", required=True, metavar="EMAIL", help="the service account")
    p.add_argument("--secret", required=True, metavar="NAME", help="the secret to write")
    p.add_argument("--bitwarden-project", metavar="NAME",
                   help="the Bitwarden project; by default, the one with-secrets.json names")
    p.add_argument("--replace", action="store_true",
                   help="overwrite a secret that already has that name, instead of refusing")
    p.add_argument("--dry-run", action="store_true", help="show the plan, and do nothing")
    p = commands.add_parser("signing-key", help="back up the signing keys pubkit.json declares to Bitwarden")
    signing_key = p.add_subparsers(dest="signing_key_command", required=True)
    p = signing_key.add_parser("backup", help="write each declared key on this machine that its Bitwarden "
                                              "project lacks, never overwriting one")
    p.add_argument("--under", type=Path, metavar="DIR",
                   help="every repository directly under DIR, not this one")
    p.add_argument("--dry-run", action="store_true", help="say what would be written, and ask for nothing")
    p = commands.add_parser("dns", help="check a zone in pubkit.json's hosting block, live")
    dns = p.add_subparsers(dest="dns_command", required=True)
    p = dns.add_parser("check", help="every nameserver gives the same answers and, with dnssec, "
                                     "validates alone against the pinned keys")
    p.add_argument("zone", help="a zone in pubkit.json's hosting block, such as example.eu")
    p.add_argument("--record", action="append", default=[], metavar="NAME/TYPE",
                   help="another rrset to compare, such as www/A; repeat it")
    dns.add_parser("moves", help="moved blocks taking Cloudflare records to keys by value, "
                                 "from `terraform show -json` on stdin")
    p = commands.add_parser("site", help="check a public site's built pages")
    site = p.add_subparsers(dest="site_command", required=True)
    p = site.add_parser("check", help="every page names itself, has an Open Graph card with a "
                                      "large enough image, and robots.txt lets preview bots in")
    p.add_argument("dir", type=Path, help="the built site, such as _site")
    p.add_argument("--url", help="the site's https:// address, to find og:image files in DIR")
    p = commands.add_parser("deploy", help="check what pubkit.json's deploy block says the service needs "
                                           "before it deploys, and make what is missing")
    deploy = p.add_subparsers(dest="deploy_command", required=True)
    for verb, said in (("walk", "check every need, in order, print each one's state, and with --fulfil make "
                                "what is missing; exit 1 when one is not ready"),
                       ("check", "deploy walk without --fulfil: check every need, changing nothing")):
        p = deploy.add_parser(verb, help=said)
        if verb == "walk":
            p.add_argument("--fulfil", action="store_true",
                           help="after the checks, make each auto need that is missing, once the plan is confirmed")
            p.add_argument("--yes", action="store_true", help="confirm the plan without asking (no write token "
                                                              "is asked for without a terminal)")
        p.add_argument("--tolerate-unknown", action="store_true",
                       help="exit 0 unless a need is missing, drift or blocked: an unknown one fails nothing")
        p.add_argument("--unit", metavar="NAME", help="walk one unit of the deploy block; every unit by default")
        p.add_argument("--offline", action="store_true",
                       help="check only what needs no network: the manifests against the needs, and the files")
        p.add_argument("--json", action="store_true", help="print the walk as a deploy-journal/2 document")
        p.add_argument("--kubectl", metavar="CMD",
                       help="the kubectl command, such as 'k3s kubectl --kubeconfig FILE', or the server's "
                            "read-only verb, 'ssh -i KEY deploy@HOST {unit} kubectl-read'")
        p.add_argument("--server", metavar="HOST", help="the admin channel: kubectl and commands over ssh root@HOST "
                                                         "(default SUSTENAUT_SERVER, without --kubectl)")
        p.add_argument("--zones", type=Path, metavar="PATH",
                       help="the pubkit.json, here or in a checkout beside this one, whose hosting block "
                            "keeps the zone of the deploy's host")
        p.add_argument("--journal", metavar="URL", help="also write the journal to storage (or PUBKIT_JOURNAL_URL)")
        p.add_argument("--namespace", metavar="NS", help="another namespace than the unit's")
    deploy.add_parser("types", help="every need type pubkit and the installed packages offer, and its keys")
    p = commands.add_parser("required",
                            help="the make targets each rust-ci.yml job must run, from the standard")
    p.add_argument("--github-output", action="store_true",
                   help="also append them to $GITHUB_OUTPUT")
    p.add_argument("--event", default="",
                   help="the GitHub event: on a pull_request, fuzz and cross may be skipped")
    p.add_argument("--head-ref", default="",
                   help="the pull request's branch; a release/ branch runs everything")
    p.add_argument("--changed-files", metavar="FILE",
                   help="a file listing the paths the pull request changes, one per line")
    args = parser.parse_args(argv)
    root = Path.cwd()
    if (why := wrong_pubkit(root, args.command)) is not None:
        print(f"pubkit: {why}", file=sys.stderr)
        return 2
    if args.command == "init":
        return init(root, args.kind, args.bin, args.project)
    if args.command == "new":
        from .new import run as new
        return new(root, args.name, args.tag, args.description, owner=args.owner,
                   key=not args.no_key, lock=not args.no_lock)
    if args.command == "sync":
        return sync(root, args.check)
    if args.command == "supply-chain":
        from .supply_chain import run
        return run(root, args.crates, args.check)
    if args.command == "doc-coverage":
        from .doc_coverage import run as coverage
        return coverage(root, args.crates, args.check)
    if args.command == "required":
        return required(root, args.github_output, args.event, args.head_ref,
                        args.changed_files)
    if args.command == "conform":
        return conform(root)
    if args.command == "pins":
        from .nightly import pins
        for line in pins(root, args.pubrel, lock=not args.no_lock):
            print(line)
        return 0
    if args.command == "crates":
        from .nightly import crates
        return crates(root, args.report)
    if args.command == "record":
        from .nightly import record
        for package in record(root, args.text):
            print(f"recorded for {package}")
        return 0
    if args.command == "nightly":
        from .nightly import nightly
        return nightly(root, args.pubrel, args.body)
    if args.command == "bump":
        from .freshness import bump
        return bump(root)
    if args.command == "freshness":
        from .freshness import freshness
        from .paths import confined
        repos_file = confined(args.repos, "the --repos file") if args.repos else None
        return freshness(args.dirs, args.json, repos_file)
    if args.command == "protect":
        from .paths import confined
        from .protect import run as protect
        if args.repos_file and args.require:
            sys.exit("pubkit protect: --repos takes the place of --require")
        repos_file = confined(args.repos_file, "the --repos file") if args.repos_file else None
        return protect(args.repos, args.check, args.require, tags=args.tags,
                       queue=args.queue, repos_file=repos_file)
    if args.command == "app-secrets":
        from .app_secrets import run as app_secrets
        return app_secrets(read_config(root), args.check)
    if args.command == "variables":
        from .paths import confined
        from .variables import run as variables
        if args.repos_file and args.repos:
            sys.exit("pubkit variables: --repos takes the place of naming them")
        repos_file = confined(args.repos_file, "the --repos file") if args.repos_file else None
        return variables(confined(args.state, "the state file"), args.repos, args.apply, repos_file)
    if args.command == "github-app":
        from .github_app import run as github_app_run
        from .paths import confined
        return github_app_run(confined(args.manifest, "the manifest"), args.replace, args.dry_run)
    if args.command == "service-account-key":
        from .service_account_key import run as service_account_key_run
        return service_account_key_run(Path.cwd(), args.account, args.secret, args.bitwarden_project,
                                       args.replace, args.dry_run)
    if args.command == "state":
        from .paths import confined
        from .state import check, repos_for
        if args.repos_for:
            return repos_for(confined(args.file, "the state file"), args.repos_for)
        return check(confined(args.file, "the state file"))
    if args.command == "board-publish":
        from .board_publish import run as publish
        from .paths import confined
        return publish(args.url, args.tenant, confined(args.file, "the --file file"))
    if args.command == "board":
        from .board import run as board
        from .paths import confined
        files = {name: confined(path, f"the --{name} file") if path else None
                 for name, path in (("rules", args.rules), ("replay", args.replay),
                                    ("record", args.record), ("repos", args.repos))}
        return board(confined(args.out, "the --out directory"),
                     files["rules"], files["replay"], files["record"], files["repos"])
    if args.command == "signing-key":
        from .signing_keys import run as signing_key_backup
        return signing_key_backup(args.under.expanduser() if args.under else None, args.dry_run)
    if args.command == "deploy" and args.deploy_command == "types":
        from .deploy import run_types
        return run_types()
    if args.command == "deploy":
        from .deploy import run_walk
        from .paths import confined
        # --zones is another repository's pubkit.json, so it may be in a
        # checkout beside this one: a pubkit.json in the parent directory's tree.
        zones = confined(args.zones, "the --zones file", also=(root.parent,)) if args.zones else None
        if zones is not None and zones.name != "pubkit.json":
            sys.exit(f"pubkit: the --zones file ({args.zones}) must be a repository's pubkit.json")
        return run_walk(root, args.offline, args.json, args.kubectl, zones, args.namespace,
                        fulfil=getattr(args, "fulfil", False), unit=args.unit, server=args.server,
                        yes=getattr(args, "yes", False), journal_url=args.journal,
                        tolerate_unknown=args.tolerate_unknown)
    if args.command == "dns" and args.dns_command == "moves":
        from .dns import moves
        return moves(sys.stdin.read())
    if args.command == "dns":
        from .dns import check
        return check(root, args.zone, args.record)
    if args.command == "site":
        from .paths import confined
        from .site import run as site
        return site(root, confined(args.dir, "the site directory"), args.url)
    if args.command == "audit" and args.step:
        if not args.package:
            sys.exit(f"pubkit: audit {args.step} needs --package NAME")
        if args.check is not None or args.standard:
            sys.exit(f"pubkit: audit {args.step} takes neither --check nor --standard")
        if args.step == "prepare":
            from .audit_prepare import prepare
            return prepare(root, args.package, args.release_version, args.out, args.quick, args.force)
        from .audit_draft import draft
        draft(root, args.package, args.report, args.model)
        return 0
    if args.command == "audit":
        if args.check is None and not args.standard:
            sys.exit("pubkit: audit takes prepare, draft, --check or --standard")
        from .audit import run as audit
        return audit(root, args.check if args.check is not None else [], args.standard)
    return lock(root)


if __name__ == "__main__":
    sys.exit(main())
