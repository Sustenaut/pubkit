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
    pubkit protect [--check] [--repos FILE | --require CHECK...] [REPO...]
    pubkit dns check ZONE [--record NAME/TYPE...]
    pubkit dns moves < STATE.json

The deploy walk and the k8s checks are pubdeploy's: ./pubdeploy, which
`pubkit sync` writes in a repository with a deploy block, runs them. What a
control plane runs across many repositories (the board, state files,
variables, freshness) is not pubkit's: pubkit's README says where it went.

Deprecated, each now ./pubsec with the same arguments, and removed in the
next minor: secrets run, app-secrets, github-app create,
service-account-key create, signing-key backup. Each prints the line that
says so and exits 2.

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
# The commands that hold a secret, pubsec's now: each prints where it went.
MOVED = ("app-secrets", "github-app", "service-account-key", "signing-key")
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
    "service": ["with-secrets", "pubsec"],
}
# Managed files that are programs, written executable.
EXECUTABLE = {"with-secrets", "pubkit", "pubsec", "pubdeploy", "tests/install-lock"}
WITH_SECRETS = "with-secrets"
# The pinned pubsec, which ./with-secrets runs and whose types the deploy walk loads.
PUBSEC = "pubsec"
# The pinned pubdeploy, the deploy walk, in a repository with a deploy block.
PUBDEPLOY = "pubdeploy"
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


def provider_requirements(wheels: list[dict[str, str]]) -> str:
    """deploy.providers' wheels, as requirements: `pubkit sync --check` runs in
    the tests' environment, and reads the need types these packages offer.
    `pubkit lock` pins each with its hash, which is the provider's sha256."""
    named = [p for p in wheels if isinstance(p.get("distribution"), str)]
    if not named:
        return ""
    return ("# deploy.providers' packages, whose need types `pubkit sync --check` reads:\n"
            + "".join(f"{p['distribution']} @ {p['wheel']}\n" for p in named))


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
    from .providers import PUBDEPLOY as DEPLOYS, PUBSEC as SECRETS, launched as floored  # noqa: PLC0415
    wheel = floored(config, SECRETS)
    walker = floored(config, DEPLOYS)
    others = [p for p in wheels if p.get("distribution") != DEPLOYS]
    return {"version": __version__, "bin_var": bin_var(config.get("bin", "app")),
            "pubrel": PUBREL_PIN, "providers": (fetch + "\n" if wheels or own else "") + launched + own,
            "launch": launch, "with": '"${with[@]}" ' if wheels else "", "mirror": mirror,
            "pypi": mirrors(config).get("pypi", ""),
            "provider_requirements": provider_requirements(wheels),
            "pubsec": fetch + f"\nfetch {shlex.quote(wheel['wheel'])} {shlex.quote(wheel['sha256'])} providers "
                              f"deploy.providers\n",
            "pubdeploy": fetch + "\n" + (PROVIDER + "".join(
                f"provider {shlex.quote(p['wheel'])} {shlex.quote(p['sha256'])}\n" for p in others) + "\n"
                if others else "with=()\n")
                + f"fetch {shlex.quote(walker['wheel'])} {shlex.quote(walker['sha256'])} providers deploy.providers\n"}


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
        from .secrets_config import signing_key_problems as key_problems  # noqa: PLC0415
        if bad := key_problems(config["signing_keys"]):
            sys.exit(f"pubkit: {CONFIG}: {'; '.join(bad)}")
    if "github_app" in config or "secrets" in config:
        from .secrets_config import config_problems  # noqa: PLC0415
        if bad := config_problems(config):
            sys.exit(f"pubkit: {CONFIG}: {'; '.join(bad)}")
    if "variables" in config:
        from .variables import block_problems  # noqa: PLC0415
        if bad := block_problems(config["variables"]):
            sys.exit(f"pubkit: {CONFIG}'s variables block: {'; '.join(bad)}")
    deploy_failed = False
    if "deploy" in config:
        # pubsec and pubdeploy, whose types and walk a deploy block needs, in
        # deploy.providers at this pubkit's floors or newer.
        from .providers import ensure  # noqa: PLC0415
        lines, deploy_failed = ensure(root, config, check)
        for line in lines:
            print(line, file=sys.stderr if check else sys.stdout)
        if lines and not check:
            config = read_config(root)
    # What the installed packages check and seed (pubdeploy's: the deploy
    # block's offline check, and the scheduled walk's caller).
    from .hooks import sync as sync_hooks  # noqa: PLC0415
    lines, hooks_failed = sync_hooks(root, config, check)
    deploy_failed = deploy_failed or hooks_failed
    for line in lines:
        print(line, file=sys.stderr if line.startswith(("pubkit:", "pubdeploy:")) else sys.stdout)
    kind = config["kind"]
    every = templates(kind)
    stale = []
    from .security import DEPENDABOT_PATH, dependabot  # noqa: PLC0415
    from .ci import INTEGRATIONS, declared, generated as workflows, leftovers, review_licenses  # noqa: PLC0415
    owned = managed(kind)
    # Any repository with a with-secrets.json runs it through the managed
    # with-secrets, and that, or a deploy block, through ./pubsec.
    if WITH_SECRETS not in owned and (root / f"{WITH_SECRETS}.json").is_file():
        owned = [*owned, WITH_SECRETS]
        every = {**every, WITH_SECRETS: templates("service", ("service",))[WITH_SECRETS]}
    if PUBSEC not in owned and (WITH_SECRETS in owned or "deploy" in config):
        owned = [*owned, PUBSEC]
        every = {**every, PUBSEC: templates("service", ("service",))[PUBSEC]}
    if "deploy" in config:
        owned = [*owned, PUBDEPLOY]
        every = {**every, PUBDEPLOY: templates("deploy", ("deploy",))[PUBDEPLOY]}
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
                      f"say which Bitwarden project it read (PUBKIT_SECRETS_PROJECT), so pubdeploy finds every bitwarden.* need "
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
        unreported = unreported_checks(root, config)
        if stale or lock_stale or replaced or actions or unignored or deploy_failed or unreported:
            return 1
        print(f"in step with pubkit {__version__}")
        if kind == "rust":
            if not (root / "Cargo.toml").exists():
                print("no Cargo.toml yet: the standard's checks wait for a crate")
                return 0
            return conform(root)
        return 0
    unreported = unreported_checks(root, config)
    if not stale and not unignored and not unreported:
        print(f"already in step with pubkit {__version__}")
    if lock_stale:
        print(f"now run `pubkit lock`: {LOCK} pins another pubkit")
    return 1 if unreported else 0


