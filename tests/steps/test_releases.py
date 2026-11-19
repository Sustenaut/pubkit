"""Steps for the pins of other packages' releases (releases.py): `pubrules
new` and the nightly, run in this process so GitHub's answers are stand-ins.
The repository, the release state and the checks are conftest's and
pubtest's."""

from __future__ import annotations

from pytest_bdd import given, parsers, scenarios, then, when

from pubrules import cli, new, nightly

scenarios("releases.feature")


@given(parsers.parse('GitHub\'s latest release of "{slug}" is "{tag}"'))
def latest(monkeypatch, slug, tag):
    monkeypatch.setattr(nightly, "latest_release", lambda repo: tag if repo == slug else None)
    nightly.latest_tag.cache_clear()


@given(parsers.parse('GitHub has no release of "{slug}"'))
def no_release(monkeypatch, slug):
    monkeypatch.setattr(nightly, "latest_release", lambda repo: None)
    nightly.latest_tag.cache_clear()


@given(parsers.parse('each wheel fetched hashes to "{sha}"'))
def hashes_to(monkeypatch, sha):
    monkeypatch.setattr(cli, "sha256_of", lambda url: sha)


@given("a service repository")
def service(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert cli.main(["init", "--kind=service", "--project=example"]) == 0
    capsys.readouterr()


@when(parsers.parse('I start the package "{name}"'), target_fixture="result")
def start(pubtest_ctx, repo, monkeypatch, name):
    monkeypatch.setattr(new, "check_tools", lambda: [])
    pubtest_ctx.attempt(lambda: exits(new.run(repo, name, f"PKG-{name.upper():<6.6}-10-2026".replace(" ", "X"),
                                              "", owner="example-org", key=False, lock=False)))
    return pubtest_ctx.outcome


@when("the nightly moves the repository's pins", target_fixture="moves")
def moves(repo, monkeypatch):
    monkeypatch.chdir(repo)
    moved = nightly.pins(repo, "", lock=False)
    nightly.latest_tag.cache_clear()
    return moved


@then(parsers.parse('the moves include "{line}"'))
def moves_include(moves, line):
    assert line in moves, moves


def exits(code):
    """A command's return, as the program's exit: what pubtest reads."""
    raise SystemExit(code)
