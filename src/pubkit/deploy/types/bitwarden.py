"""bitwarden.secret and bitwarden.generated: a secret is in the Bitwarden
project ./with-secrets reads, by name, without calling bws: with-secrets
exports every secret of its project into the command's environment, and
says so with PUBKIT_SECRETS_PROJECT, so

    ./with-secrets ./pubkit deploy check

sees them. A generated secret's value must also have its shape. A value is
read, measured and dropped: only its length and kind are said.

`walk --fulfil` makes a missing or misshapen generated secret: it generates
the value, writes it to Bitwarden with the walk's write token
(ctx.bitwarden_writer), and keeps it in ctx.values for the rest of the walk,
so the Secret it fills is made from it. A value a fulfil made is found there
before the environment, which was read when the walk started."""

from __future__ import annotations

import base64
import secrets
from typing import Any

from ...app_secrets import env_name
from ..model import Context, Need, Result
from . import Base, schema, with_secret
from .k8s import SHAPE, SHAPES

MARKER = "PUBKIT_SECRETS_PROJECT"


def generate(shape: str) -> str:
    """A new value of the shape: 32 random bytes, encoded as the shape says."""
    raw = secrets.token_bytes(32)
    if shape == "hex-32":
        return raw.hex()
    if shape == "base64-32":
        return base64.b64encode(raw).decode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()  # oauth2-proxy-cookie


class BitwardenSecret(Base):
    """A secret a person puts in Bitwarden."""
    type = "bitwarden.secret"
    grade = "human"
    budget = 0.5
    schema = schema(with_secret({}))

    def value(self, need: Need, ctx: Context) -> bytes | None:
        """The value: what this walk made, else what ./with-secrets gave."""
        if need.name in ctx.values:
            return ctx.values[need.name]
        found = ctx.env.get(env_name(need.name)) if ctx.env.get(MARKER) else None
        return None if found is None else found.encode()

    def present(self, need: Need, ctx: Context) -> tuple[str | None, Result | None]:
        """The value, or the result when there is none to look at."""
        if need.name in ctx.values:
            return ctx.values[need.name].decode(), None
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
    fulfil_budget = 60.0
    needs_of_context = ("write-token",)
    schema = schema(with_secret({"shape": SHAPE}), ("shape",))

    def action(self, need: Need, ctx: Context) -> str:
        found = ctx.results.get(need.id)
        if found is not None and found.state == "drift":
            # A value is there: the plan says it goes, and why, before the typed yes.
            why = (found.details or [found.summary])[0].partition(": ")[2] or found.summary
            return f"replace {need.name} (misshapen: {why}) with a new {need.params['shape']}, written to Bitwarden"
        return f"generate {need.name} as {need.params['shape']}, and write it to Bitwarden"

    def fulfil(self, need: Need, ctx: Context) -> Result:
        if ctx.bitwarden_writer is None:
            return Result("missing", "there is no Bitwarden write token", fix="run the walk at a terminal")
        shape = need.params["shape"]
        value = generate(shape)
        try:
            ctx.bitwarden_writer.write(need.name, value, f"made by pubkit deploy walk, as {shape}")
        except Exception as e:  # noqa: BLE001 - the writer's failure is the need's, said without a value
            return Result("missing", f"Bitwarden refused {need.name} ({type(e).__name__})",
                          fix=str(e).replace(value, "[hidden]")[:300])
        ctx.values[need.name] = value.encode()
        return Result("ready", f"generated {need.name}, {len(value)} bytes as {shape}, and wrote it to Bitwarden")

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
