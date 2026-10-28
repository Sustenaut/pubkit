"""`pubkit audit draft`: Claude drafts the rules `pubkit audit prepare` left
for review.

    pubkit audit draft --package NAME [--report FILE] [--model ID]

It sends the Claude API the package's diff since its last release tag, the
judged rules the report still marks `[needs review]` (each with the
standard's words and what the machine found for it), and the previous
release's audit report when there is one. It asks for structured JSON: per
rule a verdict, findings with file and line, and the reasoning. Each
drafted rule goes back into the report marked
`[machine-drafted by MODEL: needs human review]`, so `pubkit audit --check`
still refuses the report until a person has read every draft.

The key is `ANTHROPIC_API_KEY`, from the environment, so the command runs
through `with-secrets` (`with-secrets ./pubkit audit draft --package NAME`)
or with the key exported. The model is `--model`, else `PUBKIT_AUDIT_MODEL`,
else claude-opus-5-5; the API is `ANTHROPIC_BASE_URL`, else
https://api.anthropic.com. The HTTP call is `post`, which a test replaces.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Iterator

from . import __version__
from .audit import DIR, header
from .audit_prepare import Package, rule_text

DEFAULT_MODEL = "claude-opus-5-5"
API_VERSION = "2026-10-21"
# A declined request is re-run server side on the model Anthropic picks for
# the reason it was declined, rather than returned as a refusal.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 64000
# Beyond this the request would not fit the model's context with room to
# answer: about 200k tokens.
MAX_DIFF_CHARS = 800_000
# What the diff leaves out: generated, recorded or locked, never judged.
NOT_JUDGED = (":(exclude)*Cargo.lock", ":(exclude)*TESTING.md", ":(exclude)*SUPPLY-CHAIN.md",
              ":(exclude)*CHANGELOG.md", ":(exclude)corpus", ":(exclude)supply-chain",
              ":(exclude)audit", ":(exclude)fuzz/corpus")
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
RULE = re.compile(r"<!-- rule (\S+) review -->\n(.*?)<!-- /rule -->", re.S)
VERDICTS = ["met", "falls short", "blocks a release", "worth considering", "not applicable",
            "cannot tell from what was sent"]
SCHEMA = {
    "type": "object",
    "properties": {
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "verdict": {"type": "string", "enum": VERDICTS},
                    "rationale": {"type": "string"},
                    "findings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "severity": {"type": "string", "enum": ["blocks", "short", "consider"]},
                                "file": {"type": "string"},
                                "line": {"type": "integer"},
                                "text": {"type": "string"},
                            },
                            "required": ["severity", "file", "line", "text"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["id", "verdict", "rationale", "findings"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["rules"],
    "additionalProperties": False,
}
SYSTEM = """You audit a Rust crate against a written standard, for a release. You \
are given the rules of the standard that need judgement, each with what a \
machine already found for it, and the crate's changes since its last \
release (or all of it, when it has none). Judge each rule against what you \
are sent and nothing else.

