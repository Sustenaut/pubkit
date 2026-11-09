"""Steps for the variables block's shape, which `pubkit sync --check` checks."""

from __future__ import annotations

import json

from pytest_bdd import given, parsers, scenarios, then

scenarios("variables.feature")


@given(parsers.re(r"its pubkit.json's variables block is \"(?P<text>.+)\"$"))
def variables_block(repo, text):
    path = repo / "pubkit.json"
    config = json.loads(path.read_text())
    path.write_text(json.dumps({**config, "variables": json.loads(text)}))


@then(parsers.parse('it warns "{text}"'))
def warns(result, text):
    assert text in result.stderr, result.stderr
