"""Steps for arguments that could reach outside the repository."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from pubkit import new, nightly, paths, supply_chain

scenarios("arguments.feature")


@when(parsers.parse('the supply chain of the crate "{name}" is resolved'), target_fixture="stopped")
def resolve(repo, name):
    with pytest.raises(SystemExit) as stopped:
        supply_chain.dependencies(repo, {"packages": []}, name)
    return stopped.value


@then(parsers.parse('it stops with "{text}"'))
def stops_with(stopped, text):
    assert text in str(stopped.code), stopped.code


@given(parsers.parse('RUNNER_TEMP names the directory "{name}"'))
def runner_temp(repo, monkeypatch, name):
    (repo / name).mkdir()
    monkeypatch.setenv("RUNNER_TEMP", str(repo / name))


@when(parsers.parse('pubkit confines the file "{path}"'), target_fixture="confined")
def confine(repo, path):
    return paths.confined(repo / path, "the file")


@then("it is allowed")
def allowed(repo, confined):
    assert confined.is_relative_to(Path(os.environ["RUNNER_TEMP"]).resolve()), confined


@when(parsers.parse('pubkit records "{text}" as a row in the category "{category}"'),
      target_fixture="stopped")
def record_row(repo, text, category):
    with pytest.raises(SystemExit) as stopped:
        nightly.record(repo, text, category)
    return stopped.value


@when(parsers.parse('pubkit reads the version of "{tool}"'), target_fixture="version")
def read_version(tool):
    return new.version_of(tool)


@then("it is three numbers")
def three_numbers(version):
    assert version is not None and len(version) == 3, version


@then("it has no version")
def no_version(version):
    assert version is None, version


@given(parsers.parse('a pubrel that asks for a row for the package "{package}"'))
def fake_pubrel(repo, monkeypatch, package):
    bin_dir = repo / "bin"
    bin_dir.mkdir()
    script = bin_dir / "pubrel"
    script.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = check ]; then echo "run \\`pubrel add --package {package} CATEGORY"; exit 1; fi\n'
        f'echo "$@" >> "{repo}/pubrel-calls"\n')
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


@when(parsers.parse('pubkit records "{text}" as a row in the category "{category}" with it'),
      target_fixture="recorded")
def record_with_pubrel(repo, text, category):
    return nightly.record(repo, text, category)


@then(parsers.parse('pubrel add was run with "{args}"'))
def pubrel_add_ran(repo, recorded, args):
    assert recorded == [args.split()[1]], recorded
    assert (repo / "pubrel-calls").read_text().splitlines() == [f"add {args}"]
