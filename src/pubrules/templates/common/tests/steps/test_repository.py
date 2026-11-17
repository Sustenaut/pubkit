"""Steps for repository.feature, which `pubrules init` seeded. Its Thens are
pubtest's; the When is the repository's own, through `ctx.attempt`."""

import json
from pathlib import Path

from pytest_bdd import scenarios, when

scenarios("repository.feature")


@when("I read the repository's pubrules.json")
def read_config(ctx):
    ctx.attempt(lambda: json.loads((Path(__file__).parents[2] / "pubrules.json").read_text(encoding="utf-8")))
