"""Steps for what pubkit reads about secrets (secrets_config.py), and the
commands that moved to pubsec: the shared ones are conftest's."""

from __future__ import annotations

import json

from pytest_bdd import given, parsers, scenarios

scenarios("secrets-config.feature")

BLOCK = {"id": {"from": "the-app-id", "to": "THE_APP_ID"},
         "installation_id": {"from": "the-app-installation-id", "to": "THE_APP_INSTALLATION_ID"},
         "key": {"from": "the-app-key", "to": "THE_APP_KEY"}}


@given("its pubkit.json has a sound github_app block")
def sound(repo):
    config = json.loads((repo / "pubkit.json").read_text())
    config["github_app"] = BLOCK
    (repo / "pubkit.json").write_text(json.dumps(config))


@given(parsers.parse("its pubkit.json's signing_keys is {block}"))
def block(repo, block):
    config = json.loads((repo / "pubkit.json").read_text())
    config["signing_keys"] = json.loads(block)
    (repo / "pubkit.json").write_text(json.dumps(config))
