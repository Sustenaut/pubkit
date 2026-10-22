"""The pytest plugin every package's functional suite loads.

It provides what each suite otherwise writes for itself:

- `binary(name)`: the program under test, from `<NAME>_BIN_DIR`, so another
  implementation runs the suite by setting one variable;
- `run(...)`: run a command and capture what it did as a `Completed`;
- `ctx`: a per-scenario namespace whose `cleanup` callbacks run in reverse
  when the scenario ends, passed or failed -- behave's `context` and
  `after_scenario`, as one fixture;
- the steps every command-line suite needs (`it succeeds`, `it fails`, `the
  exit code is N`, `it prints "..."`, `stderr mentions "..."`, `stdout is
  empty`), reading the `result` fixture a `when` step sets;
- markers for every `@tag` the feature files carry, so `--strict-markers`
  catches a typo in a marker expression without each new tag being
  registered by hand;
- Hypothesis's examples per property test from `PUBKIT_FUZZ_RUNS`, which
  python-ci sets: a short run on every push and pull request, a long one on
  the nightly schedule (`security.fuzz.python` in pubkit.json).

A suite's own step with the same text takes precedence over these.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pytest_bdd import parsers, then

__all__ = ["Completed", "Context", "bin_var"]


@dataclass(frozen=True)
class Completed:
    """The observable result of running a command."""

    argv: list[str]
    code: int
    out: bytes
    err: bytes

    @property
    def stdout(self) -> str:
        return self.out.decode("utf-8", "replace")

    @property
    def stderr(self) -> str:
        return self.err.decode("utf-8", "replace")

    @property
    def lines(self) -> list[str]:
        return self.stdout.splitlines()

    def describe(self) -> str:
        return (
            f"$ {' '.join(self.argv)}\nexit {self.code}\n"
            f"stdout: {self.stdout!r}\nstderr: {self.stderr!r}"
        )


class Context(SimpleNamespace):
    """Per-scenario state, with cleanups run in reverse at teardown."""

    def __init__(self) -> None:
        super().__init__()
        self._cleanups: list[Callable[[], Any]] = []

    def cleanup(self, fn: Callable[[], Any]) -> Callable[[], Any]:
        """Run `fn` when the scenario ends; usable as a decorator."""
        self._cleanups.append(fn)
        return fn

    def close(self) -> None:
        errors: list[BaseException] = []
        while self._cleanups:
            fn = self._cleanups.pop()
            try:
                fn()
            except Exception as e:  # noqa: BLE001 - every cleanup must run
                errors.append(e)
        if errors:
            raise errors[0]


def bin_var(name: str) -> str:
    """The variable naming the directory that holds `name`: pub -> PUB_BIN_DIR."""
    return re.sub(r"[^A-Za-z0-9]", "_", name).upper() + "_BIN_DIR"


@pytest.fixture
def ctx() -> Any:
    context = Context()
    yield context
    context.close()


@pytest.fixture(scope="session")
def binary() -> Callable[..., Path]:
    """`binary(name, var=None)`: the path of a program under test.

    Resolved only from the environment (`var`, or `<NAME>_BIN_DIR`), never
    from PATH, so the suite cannot silently test whatever is installed.
    """

    def resolve(name: str, var: str | None = None) -> Path:
        var = var or bin_var(name)
        raw = os.environ.get(var)
        if not raw:
            pytest.fail(f"{var} is not set: it must name the directory holding {name}")
        path = Path(raw) / (name + (".exe" if sys.platform == "win32" else ""))
        if not path.exists():
            pytest.fail(f"{var}={raw} has no {name}")
        return path

    return resolve


@pytest.fixture
def run(tmp_path: Path) -> Callable[..., Completed]:
    """`run(program, *args, stdin=None, cwd=tmp_path, env=None)` -> Completed.

    `env` is merged over the current environment; a value of None removes
    that variable.
    """

    def runner(
        program: str | os.PathLike[str],
        *args: str | os.PathLike[str],
        stdin: bytes | str | None = None,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str | None] | None = None,
        timeout: float | None = 120,
    ) -> Completed:
        argv = [str(program), *(str(a) for a in args)]
        merged = dict(os.environ)
        for key, value in (env or {}).items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        if isinstance(stdin, str):
            stdin = stdin.encode()
        proc = subprocess.run(
            argv, input=stdin, capture_output=True, cwd=cwd or tmp_path,
            env=merged, timeout=timeout, check=False,
        )
        return Completed(argv, proc.returncode, proc.stdout, proc.stderr)

    return runner


def _features(config: pytest.Config) -> Path:
    base = config.inipath.parent if config.inipath else config.rootpath
    try:
        configured = config.getini("bdd_features_base_dir")
    except ValueError:
        configured = ""
    return base / configured if configured else base


def _hypothesis_runs() -> None:
    """Load a Hypothesis profile running PUBKIT_FUZZ_RUNS examples per
    property, with no deadline: on a shared CI runner timing is noise, and
    the long run is the one that finds the slow inputs worth a look."""
    raw = os.environ.get("PUBKIT_FUZZ_RUNS", "")
    if not raw:
        return
    if not raw.isdigit() or int(raw) < 1:
        raise pytest.UsageError(f"PUBKIT_FUZZ_RUNS must be a positive whole number, not {raw!r}")
    try:
        from hypothesis import settings  # noqa: PLC0415
    except ImportError:
        return
    settings.register_profile("pubkit", max_examples=int(raw), deadline=None, print_blob=True)
    settings.load_profile("pubkit")


def pytest_configure(config: pytest.Config) -> None:
    _hypothesis_runs()
    root = _features(config)
    tags: set[str] = set()
    if root.is_dir():
        for path in root.rglob("*.feature"):
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("@"):
                    tags.update(t[1:] for t in stripped.split() if t.startswith("@"))
    for tag in sorted(tags):
        config.addinivalue_line("markers", f"{tag}: a feature-file tag")


# --- the steps every command-line suite shares ------------------------------


def _text(value: Any, stream: str) -> str:
    raw = getattr(value, stream)
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw


def _code(result: Any) -> int:
    code = getattr(result, "code", None)
    return result.returncode if code is None else code


def _show(result: Any) -> str:
    if isinstance(result, Completed):
        return result.describe()
    return (f"exit {_code(result)}\nstdout: {_text(result, 'stdout')!r}\n"
            f"stderr: {_text(result, 'stderr')!r}")


@then("it succeeds")
def it_succeeds(result: Any) -> None:
    assert _code(result) == 0, _show(result)


@then("it fails")
def it_fails(result: Any) -> None:
    assert _code(result) != 0, _show(result)


@then(parsers.parse("the exit code is {code:d}"))
def exit_code_is(result: Any, code: int) -> None:
    assert _code(result) == code, _show(result)


@then(parsers.parse('it prints "{text}"'))
def it_prints(result: Any, text: str) -> None:
    assert text in _text(result, "stdout"), _show(result)


@then(parsers.parse('stderr mentions "{text}"'))
def stderr_mentions(result: Any, text: str) -> None:
    assert text in _text(result, "stderr"), _show(result)


@then("stdout is empty")
def stdout_is_empty(result: Any) -> None:
    assert _text(result, "stdout") == "", _show(result)
