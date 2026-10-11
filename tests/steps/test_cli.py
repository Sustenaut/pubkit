"""Steps for the `pubkit` command, run as a user would, in a scratch
repository, through the plugin's own `run` fixture."""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from pubkit import __version__

SOURCE = Path(__file__).resolve().parents[2]

scenarios("cli.feature")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    return root


def pubkit():
    """The `pubkit` installed beside the Python running this suite."""
    found = shutil.which("pubkit", path=str(Path(sys.executable).parent))
    assert found, "pubkit is not installed in this environment; run `make venv`"
    return found


@given("an empty repository")
def empty(repo):
    pass


@given(parsers.parse('a repository initialised as "{kind}"'))
def initialised(repo, run, kind):
    assert run(pubkit(), "init", f"--kind={kind}", cwd=repo).code == 0


@given(parsers.parse('the file "{path}" reads "{text}"'))
@when(parsers.parse('the file "{path}" reads "{text}"'))
def file_reads(repo, path, text):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text + "\n")


@pytest.fixture
def env():
    return {}


@given(parsers.parse('a package "{name}" {version} whose index lists files hashed "{a}" and "{b}"'))
def indexed(tmp_path, env, name, version, a, b):
    """A local wheel pip resolves (no network), and an index JSON API, on
    disk, that lists two more files for the same version."""
    links = tmp_path / "links"
    links.mkdir()
    wheel = links / f"{name}-{version}-py3-none-any.whl"
    info = f"{name}-{version}.dist-info"
    with zipfile.ZipFile(wheel, "w") as z:
        z.writestr(f"{info}/METADATA",
                   f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        z.writestr(f"{info}/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        z.writestr(f"{info}/RECORD", "")
    api = tmp_path / "index" / name / version
    api.mkdir(parents=True)
    (api / "json").write_text(json.dumps(
        {"urls": [{"digests": {"sha256": a}}, {"digests": {"sha256": b}}]}))
    env.update(PIP_NO_INDEX="1", PIP_FIND_LINKS=str(links),
               PUBKIT_INDEX_JSON=(tmp_path / "index").as_uri())
    env["resolved"] = hashlib.sha256(wheel.read_bytes()).hexdigest()


@when(parsers.re(r'I run pubkit (?P<args>(?:"[^"]*" ?)+)'), target_fixture="result")
def run_pubkit(repo, run, env, args):
    variables = {k: v for k, v in env.items() if k != "resolved"}
    return run(pubkit(), *args.strip().strip('"').split('" "'), cwd=repo, env=variables)


@then(parsers.parse("the repository has {paths}"))
def has(repo, paths):
    for path in paths.split(", "):
        assert (repo / path.strip('"')).is_file(), path


@then(parsers.parse('"{path}" contains "{text}"'))
def contains(repo, path, text):
    assert text in (repo / path).read_text()


@then(parsers.parse('"{path}" pins this pubkit\'s wheel'))
def pins_wheel(repo, path):
    assert f"/download/v{__version__}/pubkit-{__version__}-py3-none-any.whl" in (
        repo / path).read_text()


@then(parsers.parse('"{path}" pins "{pin}" with the hashes "{a}", "{b}" and the resolved file\'s'))
def pins_hashes(repo, env, path, pin, a, b):
    lock = (repo / path).read_text()
    assert pin in lock, lock
    for digest in (a, b, env["resolved"]):
        assert f"--hash=sha256:{digest}" in lock, lock




@when("pubkit's wheel is built", target_fixture="wheel")
def built_wheel(tmp_path):
    # Templates are package data, and a glob that misses one (a
    # dot-directory, say) passes every test run from source while the
    # released wheel lacks the file.
    # Built from a clean copy: setuptools reuses an egg-info's file list
    # from an earlier build, which can hide a file the globs no longer match.
    import subprocess
    copy = tmp_path / "source"
    shutil.copytree(SOURCE, copy, ignore=shutil.ignore_patterns(
        ".git", ".venv", "build", "dist", "*.egg-info", "__pycache__"))
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--quiet", "--no-deps",
                    str(copy), "-w", str(tmp_path)], check=True)
    return next(tmp_path.glob("pubkit-*.whl"))


@then("it holds every file under src/pubkit/templates")
def wheel_holds_templates(wheel):
    names = set(zipfile.ZipFile(wheel).namelist())
    src = SOURCE / "src"
    missing = [p.relative_to(src).as_posix() for p in (src / "pubkit" / "templates").rglob("*")
               if p.is_file() and p.relative_to(src).as_posix() not in names]
    assert not missing, f"the wheel lacks {missing}"


@given(parsers.parse('a crate "{name}" with no dependencies, vetted with audits imported from "{a}" and "{b}"'))
def vetted_crate(repo, name, a, b):
    """A crate with nothing to resolve, so cargo needs no network, and the
    files `cargo vet init` would write, importing two audit sets."""
    (repo / "Cargo.toml").write_text(
        f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n')
    (repo / "src").mkdir()
    (repo / "src" / "lib.rs").write_text("")
    (repo / "Cargo.lock").write_text(
        f'version = 4\n\n[[package]]\nname = "{name}"\nversion = "0.1.0"\n')
    chain = repo / "supply-chain"
    chain.mkdir()
    imports = "".join(f'\n[imports.{org}]\nurl = "https://example.org/{org}.toml"\n'
                      for org in (a, b))
    (chain / "config.toml").write_text('[cargo-vet]\nversion = "0.10"\n' + imports)
    (chain / "audits.toml").write_text("[audits]\n")
    (chain / "imports.lock").write_text("")


@given(parsers.re(r'pubkit (?P<args>(?:"[^"]*" ?)+) has run'))
def has_run(repo, run, args):
    argv = args.strip().strip('"').split('" "')
    assert run(pubkit(), *argv, cwd=repo).code == 0
