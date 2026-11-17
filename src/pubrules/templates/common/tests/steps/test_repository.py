"""Steps for repository.feature, which `pubrules init` seeded."""

import json
from pathlib import Path

from pytest_bdd import given, scenarios, then

scenarios("repository.feature")


@given("the repository's pubrules.json", target_fixture="config")
def config():
    return json.loads((Path(__file__).parents[2] / "pubrules.json").read_text(encoding="utf-8"))


@then("it names the repository's kind")
def names_kind(config):
    assert config["kind"] in ("rust", "utility", "service", "content")
