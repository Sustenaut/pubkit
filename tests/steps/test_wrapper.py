"""Steps for the managed ./pubkit wrapper, against a fake uvx on PATH that
records its arguments and where it ran."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from pubkit import __version__

scenarios("wrapper.feature")

WHEEL = (f"https://github.com/schryer/pubkit/releases/download/v{__version__}/"
         f"pubkit-{__version__}-py3-none-any.whl")

FAKE_UVX = f"""#!{sys.executable}
import json, os, sys
json.dump({{"args": sys.argv[1:], "cwd": os.getcwd()}}, open(os.environ["FAKE_UVX"], "w"))
"""


@pytest.fixture
def uvx_log(tmp_path):
    return tmp_path / "uvx.json"


@given("uvx records how it was run")
def fake_uvx(tmp_path, env, uvx_log):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uvx").write_text(FAKE_UVX)
    (bin_dir / "uvx").chmod(0o755)
    env["FAKE_UVX"] = str(uvx_log)
    env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


@when(parsers.parse('I run "{command}" from the directory "{directory}"'), target_fixture="result")
def run_from(repo, run, env, command, directory):
    (repo / directory).mkdir(exist_ok=True)
    program, *args = command.split(" ")
    return run(program, *args, cwd=repo / directory, env=env)


@when(parsers.parse('I run "{command}" without uv'), target_fixture="result")
def run_without_uv(repo, run, tmp_path, command):
    empty = tmp_path / "empty"
    empty.mkdir()
    program, *args = command.split(" ")
    return run(shutil.which("bash"), program, *args, cwd=repo, env={"PATH": str(empty)})


@then(parsers.parse('"{path}" is executable'))
def executable(repo, path):
    assert (repo / path).stat().st_mode & stat.S_IXUSR


@then(parsers.parse('"{path}" runs this pubkit\'s release wheel'))
def pins(repo, path):
    assert f'uvx --from "{WHEEL}" pubkit "$@"' in (repo / path).read_text()


@then(parsers.parse('uvx ran pubkit from this pubkit\'s release wheel with "{args}"'))
def ran(uvx_log, args):
    assert json.loads(uvx_log.read_text())["args"] == ["--from", WHEEL, "pubkit", *args.split(" ")]


@then("uvx ran in the repository's root")
def in_root(repo, uvx_log):
    assert json.loads(uvx_log.read_text())["cwd"] == str(repo)
