"""`pubkit`: keep a repository's shared testing files in step.

    pubkit init --kind=rust|utility|service [--bin=NAME]
    pubkit new --name=NAME --tag=PKG-XXXXXX-MM-YYYY [--description=TEXT]
    pubkit sync [--check]
    pubkit lock
    pubkit supply-chain [--check] CRATE...
    pubkit doc-coverage [--check] [CRATE...]
    pubkit required [--github-output]
    pubkit conform
    pubkit audit --check [PACKAGE VERSION] | --standard
    pubkit bump
    pubkit pins --pubrel V --pub V [--no-lock]
    pubkit crates [--report FILE]
    pubkit record TEXT
    pubkit nightly --pubrel V --pub V --body FILE
    pubkit freshness [--json] DIR...
    pubkit protect [--check] [--config FILE | --require CHECK...] [REPO...]
    pubkit dns check ZONE [--record NAME/TYPE...]

Files are either *managed* -- owned by pubkit, rewritten by `sync`, and
compared by `sync --check`, so they cannot drift -- or *seeded*: written once
by `init` and the repository's own from then on. Extensions go in seeded
files (the Makefile includes `pubkit.mk`; requirements.txt includes
requirements-pubkit.txt), so nothing managed is ever edited by hand.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
    "common": ["tests/requirements-pubkit.txt"],
    "rust": ["rust-toolchain.toml", "pubkit.mk"],
    "utility": [],
    # A private repository that deploys: the one secrets wrapper every
    # service runs its secret-needing commands through.
    "service": ["with-secrets"],
}
# Managed files that are programs, written executable.
EXECUTABLE = {"with-secrets"}
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


def values(config: dict[str, str]) -> dict[str, str]:
    from .new import PUBREL_PIN
    return {"version": __version__, "bin_var": bin_var(config.get("bin", "app")),
            "pubrel": PUBREL_PIN}


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
    for path, text in written:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"managed  {path}")


def init(root: Path, kind: str, binary: str | None, project: str | None = None) -> int:
    config: dict = {"kind": kind}
    if binary:
        config["bin"] = binary
    if kind == "service" and not project:
        sys.exit("pubkit: a service names the Bitwarden Secrets Manager project its secrets are in: --project=NAME")
    # A utility's CI is generated from the ci block (ci.py): Node's for a
    # package.json without a pyproject.toml, Python's otherwise.
    if kind == "utility":
        config["ci"] = {}
    init_files(root, config, extra={"project": project or ""})
    print("next: add your requirements to tests/requirements.txt, then `pubkit lock`")
    return 0


def sync(root: Path, check: bool) -> int:
    config = read_config(root)
    from .hosting import settings  # noqa: PLC0415
    settings(config)
    kind = config["kind"]
    every = templates(kind)
    stale = []
    from .security import DEPENDABOT_PATH, dependabot  # noqa: PLC0415
    from .ci import generated as workflows, leftovers  # noqa: PLC0415
    wanted = [(path, render(every[path], values(config))) for path in managed(kind)]
    wanted += workflows(config, root)
    if (generated := dependabot(config)) is not None:
        wanted.append((DEPENDABOT_PATH, generated))
    replaced = leftovers(config, root)
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
            print(f"pubkit: {path} differs from pubkit {__version__}'s; run `pubkit sync`",
                  file=sys.stderr)
        if lock_stale:
            print(f"pubkit: {LOCK} does not pin pubkit {__version__}; run `pubkit lock`",
                  file=sys.stderr)
        if stale or lock_stale or replaced:
            return 1
        print(f"in step with pubkit {__version__}")
        if kind == "rust":
            if not (root / "Cargo.toml").exists():
                print("no Cargo.toml yet: the standard's checks wait for a crate")
                return 0
            return conform(root)
        return 0
    if not stale:
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


def lock(root: Path) -> int:
    """Resolve requirements.txt and pin every package, with its sha256s.

    pip's own resolver, through `--dry-run --report`, so locking needs
    nothing beyond pip; the index's JSON API then supplies the hash of every
    file of each pinned version, so the lock installs on any platform.
    """
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed",
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
    pull request that touches no code skips fuzz and cross."""
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pubkit", description=__doc__.split("\n")[0])
    parser.add_argument("--version", action="version", version=f"pubkit {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("init", help="write the shared files into this repository")
    p.add_argument("--kind", choices=KINDS, required=True)
    p.add_argument("--bin", help="the program under test, for <NAME>_BIN_DIR")
    p.add_argument("--project", help="service: the Bitwarden Secrets Manager project (name or id) with-secrets reads")
    p = commands.add_parser("new", help="start a new Rust package that meets the standard")
    p.add_argument("--name", required=True, help="the package's name, which is also its binary's")
    p.add_argument("--tag", required=True, help="its Section 9.1 tag, PKG-XXXXXX-MM-YYYY")
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
                            help="check a release's quality audit is current (RS-REL-01)")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", nargs="*", metavar="PACKAGE VERSION",
                       help="pass only if audit/PACKAGE-VERSION.md is current and blocks nothing; "
                            "without them, the release pubrel prepare is cutting "
                            "($PUBREL_PACKAGE, $PUBREL_VERSION)"),
    group.add_argument("--standard", action="store_true",
                       help="print the sha256 of the standard this pubkit ships, for a report's header")
    p = commands.add_parser("pins", help="move every outdated pub* pin to its latest release")
    p.add_argument("--pubrel", default="", help="pubrel's latest release, for its workflow pins")
    p.add_argument("--pub", default="", help="publet-cli's latest release, for pub-version")
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
    p.add_argument("--pub", required=True)
    p.add_argument("--body", required=True, type=Path)
    commands.add_parser("bump",
                        help="move this repository to this pubkit: managed files, lock, workflow pins")
    p = commands.add_parser("freshness",
                            help="report which pinned pubkit and standard each repository meets")
    p.add_argument("--json", action="store_true", help="JSON rows instead of a Markdown table")
    p.add_argument("dirs", nargs="+", metavar="DIR", type=Path)
    commands.add_parser("conform",
                        help="check the standard's required files and settings, and the exceptions")
    p = commands.add_parser("protect",
                            help="put pubkit's ruleset on the default branch of each repository")
    p.add_argument("--check", action="store_true",
                   help="change nothing: print how each differs, and fail when one does")
    p.add_argument("--config", type=Path,
                   help="the repositories and their checks (default: ./protected.json)")
    p.add_argument("--require", action="append", metavar="CHECK",
                   help="a check the named repositories require, instead of any list; repeat it")
    p.add_argument("repos", nargs="*", metavar="REPO",
                   help="OWNER/NAME; by default every repository the config lists")
    p = commands.add_parser("dns", help="check a zone in pubkit.json's hosting block, live")
    dns = p.add_subparsers(dest="dns_command", required=True)
    p = dns.add_parser("check", help="every nameserver gives the same answers and, with dnssec, "
                                     "validates alone against the pinned keys")
    p.add_argument("zone", help="a zone in pubkit.json's hosting block, such as example.eu")
    p.add_argument("--record", action="append", default=[], metavar="NAME/TYPE",
                   help="another rrset to compare, such as www/A; repeat it")
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
    if args.command == "init":
        return init(root, args.kind, args.bin, args.project)
    if args.command == "new":
        from .new import run as new
        return new(root, args.name, args.tag, args.description, key=not args.no_key,
                   lock=not args.no_lock)
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
        for line in pins(root, args.pubrel, args.pub, lock=not args.no_lock):
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
        return nightly(root, args.pubrel, args.pub, args.body)
    if args.command == "bump":
        from .freshness import bump
        return bump(root)
    if args.command == "freshness":
        from .freshness import freshness
        return freshness(args.dirs, args.json)
    if args.command == "protect":
        from .paths import confined
        from .protect import run as protect
        config = confined(args.config, "the --config file") if args.config else None
        return protect(config, args.repos, args.check, args.require)
    if args.command == "dns":
        from .dns import check
        return check(root, args.zone, args.record)
    if args.command == "audit":
        from .audit import run as audit
        return audit(root, args.check if args.check is not None else [], args.standard)
    return lock(root)


if __name__ == "__main__":
    sys.exit(main())
