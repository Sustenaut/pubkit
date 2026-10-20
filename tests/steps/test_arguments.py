"""Steps for arguments that could reach outside the repository."""

from __future__ import annotations

import pytest
from pytest_bdd import parsers, scenarios, then, when

from pubkit import supply_chain

scenarios("arguments.feature")


@when(parsers.parse('the supply chain of the crate "{name}" is resolved'), target_fixture="stopped")
def resolve(repo, name):
    with pytest.raises(SystemExit) as stopped:
        supply_chain.dependencies(repo, {"packages": []}, name)
    return stopped.value


@then(parsers.parse('it stops with "{text}"'))
def stops_with(stopped, text):
    assert text in str(stopped.code), stopped.code
