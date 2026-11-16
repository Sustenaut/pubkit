"""Every test is a Gherkin scenario: the rule `pubkit sync --check` holds
every repository to that has code (every kind but content).

- **TEST-01:** the repository has a Gherkin suite: a `.feature` file with a
  scenario in it.
- **TEST-02:** no test runs outside a scenario. A Python test module's test
  functions are pytest-bdd's scenario bindings (`scenarios(...)`, or a
  function decorated with `@scenario` whose body is empty); there is no
  `unittest.TestCase`, no `Test*` class, no Node test file (`*.test.js`,
  `*.spec.ts`, or a script in a `test/` directory) and no test shell script
  (`test-*.sh`). Rust's `#[test]`s and doc tests are the Rust crate
  standard's, and stay.
- **TEST-03:** every feature file runs: a pytest-bdd binding names it, or the
  directory it is in, or the repository runs cucumber-js, which runs every
  feature file it is given.

A repository that cannot meet a rule yet declares an exception in
pubkit.json, as for the Rust crate standard's rules:
`{"rule": "TEST-02", "reason": "...", "until": "YYYY-MM-DD"}`.
"""

from __future__ import annotations

import ast
import configparser
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

RULES = {
    "TEST-01": "the repository has a Gherkin suite",
    "TEST-02": "no test runs outside a scenario",
    "TEST-03": "every feature file runs",
}
# Never the repository's own: tools, environments and what they fetched.
SKIPPED = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "target",
           "dist", "build", "coverage", ".terraform", "vendor"}
SCENARIO = re.compile(r"^\s*(Scenario|Scenario Outline|Scenario Template|Example):", re.M)
NODE_TEST = re.compile(r"\.(test|spec)\.(m|c)?(j|t)sx?$")
NODE_SOURCE = re.compile(r"\.(m|c)?(j|t)sx?$")
NODE_TEST_DIRS = {"test", "tests", "__tests__"}
SHELL_TEST = re.compile(r"^(test[-_].*|.*[-_]test)\.sh$")
# What a test module defines that pytest does not collect as a test.
NOT_TESTS = {"fixture", "given", "when", "then", "step"}
# A Node script in a test directory is a test when it loads a test runner;
# the rest there (fakes, setup, coverage helpers) serve the tests.
NODE_RUNNER = re.compile(r"""(from\s+|require\(\s*|import\(\s*)['"](node:test|vitest|@playwright/test|mocha|jest|ava|tap|uvu)['"/]""")
PYTHON_TEST = re.compile(r"^(test_.*|.*_test)\.py$")


def files(root: Path) -> list[PurePosixPath]:
    """The repository's files: what git tracks or would add, or, outside a
    git checkout, every file but the SKIPPED directories'."""
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root, capture_output=True, check=False)
    if listed.returncode == 0:
        paths = [PurePosixPath(p) for p in listed.stdout.decode("utf-8", "replace").split("\0") if p]
    else:
        paths = [PurePosixPath(p.relative_to(root).as_posix()) for p in root.rglob("*") if p.is_file()]
    return sorted(p for p in paths
                  if not SKIPPED.intersection(p.parts[:-1]) and (root / p).is_file())


def _decorator_name(node: ast.expr) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Attribute):
        return target.attr
    return target.id if isinstance(target, ast.Name) else ""


def _empty(body: list[ast.stmt]) -> bool:
    """A binding's body: pass, ..., or a docstring, nothing that tests."""
    return all(isinstance(s, ast.Pass) or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
               for s in body)


def _bound(call: ast.Call) -> list[str | None]:
    """The feature paths a scenarios()/scenario() call names; None for one
    that is not a literal string."""
    return [a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else None
            for a in call.args[: None if _decorator_name(call) == "scenarios" else 1]]


def python(root: Path, path: PurePosixPath) -> tuple[list[str], list[str | None]]:
    """(the tests outside a scenario in one Python file, the feature paths
    it binds)."""
    try:
        tree = ast.parse((root / path).read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, ValueError):
        return [], []
    outside: list[str] = []
    bound: list[str | None] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _decorator_name(node) in ("scenarios", "scenario"):
            bound += _bound(node)
        if isinstance(node, ast.ClassDef):
            bases = {_decorator_name(b) for b in node.bases}
            if bases & {"TestCase", "IsolatedAsyncioTestCase"}:
                outside.append(f"{path}:{node.lineno}: {node.name} is a unittest.TestCase")
    if not PYTHON_TEST.match(path.name):
        return outside, bound
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test") and not any(
                o.startswith(f"{path}:{node.lineno}:") for o in outside):
            outside.append(f"{path}:{node.lineno}: {node.name} is a test class")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            decorators = {_decorator_name(d) for d in node.decorator_list}
            if decorators & NOT_TESTS:
                continue
            binding = "scenario" in decorators
            if not binding:
                outside.append(f"{path}:{node.lineno}: {node.name} is a test function, not a scenario")
            elif not _empty(node.body):
                outside.append(f"{path}:{node.lineno}: {node.name} binds a scenario but tests in its "
                               f"body too; move that into a step")
    return outside, bound


