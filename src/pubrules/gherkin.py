"""Every test is a Gherkin scenario: the rule `pubrules sync --check` holds
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
- **TEST-04:** every scenario has a When among its own steps: it sets
  something up, takes one action and checks what came of it (pubtest's
  GRAMMAR.md). A When in a Background does not count, since a Background
  builds the world. A step's `And` or `But` takes the keyword before it.

A repository that cannot meet a rule yet declares an exception in
pubrules.json, as for the Rust crate standard's rules:
`{"rule": "TEST-02", "reason": "...", "until": "YYYY-MM-DD"}`.

TEST-04 came after the repositories had suites, so the move to the pubrules
that brings it (`pubrules bump`, and the nightly's pins) writes its exception,
until 2026-12-31, into a repository that fails it (`grandfather`): no
repository's CI goes red on the bump, and one that passes, or moves later,
gets none.
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
    "TEST-04": "every scenario has a When",
}
# The release that brings TEST-04 (the one after 0.43.3, which the rename
# cut without it), and how long a repository that moved to it failing the
# rule has to meet it.
WHEN_FROM = "0.43.4"
WHEN_UNTIL = "2026-12-31"
# Never the repository's own: tools, environments and what they fetched.
SKIPPED = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "target",
           "dist", "build", "coverage", ".terraform", "vendor"}
SCENARIO = re.compile(r"^\s*(Scenario|Scenario Outline|Scenario Template|Example):", re.M)
NODE_TEST = re.compile(r"\.(test|spec)\.(m|c)?(j|t)sx?$")
NODE_SOURCE = re.compile(r"\.(m|c)?(j|t)sx?$")
NODE_TEST_DIRS = {"test", "tests", "__tests__"}
SHELL_TEST = re.compile(r"^(test[-_].*|.*[-_.]test)\.sh$")
# What a test module defines that pytest does not collect as a test.
NOT_TESTS = {"fixture", "given", "when", "then", "step"}
# A Node script in a test directory is a test when it loads a test runner;
# the rest there (fakes, setup, coverage helpers) serve the tests.
# The Gherkin runners for Node: a file that loads one defines steps or their
# support, whatever else it imports (node:test's mock, say), and each runs
# every feature file it is given.
GHERKIN_RUNNERS = ("@cucumber/cucumber", "quickpickle", "@amiceli/vitest-cucumber")
GHERKIN_RUNNER = re.compile(r"""(from\s+|require\(\s*|import\(\s*)['"](""" + "|".join(map(re.escape, GHERKIN_RUNNERS)) + r""")['"/]""")
NODE_RUNNER = re.compile(r"""(from\s+|require\(\s*|import\(\s*)['"](node:test|vitest|@playwright/test|mocha|jest|ava|tap|uvu)['"/]""")
PYTHON_TEST = re.compile(r"^(test_.*|.*_test)\.py$")
# A Gherkin file's lines that matter to TEST-04: the blocks, and the steps.
BLOCK = re.compile(r"^\s*(Feature|Rule|Background|Scenario Outline|Scenario Template|Scenario|Example|Examples|Scenarios):")
STEP = re.compile(r"^\s*(Given|When|Then|And|But|\*)\s")
DOCSTRING = re.compile(r'^\s*("""|```)')


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
    """The repository runs a Gherkin runner for Node: a cucumber config, or a
    package.json (the repository's, or a package's in it) that depends on one."""
    if any(p.name.startswith("cucumber.") for p in listed):
        return True
    for path in listed:
        if path.name != "package.json":
            continue
        try:
            data = json.loads((root / path).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if any(runner in (data.get(k) or {}) for k in ("dependencies", "devDependencies")
               for runner in GHERKIN_RUNNERS):
            return True
    return False


def whenless(text: str) -> list[tuple[int, str]]:
    """(line, title) of each scenario in a feature file's text that has no
    When of its own. An Outline is one scenario, whatever its Examples."""
    found: list[tuple[int, str]] = []
    scenario: tuple[int, str] | None = None
    has_when = False
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        if fence:
            if line.strip().startswith(fence):
                fence = None
            continue
        opened = DOCSTRING.match(line)
        if opened:
            fence = opened.group(1)
            continue
        block = BLOCK.match(line)
        if block:
            if block.group(1) in ("Examples", "Scenarios"):
                continue
            if scenario and not has_when:
                found.append(scenario)
            scenario = None
            if block.group(1) not in ("Feature", "Rule", "Background"):
                scenario, has_when = (number, line.strip()), False
            continue
        step = STEP.match(line)
        if step and scenario and step.group(1) == "When":
            has_when = True
    if scenario and not has_when:
        found.append(scenario)
    return found


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
        for line, title in whenless((root / path).read_text(encoding="utf-8", errors="replace")):
            problems["TEST-04"].append(f"{path}:{line}: {title} has no When: give it the one action "
                                       f"it tests (pubtest's GRAMMAR.md)")
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
        elif NODE_SOURCE.search(path.name) and (
                NODE_TEST.search(path.name) or NODE_TEST_DIRS.intersection(path.parts[:-1])):
            text = (root / path).read_text(encoding="utf-8", errors="replace")
            if GHERKIN_RUNNER.search(text):
                continue
            if NODE_TEST.search(path.name) or NODE_RUNNER.search(text):
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


def grandfather(root: Path, package: str | None, before: str | None) -> list[str]:
    """On the move from a pubrules without TEST-04 (one before WHEN_FROM, or
    any pubkit, as pubrules was called before 0.43.0), the TEST-04
    exception a repository that fails the rule needs, written into its
    pubrules.json; what was written, or nothing."""
    from .conform import today  # noqa: PLC0415
    try:
        older = bool(before) and (package == "pubkit" or _release(before) < _release(WHEN_FROM))
    except ValueError:
        older = False
    path = root / "pubrules.json"
    if not older or not path.is_file():
        return []
    config = json.loads(path.read_text(encoding="utf-8"))
    declared = config.get("exceptions", [])
    if (not applies(config) or any(isinstance(e, dict) and e.get("rule") == "TEST-04" for e in declared)
            or str(today()) > WHEN_UNTIL):
        return []
    missing = checks(root).get("TEST-04", [])
    if not missing:
        return []
    count = f"{len(missing)} scenario{'s' if len(missing) > 1 else ''}"
    config["exceptions"] = [*declared, {
        "rule": "TEST-04",
        "reason": f"{count} had no When on the move from {package or 'pubrules'} {before}, before the rule; "
                  f"give each its one action, on pubtest's steps",
        "until": WHEN_UNTIL}]
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return [f"pubrules.json: TEST-04 waived until {WHEN_UNTIL}, for the {count} with no When"]


def _release(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in str(version).split(".")[:3])


def run(root: Path, config: dict) -> tuple[list[str], list[str]]:
    """(lines to print, problems): this repository against the rule, less the
    exceptions pubrules.json declares for it."""
    from .conform import exceptions  # noqa: PLC0415
    if not applies(config):
        return ["the Gherkin rule does not apply to a content repository"], []
    waived, open_, problems = exceptions(config, set(RULES), family=lambda r: str(r).startswith("TEST-"))
    for rule, texts in sorted(checks(root).items()):
        if rule not in waived:
            problems += [f"{rule}: {t}" for t in texts]
    if problems:
        lines = []
    elif open_:
        lines = [f"every test is a Gherkin scenario, but for {len(open_)} open exception{'s' if len(open_) > 1 else ''}"]
    else:
        lines = ["every test is a Gherkin scenario"]
    lines += [f"open exception: {e}" for e in open_]
    return lines, problems


def main(root: Path, config: dict) -> int:
    import sys  # noqa: PLC0415
    lines, problems = run(root, config)
    for problem in problems:
        print(f"pubrules: {problem}", file=sys.stderr)
    if lines:
        print("\n".join(lines))
    return 1 if problems else 0
