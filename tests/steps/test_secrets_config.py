"""Steps for what pubrules reads about secrets (secrets_config.py), and the
commands that moved to pubsec: the shared ones are conftest's."""

from __future__ import annotations

import json

from pytest_bdd import given, parsers, scenarios

scenarios("secrets-config.feature")

BLOCK = {"id": {"from": "the-app-id", "to": "THE_APP_ID"},
         "installation_id": {"from": "the-app-installation-id", "to": "THE_APP_INSTALLATION_ID"},
         "key": {"from": "the-app-key", "to": "THE_APP_KEY"}}


@given("its pubrules.json has a sound github_app block")
def sound(repo):
    config = json.loads((repo / "pubrules.json").read_text())
    config["github_app"] = BLOCK
    (repo / "pubrules.json").write_text(json.dumps(config))


@given(parsers.parse("its pubrules.json's signing_keys is {block}"))
def block(repo, block):
    config = json.loads((repo / "pubrules.json").read_text())
    config["signing_keys"] = json.loads(block)
    (repo / "pubrules.json").write_text(json.dumps(config))