def unreported_checks(root: Path, config: dict) -> list[str]:
    """The checks pubkit.json's protect block requires that none of the
    repository's workflows, as sync leaves them, reports where the ruleset
    waits for them; each is printed."""
    if "protect" not in config:
        return []
    from .protect import entry, unreported  # noqa: PLC0415
    protected = entry("", config, CONFIG)
    missing = unreported(root, protected["checks"], protected["queue"])
    for problem in missing:
        print(f"pubkit: {CONFIG}'s protect.checks: {problem}", file=sys.stderr)
    return missing


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
                   "record"}


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
    if given[:1] in (["secrets"], *([c] for c in MOVED)):
        # Moved to pubsec: say where, whatever the arguments.
        print(f"pubkit: this is now ./pubsec {' '.join(given[1:] if given[0] == 'secrets' else given)}; "
              "run pubkit sync to get the launcher", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(prog="pubkit", description=__doc__.split("\n")[0])
    parser.add_argument("--version", action="version", version=f"pubkit {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("cache", help="keep what a build fetched or made, by pubkit.json's cache rules "
                                      "(pubkit cache --help)")
    for moved in ("secrets", *MOVED):
        commands.add_parser(moved, help="deprecated: now ./pubsec, with the same arguments")
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
    if args.command == "protect":
        from .paths import confined
        from .protect import run as protect
        if args.repos_file and args.require:
            sys.exit("pubkit protect: --repos takes the place of --require")
        repos_file = confined(args.repos_file, "the --repos file") if args.repos_file else None
        return protect(args.repos, args.check, args.require, tags=args.tags,
                       queue=args.queue, repos_file=repos_file)
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
