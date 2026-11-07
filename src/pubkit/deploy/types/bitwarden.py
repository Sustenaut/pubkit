"""bitwarden.secret and bitwarden.generated: a secret is in the Bitwarden
project ./with-secrets reads, by name, without calling bws: with-secrets
exports every secret of its project into the command's environment, and
says so with PUBKIT_SECRETS_PROJECT, so

    ./with-secrets ./pubkit deploy check

sees them. A generated secret's value must also have its shape. A value is
read, measured and dropped: only its length and kind are said."""

from __future__ import annotations

from typing import Any

from ...app_secrets import env_name
from ..model import Context, Need, Result
from . import Base, schema, with_secret
from .k8s import SHAPE, SHAPES

MARKER = "PUBKIT_SECRETS_PROJECT"


class BitwardenSecret(Base):
    """A secret a person puts in Bitwarden."""
    type = "bitwarden.secret"
    grade = "human"
    budget = 0.5
    schema = schema(with_secret({}))

    def present(self, need: Need, ctx: Context) -> tuple[str | None, Result | None]:
        """The value, or the result when there is none to look at."""
        project = ctx.env.get(MARKER)
        if not project:
            return None, Result("unknown", f"{need.name} is not looked for outside ./with-secrets",
                                fix="run this through ./with-secrets, which reads the Bitwarden project")
        value = ctx.env.get(env_name(need.name))
        if value is None:
            return None, Result("missing", f"{need.name} is not in the Bitwarden project {project}",
                                fix=f"put {need.name} in the Bitwarden project {project}, and give the "
                                    f"read-only machine account access to it")
        return value, None

    def check(self, need: Need, ctx: Context) -> Result:
        value, result = self.present(need, ctx)
        if result is not None:
            return result
        return Result("ready", f"{need.name} is in Bitwarden: {len(str(value).encode())} bytes")


class BitwardenGenerated(BitwardenSecret):
    """A secret made by a program, which must have its shape."""
    type = "bitwarden.generated"
    grade = "auto"
    schema = schema(with_secret({"shape": SHAPE}), ("shape",))

    def secret_shape(self, params: dict[str, Any]) -> str | None:
        return params.get("shape")

    def check(self, need: Need, ctx: Context) -> Result:
        value, result = self.present(need, ctx)
        if result is not None:
            return result
        raw = str(value).encode()
        shape = SHAPES[need.params["shape"]]
        if (why := shape.problem(raw)) is not None:
            return Result("drift", f"{need.name} is not {shape.what}: {why}"[:200],
                          fix=f"make {need.name} again, as {need.params['shape']}, in Bitwarden",
                          details=[f"{shape.what}: {why}"])
        return Result("ready", f"{need.name} is in Bitwarden: {len(raw)} bytes, {shape.what}")
