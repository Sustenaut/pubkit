"""Steps for repository.feature, which `pubkit init` seeded."""

import json
from pathlib import Path

from pytest_bdd import given, scenarios, then

scenarios("repository.feature")


@given("the repository's pubkit.json", target_fixture="config")
def config():
    return json.loads((Path(__file__).parents[2] / "pubkit.json").read_text(encoding="utf-8"))


@then("it names the repository's kind")
def names_kind(config):
    assert config["kind"] in ("rust", "utility", "service", "content")
