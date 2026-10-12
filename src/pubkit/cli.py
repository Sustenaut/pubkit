"""`pubkit`: keep a repository's shared testing files in step.

    pubkit init --kind=rust|python [--bin=NAME]
    pubkit sync [--check]
    pubkit lock
    pubkit supply-chain [--check] CRATE...
    pubkit doc-coverage [--check] [CRATE...]
    pubkit required [--github-output]

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

KINDS = ("rust", "python")
MANAGED = {
    "common": ["tests/requirements-pubkit.txt"],
    "rust": ["rust-toolchain.toml", "pubkit.mk"],
    "python": [],
}
CONFIG = "pubkit.json"
LOCK = Path("tests/requirements.lock")
REQUIREMENTS = Path("tests/requirements.txt")


def templates(kind: str) -> dict[str, str]:
    """Every template for `kind`, by repository path; the kind's own win."""
    out: dict[str, str] = {}
    for layer in ("common", kind):
        root = resources.files("pubkit") / "templates" / layer
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
        sys.exit(f"pubkit: no {CONFIG} here; run `pubkit init --kind=rust|python` first")
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("kind") not in KINDS:
        sys.exit(f"pubkit: {CONFIG} must give a kind: one of {', '.join(KINDS)}")
    return config


def values(config: dict[str, str]) -> dict[str, str]:
    return {"version": __version__, "bin_var": bin_var(config.get("bin", "app"))}


def init(root: Path, kind: str, binary: str | None) -> int:
    config = {"kind": kind}
    if binary:
        config["bin"] = binary
    (root / CONFIG).write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    owned = set(managed(kind))
    for path, text in sorted(templates(kind).items()):
        target = root / path
        if target.exists() and path not in owned:
            print(f"kept     {path} (already present)")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(text, values(config)), encoding="utf-8")
        print(f"{'managed' if path in owned else 'seeded '}  {path}")
    print("next: add your requirements to tests/requirements.txt, then `pubkit lock`")
    return 0


def sync(root: Path, check: bool) -> int:
    config = read_config(root)
    kind = config["kind"]
    every = templates(kind)
    stale = []
    for path in managed(kind):
        want = render(every[path], values(config))
        target = root / path
        have = target.read_text(encoding="utf-8") if target.exists() else None
        if have == want:
            continue
        stale.append(path)
        if not check:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(want, encoding="utf-8")
            print(f"updated  {path}")
    lock = root / LOCK
    wheel = f"/download/v{__version__}/pubkit-{__version__}-py3-none-any.whl"
    lock_stale = lock.exists() and wheel not in lock.read_text(encoding="utf-8")
    if check:
        for path in stale:
            print(f"pubkit: {path} differs from pubkit {__version__}'s; run `pubkit sync`",
                  file=sys.stderr)
        if lock_stale:
            print(f"pubkit: {LOCK} does not pin pubkit {__version__}; run `pubkit lock`",
                  file=sys.stderr)
        if stale or lock_stale:
            return 1
        print(f"in step with pubkit {__version__}")
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


def required(root: Path, github_output: bool) -> int:
    """The make targets each rust-ci.yml job must run here, from the standard."""
    from . import standard
    config = read_config(root)
    if config["kind"] != "rust":
        sys.exit("pubkit: required checks come from the Rust crate standard; this is not a Rust repository")
    plan = standard.required(standard.load(), standard.has_library(root))
    lines = [f"{job}={' '.join(targets)}" for job, targets in plan["targets"].items()]
    lines += [f"{job}_job={'true' if job in plan['jobs'] else 'false'}"
              for job in sorted(standard.WORKFLOW_JOBS)]
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
    p = commands.add_parser("required",
                            help="the make targets each rust-ci.yml job must run, from the standard")
    p.add_argument("--github-output", action="store_true",
                   help="also append them to $GITHUB_OUTPUT")
    args = parser.parse_args(argv)
    root = Path.cwd()
    if args.command == "init":
        return init(root, args.kind, args.bin)
    if args.command == "sync":
        return sync(root, args.check)
    if args.command == "supply-chain":
        from .supply_chain import run
        return run(root, args.crates, args.check)
    if args.command == "doc-coverage":
        from .doc_coverage import run as coverage
        return coverage(root, args.crates, args.check)
    if args.command == "required":
        return required(root, args.github_output)
    return lock(root)


if __name__ == "__main__":
    sys.exit(main())
