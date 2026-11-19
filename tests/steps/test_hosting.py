"""Steps for hosting.py: the hosting block of pubrules.json that the hosting
modules read. fixtures/hosting/pubrules.json is a copy of the block the
modules are tested with, in the package that ships them."""

from __future__ import annotations

import json
from pathlib import Path

from pytest_bdd import given, parsers, scenarios, then, when

from pubrules import hosting

scenarios("hosting.feature")

TESTDATA = Path(__file__).resolve().parents[1] / "fixtures" / "hosting" / "pubrules.json"


@given("the pubrules.json the Terraform modules are tested with", target_fixture="config")
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


@when("pubrules reads the hosting block")
def reads(pubtest_ctx, config):
    pubtest_ctx.attempt(lambda: hosting.settings(config))


@then("the result is the hosting block it was given")
def accepts(pubtest_ctx, config):
    assert pubtest_ctx.refused is None, pubtest_ctx.refused
    assert pubtest_ctx.result == config.get("hosting", {}), pubtest_ctx.result
