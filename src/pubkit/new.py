"""`pubkit new`: a new Rust package that meets the standard from its first
commit.

    pubkit new --name=pubfoo --tag=PKG-PUBFOO-10-2026 [--owner=OWNER] [--description=TEXT]

Run in an empty repository (a README or LICENSE already there is kept). The
package's repository is OWNER's, by default the owner the `origin` remote
names; without either it stops. It:

1. checks the toolchain first, refusing a `pubrel` or `pub` older than the
   releases the pipeline needs;
2. writes everything the standard requires: the managed and seeded files,
   a crate whose docs, `_documentation` module, test, test vectors, fuzz
   target and Gherkin scenarios pass every check, `deny.toml`,
   `supply-chain/` with the shared imports, `SECURITY.md`;
3. sets up the release side: `release.json` with its security checks (the
   quality audit among them) and `corpus/corpus.json`, with
   `corpus/.publet` gitignored. `pubrel add` records each change in a
   file of its own under `corpus/publets/pkg.<name>/unreleased/`, so
   nothing is written there yet;
4. creates the corpus's workspace and its release key, labelled "<name>
   release key", names it in `release.json`, and writes its public object
   to `corpus/objects`, checked against its identifier;
5. generates TESTING.md, SUPPLY-CHAIN.md and the test vectors, and locks
   the test requirements;
6. prints the protect block its pubkit.json needs, and that the repository
   is then switched on in the release app.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__, cbor
from .refs import OWNER_NAME

# The releases a new package's pipeline needs on this machine: pubrel 0.6.0
# records each unreleased change in a file of its own (0.5.3 gave security
# checks the release they check, which the quality audit reads), and pub
# 0.2.1 rotates keys.
MIN_PUBREL = (0, 6, 0)
MIN_PUB = (0, 2, 1)
# Where pub comes from: publet-cli, at a tag of this repository.
PUB_GIT = "https://github.com/Sustenaut/publet"
# The pubrel release workflow a new package's CI calls, and the commit its
# tag names, which the workflow pins (refs.py).
PUBREL_PIN = "0.6.1"
PUBREL_COMMIT = "5395ddd816d9530b649b92ed0b8b3cfdfe87d7dc"

NAME = re.compile(r"[a-z][a-z0-9-]*")
TAG = re.compile(r"PKG-[A-Z0-9]{6}-(0[1-9]|1[0-2])-\d{4}")
# An object's identifier, as cbor.cid makes it: its sha-256, in base32.
CID = re.compile(r"pub:sha2-256:[a-z2-7]{52}")
KEPT = {"README.md", "LICENSE"}
RENAMED = {"gitignore": ".gitignore", "fuzz/gitignore": "fuzz/.gitignore"}

# The checks a new package's ruleset requires: its CI and its release job.
CHECKS = ["ci / passed", "release / release"]


VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


def version_of(tool: str) -> tuple[int, ...] | None:
    """The version `tool --version` prints, or `None` when it is missing."""
    if shutil.which(tool) is None:
        return None
    out = subprocess.run([tool, "--version"], capture_output=True, text=True, check=False).stdout
    # Word by word, each matched from its start: one unanchored search for
    # digits backtracks quadratically on a long run of them.
    for word in (out.splitlines()[0] if out else "").split():
        m = VERSION.match(word)
        if m:
            return tuple(int(x) for x in m.groups())
    return None


def check_tools() -> list[str]:
    problems = []
    for tool, least, install in (
        ("pubrel", MIN_PUBREL, "cargo install --locked pubrel"),
        ("pub", MIN_PUB, f"cargo install --locked --git {PUB_GIT} --tag v{{v}} publet-cli"),
        ("cargo", None, "https://rustup.rs"),
    ):
        found = version_of(tool)
        need = ".".join(map(str, least)) if least else ""
        if found is None:
            problems.append(f"{tool} is not installed: {install.format(v=need)}")
        elif least and found < least:
            have = ".".join(map(str, found))
            problems.append(f"{tool} {have} is older than {need}, which a new package's "
                            f"pipeline needs: {install.format(v=need)}"
                            + (f" --version {need}" if tool == "pubrel" else ""))
    return problems


def run_in(where: Path, *argv: str) -> str:
    out = subprocess.run(argv, cwd=where, capture_output=True, text=True, check=False)
    if out.returncode != 0:
        sys.exit(f"pubkit: `{' '.join(argv)}` failed in {where}:\n{out.stdout}{out.stderr}")
    return out.stdout


def export_object(corpus: Path, cid: str) -> None:
    """Write the object `cid` names from the workspace's store to objects/,
    re-encoded from `pub read --json` and checked against `cid`."""
    shown = json.loads(run_in(corpus, "pub", "read", cid, "--json"))["object"]
    data = cbor.encode(shown)
    if cbor.cid(data) != cid:
        sys.exit(f"pubkit: re-encoding {cid} gave {cbor.cid(data)}; not writing it")
    objects = corpus / "objects"
    objects.mkdir(exist_ok=True)
    (objects / f"{cid.replace(':', '_')}.cbor").write_bytes(data)


def release_json(name: str, tag: str) -> dict:
    return {
        "name": name,
        "package": f"pkg.{name}",
        "tag": tag,
        "corpus": "corpus",
        "manifest": {"kind": "cargo-package", "path": "Cargo.toml"},
        "code": ["src/", "Cargo.toml", "Cargo.lock"],
        "command": {"name": name, "check": f"{name} --version"},
        "install": "cargo install --locked " + name + " --version {version}",
        "semver-checks": True,
        "security": {
            "checks": [
                {"name": "advisories, licences, bans and sources",
                 "run": "cargo deny --locked check --hide-inclusion-graph",
                 "version": "cargo deny --version"},
                {"name": "supply-chain audits", "run": "cargo vet --locked",
                 "version": "cargo vet --version"},
                {"name": "supply-chain report current",
                 "run": f".venv/bin/pubkit supply-chain --check {name}",
                 "version": ".venv/bin/pubkit --version"},
                {"name": "quality audit current", "run": ".venv/bin/pubkit audit --check",
                 "version": ".venv/bin/pubkit --version"},
            ],
        },
    }


def remote_owner(root: Path) -> str | None:
    """The owner of the GitHub repository `origin` points at, if it does."""
    out = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"],
                         capture_output=True, text=True, check=False).stdout
    m = re.search(r"github\.com[:/]([A-Za-z0-9-]+)/", out)
    return m.group(1) if m else None


def run(root: Path, name: str, tag: str, description: str, *, owner: str | None = None,
        key: bool = True, lock: bool = True) -> int:
    from .cli import CONFIG, init_files  # noqa: PLC0415 - avoids an import cycle

    root = root.resolve()
    named = NAME.fullmatch(name)
    if not named:
        sys.exit(f"pubkit: --name must be lower-case letters, digits and hyphens: {name}")
    name = named.group(0)
    if not TAG.fullmatch(tag):
        sys.exit(f"pubkit: --tag must be a Section 9.1 tag, PKG-XXXXXX-MM-YYYY: {tag}")
    owner = owner or remote_owner(root)
    if not owner:
        sys.exit("pubkit: origin is not a GitHub repository; name the package's owner with "
                 "--owner=OWNER")
    if not OWNER_NAME.fullmatch(owner):
        sys.exit(f"pubkit: --owner must be a GitHub owner: {owner}")
    for there in (CONFIG, "Cargo.toml", "release.json"):
        if (root / there).exists():
            sys.exit(f"pubkit: {there} already exists; `pubkit new` starts a package, "
                     f"`pubkit init` adds pubkit to one")
    problems = check_tools()
    if problems:
        for p in problems:
            print(f"pubkit: {p}", file=sys.stderr)
        return 1

    config = {"kind": "rust", "bin": name, "ci": {}}
    extra = {"name": name, "owner": owner, "crate": name.replace("-", "_"), "tag": tag,
             "description": description or f"{name}: a pub* package."}
    init_files(root, config, layers=("common", "rust", "new-rust"), extra=extra,
               keep=KEPT)
    for src, dst in RENAMED.items():
        if (root / src).exists():
            (root / src).rename(root / dst)
    (root / "fuzz" / "regressions" / "greet").mkdir(parents=True, exist_ok=True)
    (root / "fuzz" / "regressions" / "greet" / ".gitkeep").touch()

    release = release_json(name, tag)
    corpus = root / "corpus"
    if key:
        run_in(corpus, "pub", "init")
        out = run_in(corpus, "pub", "sign", "--generate-key", "--principal=human",
                     f"--label={name} release key")
        printed = next(line.split()[1] for line in out.splitlines() if line.startswith("key"))
        cid = CID.fullmatch(printed)
        if not cid:
            sys.exit(f"pubkit: `pub sign` printed {printed!r} for the key, which is not an "
                     f"object's identifier; not using it")
        key_cid = cid.group(0)
        run_in(corpus, "pub", "policy", f"--root={key_cid}")
        export_object(corpus, key_cid)
        release["key"] = key_cid
        print(f"key      {key_cid} (\"{name} release key\", secret in corpus/.publet, gitignored)")
    (root / "release.json").write_text(json.dumps(release, indent=2) + "\n")

    run_in(root, sys.executable, "tools/vectors/generate.py")
    run_in(root, "cargo", "generate-lockfile")
    from .doc_coverage import run as coverage  # noqa: PLC0415
    from .supply_chain import run as supply_chain  # noqa: PLC0415
    coverage(root, [name], check=False)
    supply_chain(root, [name], check=False)
    if lock:
        from .cli import lock as lock_requirements  # noqa: PLC0415
        lock_requirements(root)

    block = json.dumps({"checks": CHECKS})
    print(f"""
{name} is ready: every file the standard asks for, a release key, and a
crate that passes every check. Next:

  make venv && make check          # the whole suite, as CI runs it
  cargo vet                        # fetch the shared audits into supply-chain/
  git add -A && git commit -m "{name}: a new pub* package" && git push

Then add a protect block to pubkit.json, which says how {owner}/{name} is
protected (in an organization, add its release app's id too, as
"release_app"):

  "protect": {block}

and switch the repository on in the release app.""")
    return 0
