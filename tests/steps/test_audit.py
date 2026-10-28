"""Steps for `pubkit audit`, against reports written into a scratch
repository."""

from __future__ import annotations

from pytest_bdd import given, parsers, scenarios, then

from pubkit import audit

scenarios("audit.feature")


def report(repo, package, version, standard, blocking, header=True, body="Nothing blocks a release.\n"):
    path = repo / "audit" / f"{package}-{version}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    head = (f"---\npackage: {package}\nversion: {version}\nstandard: {standard}\n"
            f"blocking: {blocking}\ndate: 2026-10-13\nauditor: rust-crate-quality\n---\n")
    path.write_text((head if header else "") + "# Audit\n\n" + body)


@given(parsers.parse('an audit report on "{package}" "{version}" against the current standard with {blocking:d} blocking'))
def current_report(repo, package, version, blocking):
    report(repo, package, version, audit.current(), blocking)


@given(parsers.parse('an audit report on "{package}" "{version}" against the current standard with {blocking:d} blocking, and a rule marked "{mark}"'))
def marked_report(repo, package, version, blocking, mark):
    report(repo, package, version, audit.current(), blocking,
           body=f"## Needs review\n\n### §1.6 Related crates **{mark}**\n")


@given(parsers.parse('an audit report on "{package}" "{version}" against an older standard with {blocking:d} blocking'))
def old_report(repo, package, version, blocking):
    report(repo, package, version, "0" * 64, blocking)


@given(parsers.parse('an audit report on "{package}" "{version}" with no header'))
def bare_report(repo, package, version):
    report(repo, package, version, "", 0, header=False)


@given(parsers.parse('pubrel is cutting "{package}" "{version}"'))
def cutting(env, package, version):
    env["PUBREL_PACKAGE"] = package
    env["PUBREL_VERSION"] = version


@then("it prints the sha256 of the standard pubkit ships")
def prints_digest(result):
    out = result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout
    assert out.strip() == audit.current()
