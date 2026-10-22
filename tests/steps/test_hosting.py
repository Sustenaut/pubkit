"""Steps for hosting.py: the hosting block of pubkit.json that
terraform/hosting reads."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then

from pubkit import hosting

scenarios("hosting.feature")

TESTDATA = Path(__file__).resolve().parents[2] / "terraform" / "testdata" / "pubkit.json"


@given("the pubkit.json the Terraform modules are tested with", target_fixture="config")
def module_testdata():
    return json.loads(TESTDATA.read_text())


@given(parsers.parse("the hosting settings '{text}'"), target_fixture="config")
def config(text):
    return json.loads(text)


@given(parsers.parse("the providers of the modules' test setup with the zone '{zone}'"),
       target_fixture="config")
def with_zone(zone):
    config = json.loads(TESTDATA.read_text())
    config["hosting"]["zones"] = {"example.eu": json.loads(zone)}
    return config


@then("pubkit accepts its hosting block")
def accepts(config):
    assert hosting.settings(config) == config.get("hosting", {})


@then(parsers.parse('reading them stops with "{message}"'))
def stops(config, message):
    with pytest.raises(SystemExit) as stop:
        hosting.settings(config)
    assert message in str(stop.value)
