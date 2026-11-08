"""The canary: no secret value ever reaches what `pubkit deploy` prints.

Every fake Secret value, every fake Bitwarden value, the write token, every
fake key JSON and what the deploy channel shows of a Secret hold CANARY, and
every value a fulfil generates is kept as one too. Each test drives the
checks, and the fulfils, through a state where values are read, judged,
generated and moved, and what comes out (stdout, stderr, the journal, any
exception's text, and every argv kubectl, ssh, gcloud or gh was given) must
not hold one: a value moves on a standard input only. This test must never
be weakened: a check or a fulfil that needs to show a value is wrong, not
the test."""

from __future__ import annotations

import json

import pytest

from deploy_world import CANARY, run, world  # noqa: F401 - the fixture
from pubkit.deploy import load
from pubkit.deploy.model import Need, Result
from pubkit.deploy.registry import types
from pubkit.deploy.walk import walk

STATES = {
    "all well": lambda w: None,
    "a bad cookie in the cluster": lambda w: w.cluster["namespaces"]["example"]["secrets"]["example-page-secrets"]
    .update({"cookie-secret": (CANARY + "+/+/").encode()}),
    "a bad cookie in Bitwarden": lambda w: w.env.update({"EXAMPLE_COOKIE_SECRET": CANARY + "+/+/" * 6}),
    "a variable that differs from Bitwarden": lambda w: w.env.update({"EXAMPLE_USER": CANARY + "-other"}),
    "a Secret without its key": lambda w: w.cluster["namespaces"]["example"]["secrets"]["example-files"]
    .update({"other": CANARY.encode()}) or w.cluster["namespaces"]["example"]["secrets"]["example-files"]
    .pop("token"),
    "nothing reachable": lambda w: w.http.clear(),
    "kubectl forbidden": lambda w: w.cluster.update({"forbidden": True}),
    "GitHub forbidden": lambda w: w.github.update({"forbidden": True}),
    "outside with-secrets": lambda w: w.env.pop("PUBKIT_SECRETS_PROJECT"),
    "nothing in the cluster": lambda w: w.cluster["namespaces"].pop("example") and None,
    "no cookie in Bitwarden, nor its Secret": lambda w: w.env.pop("EXAMPLE_COOKIE_SECRET") and
    w.cluster["namespaces"]["example"]["secrets"].pop("example-page-secrets") and None,
    "over the deploy channel": lambda w: setattr(w, "channel", "deploy"),
    "over the deploy channel, before secret-keys": lambda w: setattr(w, "channel", "deploy") or
    w.cluster.update({"secret_keys_verb": False}),
    "GitHub without its secrets": lambda w: w.github.update({"secrets": set()}),
    "a key to make": lambda w: w.block["needs"].append({
        "type": "gcp.service-account-key", "account": "example-files", "project": "example-project",
        "bitwarden": "example-files-key"}),
    "a key Bitwarden refuses": lambda w: w.block["needs"].append({
        "type": "gcp.service-account-key", "account": "example-files", "project": "example-project",
        "bitwarden": "example-files-key"}) or setattr(w, "bitwarden_refuses", True),
    "kubectl refusing writes": lambda w: w.cluster["namespaces"]["example"]["secrets"].pop("example-files")
    and w.cluster.update({"refuse_writes": True}),
}


@pytest.mark.parametrize("state", sorted(STATES))
@pytest.mark.parametrize("argv", [("deploy", "check"), ("deploy", "check", "--json"),
                                  ("deploy", "check", "--offline", "--json"), ("deploy", "walk", "--fulfil", "--yes"),
                                  ("deploy", "walk", "--fulfil", "--yes", "--json")])
def test_no_value_is_printed(world, capsys, state, argv):
    STATES[state](world)
    run(world, capsys, *argv)
    assert world.code in (0, 1)
    for text in world.outputs:
        assert CANARY not in text
    assert not world.leaked(), world.leaked()[0][:500]


def test_an_exception_holding_a_value_is_not_shown(world, capsys):
    class Leaky:
        type, grade, budget, offline, schema = "example.leaky", "none", 1.0, True, {}

        def default_name(self, params):
            return None

        def depends_on(self, need, needs):
            return []

        def check(self, need, ctx):
            raise ValueError(f"the value is {CANARY}")

        def fulfil(self, need, ctx):
            raise NotImplementedError("phase 2")

    need = Need("example.leaky", "one", {})
    journal = walk({need.id: need}, {"example.leaky": Leaky()}, world.context(), service={})
    world.journal = journal.document()
    (_, result), = journal.entries
    assert result.state == "unknown"
    assert result.summary == "the check failed (ValueError)"
    assert CANARY not in journal.json()


def test_a_value_in_the_rendered_manifests_is_not_shown(world, capsys):
    world.rendered += ("---\napiVersion: v1\nkind: Secret\nmetadata: {name: example-held, namespace: example}\n"
                       f"stringData: {{cookie: {CANARY}}}\n")
    run(world, capsys, "deploy", "check", "--offline")
    assert CANARY not in world.stdout + world.stderr


def test_every_result_of_the_sound_fixture_is_free_of_values(world):
    ctx = world.context()
    plan = load(world.root, world.config, ctx)
    journal = walk(plan.needs, types(), ctx, service={})
    world.journal = journal.document()
    assert all(isinstance(r, Result) for _, r in journal.entries)
    assert CANARY not in journal.json()


def test_a_fulfil_that_raises_holding_a_value_is_not_shown(world):
    from pubkit.deploy.walk import fulfil  # noqa: PLC0415

    class Leaky:
        type, grade, budget, offline, schema = "example.leaky", "auto", 1.0, True, {}

        def default_name(self, params):
            return None

        def depends_on(self, need, needs):
            return []

        def check(self, need, ctx):
            return Result("missing", "not there")

        def fulfil(self, need, ctx):
            raise ValueError(f"the value is {CANARY}")

    need = Need("example.leaky", "one", {})
    kinds = {"example.leaky": Leaky()}
    ctx = world.context()
    journal = fulfil(walk({need.id: need}, kinds, ctx, service={}), kinds, ctx, {need.id})
    world.journal = journal.document()
    (_, result), = journal.entries
    assert result.state == "missing" and result.details[0].startswith("fulfil failed: the fulfil failed (ValueError)")
    assert CANARY not in journal.json()


def test_generated_values_stay_in_the_walk(world, capsys):
    """ctx.values holds what a fulfil made: never journaled, printed or kept."""
    world.env.pop("EXAMPLE_COOKIE_SECRET")
    run(world, capsys, "deploy", "walk", "--fulfil", "--yes", "--json")
    made = world.written["example-cookie-secret"]
    assert made in world.values
    assert made not in "".join(world.outputs) + json.dumps(world.journal)