def _features_base(root: Path, listed: list[PurePosixPath]) -> dict[PurePosixPath, PurePosixPath]:
    """Each pytest.ini's directory, and the bdd_features_base_dir it sets."""
    bases = {}
    for path in listed:
        if path.name != "pytest.ini":
            continue
        ini = configparser.ConfigParser()
        try:
            ini.read_string((root / path).read_text(encoding="utf-8"))
        except (configparser.Error, UnicodeDecodeError):
            continue
        base = ini.get("pytest", "bdd_features_base_dir", fallback=None)
        if base:
            bases[path.parent] = PurePosixPath(_normal(path.parent / base.strip()))
    return bases


def _normal(path: PurePosixPath) -> str:
    parts: list[str] = []
    for part in path.parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part != ".":
            parts.append(part)
    return "/".join(parts) or "."


def _base_for(path: PurePosixPath, bases: dict[PurePosixPath, PurePosixPath]) -> PurePosixPath:
    """The features base a test module's bindings resolve against: its
    nearest pytest.ini's, or else its own directory (pytest-bdd's default)."""
    for parent in [path.parent, *path.parent.parents]:
        if parent in bases:
            return bases[parent]
    return path.parent


def _cucumber(root: Path, listed: list[PurePosixPath]) -> bool:
    """The repository runs cucumber-js: a cucumber config, or it in package.json."""
    if any(p.parent == PurePosixPath(".") and p.name.startswith("cucumber.") for p in listed):
        return True
    package = root / "package.json"
    if not package.is_file():
        return False
    try:
        data = json.loads(package.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    return any("@cucumber/cucumber" in (data.get(k) or {}) for k in ("dependencies", "devDependencies"))


def checks(root: Path) -> dict[str, list[str]]:
    """Each rule's problems here."""
    listed = files(root)
    problems: dict[str, list[str]] = {rule: [] for rule in RULES}
    features = [p for p in listed if p.suffix == ".feature"]
    with_scenarios = [p for p in features
                      if SCENARIO.search((root / p).read_text(encoding="utf-8", errors="replace"))]
    if not with_scenarios:
        problems["TEST-01"].append(
            "there is no Gherkin suite: no .feature file with a scenario (tests/features/, "
            "bound by tests/steps/test_*.py with pytest-bdd's scenarios())")
    for path in features:
        if path not in with_scenarios:
            problems["TEST-01"].append(f"{path} has no scenario")
    bases = _features_base(root, listed)
    named: set[str] = set()
    unresolved = False
    for path in listed:
        if path.suffix == ".py":
            outside, bound = python(root, path)
            problems["TEST-02"] += outside
            base = _base_for(path, bases)
            for feature in bound:
                if feature is None:
                    unresolved = True
                else:
                    named.add(_normal(base / feature))
        elif NODE_TEST.search(path.name) or (
                NODE_SOURCE.search(path.name) and NODE_TEST_DIRS.intersection(path.parts[:-1])
                and NODE_RUNNER.search((root / path).read_text(encoding="utf-8", errors="replace"))):
            problems["TEST-02"].append(f"{path}: a Node test outside a scenario")
        elif path.suffix == ".sh" and SHELL_TEST.match(path.name):
            problems["TEST-02"].append(f"{path}: a test script outside a scenario")
    if not unresolved and not _cucumber(root, listed):
        for path in with_scenarios:
            text = str(path)
            if not any(text == n or n == "." or text.startswith(n.rstrip("/") + "/") for n in named):
                problems["TEST-03"].append(f"{path}: no scenarios() or @scenario names it, so it never runs")
    return {rule: texts for rule, texts in problems.items() if texts}


def applies(config: dict) -> bool:
    """Every kind but content, whose own tooling checks what it holds."""
    return config.get("kind") != "content"


def run(root: Path, config: dict) -> tuple[list[str], list[str]]:
    """(lines to print, problems): this repository against the rule, less the
    exceptions pubkit.json declares for it."""
    from .conform import exceptions  # noqa: PLC0415
    if not applies(config):
        return ["the Gherkin rule does not apply to a content repository"], []
    waived, open_, problems = exceptions(config, set(RULES), family=lambda r: str(r).startswith("TEST-"))
    for rule, texts in sorted(checks(root).items()):
        if rule not in waived:
            problems += [f"{rule}: {t}" for t in texts]
    lines = ["every test is a Gherkin scenario"] if not problems else []
    lines += [f"open exception: {e}" for e in open_]
    return lines, problems


def main(root: Path, config: dict) -> int:
    import sys  # noqa: PLC0415
    lines, problems = run(root, config)
    for problem in problems:
        print(f"pubkit: {problem}", file=sys.stderr)
    if lines:
        print("\n".join(lines))
    return 1 if problems else 0
