"""`pubkit new`: a new Rust package that meets the standard from its first
commit.

    pubkit new --name=pubfoo --tag=PKG-PUBFOO-10-2026 [--description=TEXT]

Run in an empty repository (a README or LICENSE already there is kept). It:

1. checks the toolchain first, refusing a `pubrel` or `pub` older than the
   releases the pipeline needs;
2. writes everything the standard requires: the managed and seeded files,
   a crate whose docs, `_documentation` module, test, test vectors, fuzz
   target and Gherkin scenarios pass every check, `deny.toml`,
   `supply-chain/` with the shared imports, `SECURITY.md`;
3. sets up the release side: `release.json` with its security checks (the
   quality audit among them), `corpus/corpus.json` and an empty
   `unreleased.json`, with `corpus/.publet` gitignored;
4. creates the corpus's workspace and its release key, labelled "<name>
   release key", names it in `release.json`, and writes its public object
   to `corpus/objects`, checked against its identifier;
5. generates TESTING.md, SUPPLY-CHAIN.md and the test vectors, and locks
   the test requirements;
6. prints the one GitHub step it does not take by itself, the branch
   ruleset, and the line that registers the package for pubkit's nightly
   workflow.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__, cbor

# The releases a new package's pipeline needs on this machine: pubrel 0.5.3
# gives security checks the release they check (the quality audit reads
# it), and pub 0.2.1 rotates keys.
MIN_PUBREL = (0, 5, 3)
MIN_PUB = (0, 2, 1)
# The pubrel release workflow a new package's CI calls.
PUBREL_PIN = "0.5.2"

NAME = re.compile(r"[a-z][a-z0-9-]*")
TAG = re.compile(r"PKG-[A-Z0-9]{6}-(0[1-9]|1[0-2])-\d{4}")
KEPT = {"README.md", "LICENSE"}
RENAMED = {"gitignore": ".gitignore", "fuzz/gitignore": "fuzz/.gitignore"}

UNRELEASED_COMMENT = (
    "Changes not yet released. One row per change: category changed or removed "
    "(a published interface changed: major), added (a feature: minor), or fixed or "
    "security (patch). `pubrel add` appends one; `pubrel summary` sets the release's "
    "summary; `pubrel prepare` moves them into the package publet."
)

RULESET = {
    "name": "main changes by pull request, after CI",
    "target": "branch",
    "enforcement": "active",
    "conditions": {"ref_name": {"exclude": [], "include": ["~DEFAULT_BRANCH"]}},
    "rules": [
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        {"type": "pull_request", "parameters": {
            "required_approving_review_count": 0, "dismiss_stale_reviews_on_push": False,
            "required_reviewers": [], "require_code_owner_review": False,
            "require_last_push_approval": False, "required_review_thread_resolution": False,
            "allowed_merge_methods": ["merge", "squash", "rebase"]}},
        {"type": "required_status_checks", "parameters": {
            "strict_required_status_checks_policy": False, "do_not_enforce_on_create": False,
            "required_status_checks": [
                {"context": "ci / passed", "integration_id": 15368},
                {"context": "release / release", "integration_id": 15368}]}},
    ],
    "bypass_actors": [],
}


def version_of(tool: str) -> tuple[int, ...] | None:
    """The version `tool --version` prints, or `None` when it is missing."""
    if shutil.which(tool) is None:
        return None
    out = subprocess.run([tool, "--version"], capture_output=True, text=True, check=False).stdout
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out.splitlines()[0] if out else "")
    return tuple(int(x) for x in m.groups()) if m else None


def check_tools() -> list[str]:
    problems = []
    for tool, least, install in (
        ("pubrel", MIN_PUBREL, "cargo install --locked pubrel"),
        ("pub", MIN_PUB, "cargo install --locked --git https://github.com/schryer/publet "
                         "--tag v{v} publet-cli"),
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


def run(root: Path, name: str, tag: str, description: str, *, key: bool = True,
        lock: bool = True) -> int:
    from .cli import CONFIG, init_files  # noqa: PLC0415 - avoids an import cycle

    root = root.resolve()
    if not NAME.fullmatch(name):
        sys.exit(f"pubkit: --name must be lower-case letters, digits and hyphens: {name}")
    if not TAG.fullmatch(tag):
        sys.exit(f"pubkit: --tag must be a Section 9.1 tag, PKG-XXXXXX-MM-YYYY: {tag}")
    for there in (CONFIG, "Cargo.toml", "release.json"):
        if (root / there).exists():
            sys.exit(f"pubkit: {there} already exists; `pubkit new` starts a package, "
                     f"`pubkit init` adds pubkit to one")
    problems = check_tools()
    if problems:
        for p in problems:
            print(f"pubkit: {p}", file=sys.stderr)
        return 1

    config = {"kind": "rust-package", "bin": name}
    extra = {"name": name, "crate": name.replace("-", "_"), "tag": tag,
             "description": description or f"{name}: a pub* package."}
    init_files(root, config, layers=("common", "rust-package", "new-rust"), extra=extra,
               keep=KEPT)
    for src, dst in RENAMED.items():
        if (root / src).exists():
            (root / src).rename(root / dst)
    (root / "fuzz" / "regressions" / "greet").mkdir(parents=True, exist_ok=True)
    (root / "fuzz" / "regressions" / "greet" / ".gitkeep").touch()

    release = release_json(name, tag)
    unreleased = root / "corpus" / "publets" / f"pkg.{name}" / "unreleased.json"
    unreleased.parent.mkdir(parents=True, exist_ok=True)
    unreleased.write_text(json.dumps({"changes": [], "comment": UNRELEASED_COMMENT},
                                     indent=2) + "\n")

    corpus = root / "corpus"
    if key:
        run_in(corpus, "pub", "init")
        out = run_in(corpus, "pub", "sign", "--generate-key", "--principal=human",
                     f"--label={name} release key")
        key_cid = next(line.split()[1] for line in out.splitlines() if line.startswith("key"))
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

    ruleset = json.dumps(RULESET)
    print(f"""
{name} is ready: every file the standard asks for, a release key, and a
crate that passes every check. Next:

  make venv && make check          # the whole suite, as CI runs it
  cargo vet                        # fetch the shared audits into supply-chain/
  git add -A && git commit -m "{name}: a new pub* package" && git push

Then protect main, as every pub* repository is (pull requests only, after
CI), with:

  gh api -X POST repos/schryer/{name}/rulesets --input - <<'RULESET'
{ruleset}
RULESET

And register it for pubkit's nightly workflow: add
{{"repo": "schryer/{name}"}} to governed.json in schryer/pubkit.""")
    return 0