For each rule, give a verdict, the findings behind it, and your reasoning in \
a few plain sentences. A finding names the file and the line (0 when no line \
is meant) and says what is there and what the standard asks; it "blocks" \
only for a false claim in the docs, a failing check or a public item with no \
example, falls "short" for a missing or imprecise section, and is worth \
"consider"ing otherwise. Never call a claim about another crate true unless \
what you were sent shows the check that was run: say it is unverified. When \
the diff does not show enough to judge, say "cannot tell from what was sent" \
and what a person should read. Do not report anything the standard lists as \
deliberately not adopted. A person reviews every verdict before the release."""

Post = Callable[[str, dict, bytes], Iterator[str]]


def post(url: str, headers: dict, body: bytes) -> Iterator[str]:
    """POST and yield the response's lines, as a server-sent event stream."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            for raw in response:
                yield raw.decode("utf-8").rstrip("\r\n")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            pass
        sys.exit(f"pubkit: the Claude API refused the request ({e.code}): {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"pubkit: cannot reach the Claude API at {url}: {e.reason}")


def stream(lines: Iterator[str]) -> tuple[str, str, str]:
    """(the model that answered, its text, why it stopped) from a Messages
    API event stream."""
    model, text, stop, event = "", [], "", ""
    for line in lines:
        if line.startswith("event:"):
            event = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        data = json.loads(line[5:].strip() or "{}")
        kind = data.get("type", event)
        if kind == "message_start":
            model = data.get("message", {}).get("model", "")
        elif kind == "content_block_delta" and data.get("delta", {}).get("type") == "text_delta":
            text.append(data["delta"]["text"])
        elif kind == "message_delta":
            stop = data.get("delta", {}).get("stop_reason") or stop
        elif kind == "error":
            sys.exit(f"pubkit: the Claude API stopped with an error: "
                     f"{data.get('error', {}).get('message', data)}")
    return model, "".join(text), stop


def diff(pkg: Package) -> str:
    """What changed in the package since its last release, or all of it."""
    base = pkg.last_tag or EMPTY_TREE
    paths = [pkg.rel(pkg.dir) if pkg.dir != pkg.root else "."]
    if pkg.dir != pkg.root:
        paths += [p for p in ("SECURITY.md", "README.md", "Cargo.toml", ".github/workflows", "fuzz")
                  if (pkg.root / p).exists()]
    proc = subprocess.run(["git", "diff", "--no-color", base, "--", *paths, *NOT_JUDGED],
                          cwd=pkg.root, capture_output=True, text=True, check=False)
    if proc.returncode:
        sys.exit(f"pubkit: git diff {base} failed: {proc.stderr.strip()}")
    return proc.stdout


def previous_report(pkg: Package) -> str:
    """The audit report of the release before this one, if it is here."""
    if not pkg.last_tag:
        return ""
    version = pkg.last_tag.removeprefix(pkg.tag_prefix)
    path = pkg.root / DIR / f"{pkg.name}-{version}.md"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def prompt(pkg: Package, pending: dict[str, str], rules: dict[str, dict], changes: str,
           earlier: str) -> str:
    kind = "a library" if pkg.library else "a command-line tool with no library target"
    parts = [f"The crate is {pkg.name}, {kind}, to be released as {pkg.version}. "
             + (f"Its last release is {pkg.last_tag}; the diff below is everything since then."
                if pkg.last_tag else "It has never been released; the diff below is all of it."),
             "", "<rules>"]
    for key, block in pending.items():
        rule = rules[key]
        found = block.split("What the machine found:", 1)
        parts += [f'<rule id="{key}" section="{rule["section"]}">', rule_text(rule)]
        if rule.get("question"):
            parts.append(f"To judge: {rule['question']}")
        if len(found) == 2:
            parts += ["What the machine found:", found[1].strip()]
        parts.append("</rule>")
    parts.append("</rules>")
    if earlier:
        parts += ["", "<previous_audit>", earlier, "</previous_audit>"]
    parts += ["", "<diff>", changes, "</diff>", "",
              "Give one entry per rule above, by its id."]
    return "\n".join(parts)


def merged(text: str, drafts: list[dict], model: str) -> tuple[str, int]:
    """The report with each drafted rule's block replaced by its draft, and
    the count of drafted findings that block."""
    by_id = {d["id"]: d for d in drafts}
    blocking = 0

    def replace(m: re.Match) -> str:
        nonlocal blocking
        key, block = m.group(1), m.group(2)
        d = by_id.get(key)
        if d is None:
            return m.group(0)
        title = block.splitlines()[0].replace(" **[needs review]**", "")
        lines = [f"<!-- rule {key} drafted -->", title, "",
                 f"**[machine-drafted by {model}: needs human review]** Verdict: {d['verdict']}.", "",
                 d["rationale"].strip()]
        if d["findings"]:
            lines.append("")
        for f in d["findings"]:
            where = f"{f['file']}:{f['line']}" if f["line"] else f["file"]
            lines.append(f"- **{f['severity']}**{' (`' + where + '`)' if where else ''}: {f['text'].strip()}")
            blocking += f["severity"] == "blocks"
        rest = "\n".join(block.splitlines()[1:]).strip()
        lines += ["", rest, "<!-- /rule -->"]
        return "\n".join(lines)

    return RULE.sub(replace, text), blocking


def draft(root: Path, name: str, report: str | None, model: str | None,
          send: Post | None = None) -> int:
    from . import standard
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        sys.exit("pubkit: audit draft calls the Claude API, which needs ANTHROPIC_API_KEY: run it "
                 "through with-secrets (with-secrets ./pubkit audit draft --package NAME), or "
                 "export the key")
    model = model or os.environ.get("PUBKIT_AUDIT_MODEL") or DEFAULT_MODEL
    path = Path(report) if report else None
    version = None
    if path and path.is_file():
        version = (header(path.read_text(encoding="utf-8")) or {}).get("version")
    pkg = Package(root, name, version)
    path = path or root / DIR / f"{pkg.name}-{pkg.version}.md"
    if not path.is_file():
        sys.exit(f"pubkit: no {pkg.rel(path)}: run `pubkit audit prepare --package {name}` first")
    text = path.read_text(encoding="utf-8")
    pending = {m.group(1): m.group(2) for m in RULE.finditer(text)}
    if not pending:
        print(f"{pkg.rel(path)} has no rule left marked for review; nothing to draft")
        return 0
    rules = {r["id"]: r for r in standard.load()["judged"]}
    unknown = [k for k in pending if k not in rules]
    if unknown:
        sys.exit(f"pubkit: {pkg.rel(path)} marks rules {', '.join(unknown)}, which this pubkit's "
                 f"standard does not have: prepare the report again")
    changes = diff(pkg)
    if len(changes) > MAX_DIFF_CHARS:
        sys.exit(f"pubkit: the diff since {pkg.last_tag or 'the start'} is {len(changes):,} characters, "
                 f"more than one request can judge ({MAX_DIFF_CHARS:,}); review those rules by hand")
    body = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "stream": True,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": prompt(pkg, pending, rules, changes, previous_report(pkg))}],
        "output_config": {"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
        "fallbacks": "default",
    }
    headers = {"content-type": "application/json", "x-api-key": key,
               "anthropic-version": API_VERSION, "anthropic-beta": FALLBACK_BETA,
               "user-agent": f"pubkit/{__version__}"}
    base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
    print(f"drafting {len(pending)} rule(s) of {pkg.name} {pkg.version} with {model} "
          f"({len(changes):,} characters of diff since {pkg.last_tag or 'the start'})", file=sys.stderr)
    served, answer, stop = stream((send or post)(f"{base}/v1/messages", headers, json.dumps(body).encode()))
    if stop == "refusal":
        sys.exit("pubkit: the model declined to draft this audit; review the rules by hand")
    if stop == "max_tokens":
        sys.exit("pubkit: the draft ran out of room before it finished; nothing was written")
    try:
        drafts = json.loads(answer)["rules"]
    except (ValueError, KeyError, TypeError):
        sys.exit("pubkit: the Claude API's answer is not the JSON asked for; nothing was written")
    drafts = [d for d in drafts if d.get("id") in pending]
    served = served or model
    text, blocking = merged(text, drafts, served)
    fields = header(text) or {}
    if blocking and fields.get("blocking", "").isdigit():
        text = re.sub(r"^blocking: \d+$", f"blocking: {int(fields['blocking']) + blocking}", text, count=1, flags=re.M)
    if fields.get("auditor"):
        text = re.sub(r"^auditor: .*$", f"auditor: {fields['auditor']}, drafted by {served}", text, count=1, flags=re.M)
    path.write_text(text, encoding="utf-8")
    missed = sorted(set(pending) - {d["id"] for d in drafts})
    print(f"drafted {len(drafts)} rule(s) into {pkg.rel(path)}, {blocking} drafted finding(s) block"
          + (f"; not drafted: {', '.join(missed)}" if missed else "")
          + ". Each is marked for human review; `pubkit audit --check` fails until a person has read them")
    return 0
