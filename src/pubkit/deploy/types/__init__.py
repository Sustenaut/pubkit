"""The need types pubkit has itself. Phase 1 checks only: every fulfil
raises NotImplementedError("phase 2")."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ...protect import ApiError
from ..model import Context, Grade, Need, Ran, Result


class Base:
    """What a built-in type shares. A `singleton` is named by the namespace."""
    type = ""
    schema: dict[str, Any] = {"type": "object", "additionalProperties": False, "properties": {}}
    grade: Grade = "none"
    budget = 1.0
    offline = False
    singleton = False

    def default_name(self, params: dict[str, Any]) -> str | None:
        return None

    def depends_on(self, need: Need, needs: Mapping[str, Need]) -> list[str]:
        return []

    def check(self, need: Need, ctx: Context) -> Result:
        raise NotImplementedError

    def fulfil(self, need: Need, ctx: Context) -> Result:
        raise NotImplementedError("phase 2")


def schema(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "object", "additionalProperties": False, "properties": properties}
    if required:
        out["required"] = list(required)
    return out


def with_secret(properties: dict[str, Any]) -> dict[str, Any]:
    from ..schema import SECRET  # noqa: PLC0415
    return {**properties, "secret": SECRET}


def kubectl_json(ctx: Context, *args: str) -> tuple[Any, Ran]:
    """kubectl's JSON answer to `args` -o json, or None, and how it ran."""
    ran = ctx.kubectl([*args, "-o", "json"])
    if ran.returncode != 0:
        return None, ran
    try:
        return json.loads(ran.stdout), ran
    except ValueError:
        return None, Ran(1, "", "kubectl's answer is not JSON")


def not_found(ran: Ran) -> bool:
    return "(NotFound)" in ran.stderr or " not found" in ran.stderr


def kubectl_refused(ran: Ran, ns: str, what: str) -> Result:
    """`unknown`, saying what access is missing, or what kubectl said."""
    last = (ran.stderr.strip().splitlines() or [f"exit {ran.returncode}"])[-1]
    if ran.returncode == 127:
        return Result("unknown", f"kubectl did not run, to read {what}", fix="install kubectl, or give --kubectl CMD")
    if "(Forbidden)" in ran.stderr or "forbidden" in ran.stderr:
        return Result("unknown", f"kubectl may not read {what} in {ns}",
                      fix=f"use a kubeconfig that may get {what} in {ns} ({last})")
    return Result("unknown", f"kubectl could not read {what} in {ns}", fix=f"kubectl said: {last}")


def github_refused(error: ApiError, what: str) -> Result:
    if error.status in (401, 403):
        return Result("unknown", f"GitHub refused to show {what}",
                      fix="sign gh in as someone who administers the repository (gh auth login)")
    return Result("unknown", f"GitHub could not be asked for {what}", fix=f"gh said: {str(error)[:200]}")
