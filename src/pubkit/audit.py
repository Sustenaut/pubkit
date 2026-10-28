"""`pubkit audit`: a release cites a current quality audit (RS-REL-01).

The rust-crate-quality agent writes its report on a package's release to
`audit/<package>-<version>.md`, starting with a header:

    ---
    package: pubunit
    version: 0.1.1
    standard: <sha256 of the Rust crate standard it audited against>
    blocking: 0
    date: 2026-10-13
    auditor: rust-crate-quality
    ---

`pubkit audit --check [PACKAGE VERSION]` passes only when that report exists,
names that package and version, was made against the standard this pubkit
ships, and counts no finding that blocks a release. Without arguments it
checks the release `pubrel prepare` is cutting, which pubrel gives every
security check as `PUBREL_PACKAGE` and `PUBREL_VERSION`. A repository
declares it in `release.json` as a security check, so `pubrel prepare` cuts
no release without one, and the release's signed security record cites the
report. `pubkit audit --standard` prints the standard's sha256, for the
header.

`pubkit audit prepare` writes a report with every machine-decidable rule
decided and each rule that needs judgement marked `[needs review]`;
`pubkit audit draft` marks what Claude drafts for those rules
`[machine-drafted by MODEL: needs human review]`. Either mark fails the
check, so no report passes until a person has judged every rule.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from . import standard

DIR = "audit"
FIELDS = ("package", "version", "standard", "blocking", "date", "auditor")
# What prepare and draft leave for a person: each a rule nobody has judged.
PENDING = re.compile(r"\[(?:needs review|machine-drafted[^\]]*)\]")


def current() -> str:
    """The sha256 of the standard this pubkit ships."""
    return standard.load()["sha256"]


def header(text: str) -> dict[str, str] | None:
    """The `---`-fenced header at the top of a report, or `None`."""
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return None
    fields = {}
    for line in m.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def check(root: Path, package: str, version: str) -> tuple[str | None, str]:
    """(problem or None, the line that describes the report)."""
    rel = f"{DIR}/{package}-{version}.md"
    path = root / rel
    if not path.is_file():
        return (f"no {rel}: run `pubkit audit prepare --package {package}`, which writes it "
                f"with every rule a machine can decide, then review the rules it marks (or have "
                f"the rust-crate-quality agent write it)"), rel
    text = path.read_text(encoding="utf-8")
    fields = header(text)
    if fields is None:
        return f"{rel} has no header (--- package, version, standard, blocking ... ---)", rel
    missing = [f for f in FIELDS if not fields.get(f)]
    if missing:
        return f"{rel}: its header gives no {', '.join(missing)}", rel
    if fields["package"] != package or fields["version"] != version:
        return (f"{rel} is a report on {fields['package']} {fields['version']}, "
                f"not {package} {version}"), rel
    if fields["standard"] != current():
        return (f"{rel} audited against another version of the standard "
                f"({fields['standard'][:12]}, not {current()[:12]}): audit again"), rel
    try:
        blocking = int(fields["blocking"])
    except ValueError:
        return f"{rel}: blocking is {fields['blocking']!r}, not a count", rel
    pending = PENDING.findall(text)
    if pending:
        return (f"{rel} has {len(pending)} rule(s) nobody has judged yet (marked {pending[0]}): "
                f"review each, record what you find, and remove its mark"), rel
    if blocking:
        return f"{rel} lists {blocking} finding(s) that block a release: resolve them and audit again", rel
    return None, (f"{rel}: {package} {version} audited on {fields['date']} by "
                  f"{fields['auditor']} against the current standard ({current()[:12]}); "
                  f"0 findings block a release")


def run(root: Path, args: list[str], show_standard: bool) -> int:
    if show_standard:
        print(current())
        return 0
    if not args:
        args = [os.environ.get("PUBREL_PACKAGE", ""), os.environ.get("PUBREL_VERSION", "")]
    if len(args) != 2 or not all(args):
        print("pubkit: audit --check takes PACKAGE VERSION, or runs as a security check of "
              "`pubrel prepare`, which sets PUBREL_PACKAGE and PUBREL_VERSION", file=sys.stderr)
        return 1
    problem, line = check(root, args[0], args[1])
    if problem:
        print(f"pubkit: {problem}", file=sys.stderr)
        return 1
    print(line)
    return 0
