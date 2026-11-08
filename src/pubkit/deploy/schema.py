"""The deploy block's schema, and what is wrong with a block: phrases, one
per problem, in the voice `pubkit sync` prints the other blocks' in.

The block's own keys are in schema.json, shipped with pubkit; each need's
keys are its type's schema (`pubkit deploy types` lists them), with the keys
every need may carry: type, name and after. pubkit has no JSON schema
library, so `validate` reads the part of JSON schema these schemas use:
type, properties, required, additionalProperties, items, enum, const,
pattern, minLength, maxLength, minItems, minimum, maximum and $ref within the
document. A schema's `x-phrase` says, in pubkit's words, what a value that
fails it must be, and `x-name` what an object is, for a key it does not take.
"""

from __future__ import annotations

import json
import re
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from .model import NeedType

BLOCK = "deploy"
WORKFLOWS = Path(".github/workflows")
DEFAULT_WORKFLOW = "deploy.yml"
DEFAULT_OVERLAY = "k8s"
KUSTOMIZATIONS = ("kustomization.yaml", "kustomization.yml", "Kustomization")
# The keys every need may carry, beside its type's own.
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/@-]{0,252}")
ID = re.compile(r"[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)*:[A-Za-z0-9][A-Za-z0-9._/@-]{0,252}")
COMMON: dict[str, Any] = {
    "type": {"type": "string"},
    "name": {"type": "string", "pattern": f"^{NAME.pattern}$",
             "x-phrase": "must be a name: letters, digits and . _ / @ -"},
    "after": {"type": "array", "items": {"type": "string", "pattern": f"^{ID.pattern}$",
                                         "x-phrase": "must be a need's id, TYPE:NAME"},
              "x-phrase": "must be a list of needs' ids, each TYPE:NAME"},
    "unit": {"type": "string", "x-phrase": "must be a unit's name"},
}
# Where a need's type says it fills a Kubernetes Secret: its name, the key, and
# whether the value is base64 that goes into the Secret decoded.
SECRET: dict[str, Any] = {
    "type": "object", "required": ["name"], "additionalProperties": False,
    "x-phrase": "must be an object with the Secret's name, and maybe key and decode",
    "properties": {
        "name": {"type": "string", "pattern": r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$",
                 "x-phrase": "must be a Secret's name, in lower case"},
        "key": {"type": "string", "pattern": r"^[-._a-zA-Z0-9]{1,253}$",
                "x-phrase": "must be a key of a Secret: letters, digits, - . _"},
        "decode": {"enum": ["base64"]},
    },
}
TYPES = {"string": "a string", "array": "a list", "object": "an object", "boolean": "true or false",
         "integer": "a whole number", "number": "a number", "null": "null"}


@cache
def block_schema() -> dict[str, Any]:
    return json.loads(resources.files("pubkit.deploy").joinpath("schema.json").read_text(encoding="utf-8"))


@cache
def journal_schema() -> dict[str, Any]:
    return json.loads(resources.files("pubkit.deploy").joinpath("journal.json").read_text(encoding="utf-8"))


def listed(words: list[str] | tuple[str, ...], last: str = "and") -> str:
    """a, b and c."""
    words = list(words)
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} {last} {words[-1]}"


def where(path: tuple[str | int, ...]) -> str:
    """needs[2].secret.name"""
    out = ""
    for step in path:
        out += f"[{step}]" if isinstance(step, int) else (f".{step}" if out else str(step))
    return out


def is_type(value: Any, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    return isinstance(value, {"string": str, "array": list, "object": dict, "boolean": bool,
                              "null": type(None)}[name])


Found = list[tuple[tuple[str | int, ...], str, bool]]


def validate(value: Any, schema: dict[str, Any], path: tuple[str | int, ...] = (),
             root: dict[str, Any] | None = None) -> Found:
    """What is wrong with `value` by `schema`: where, the phrase, and whether
    it is about a key of the object there rather than the value."""
    root = root or schema
    if "$ref" in schema:
        target: Any = root
        for step in schema["$ref"].removeprefix("#/").split("/"):
            target = target[step]
        schema = {**target, **{k: v for k, v in schema.items() if k != "$ref"}}
    own: list[str] = []
    deeper: Found = []
    wanted = schema.get("type")
    names = [wanted] if isinstance(wanted, str) else wanted or []
    if names and not any(is_type(value, n) for n in names):
        own.append(f"must be {listed([TYPES[n] for n in names], 'or')}")
    else:
        if "enum" in schema and value not in schema["enum"]:
            own.append(f"must be {listed([json.dumps(v) for v in schema['enum']], 'or')}")
        if "const" in schema and value != schema["const"]:
            own.append(f"must be {json.dumps(schema['const'])}")
        if isinstance(value, str):
            if "pattern" in schema and not re.search(schema["pattern"], value):
                own.append(f"must match {schema['pattern']}")
            if len(value) < schema.get("minLength", 0):
                own.append(f"must be at least {schema['minLength']} characters")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                own.append(f"must be at most {schema['maxLength']} characters")
        if is_type(value, "number"):
            if "minimum" in schema and value < schema["minimum"]:
                own.append(f"must be at least {schema['minimum']}")
            if "maximum" in schema and value > schema["maximum"]:
                own.append(f"must be at most {schema['maximum']}")
        if isinstance(value, list):
            if len(value) < schema.get("minItems", 0):
                own.append(f"must list at least {schema['minItems']}")
            if "items" in schema:
                for i, item in enumerate(value):
                    deeper += validate(item, schema["items"], (*path, i), root)
        if isinstance(value, dict):
            properties = schema.get("properties", {})
            for key in schema.get("required", []):
                if key not in value:
                    about = properties.get(key, {}).get("description", "")
                    deeper.append((path, f"{key} is missing" + (f": {about[0].lower()}{about[1:].rstrip('.')}"
                                                                 if about else ""), True))
            extra = schema.get("additionalProperties", True)
            for key in value:
                if key in properties:
                    deeper += validate(value[key], properties[key], (*path, key), root)
                elif extra is False:
                    of = f" of {schema['x-name']}" if "x-name" in schema else ""
                    deeper.append((path, f"{key} is not a setting{of}; it takes {listed(list(properties))}", True))
                elif isinstance(extra, dict):
                    deeper += validate(value[key], extra, (*path, key), root)
    if own and "x-phrase" in schema:
        own = [schema["x-phrase"]]
    return [(path, phrase, False) for phrase in own] + deeper


def phrase(prefix: str, path: tuple[str | int, ...], text: str, key: bool) -> str:
    """`deploy: namespace must be ...`, `deploy: needs[0]: type is missing`,
    or `deploy must be ...` for the block itself."""
    at = where(path)
    if key:
        return f"{prefix}: {at}: {text}" if at else f"{prefix}: {text}"
    return f"{prefix}: {at} {text}" if at else f"{prefix} {text}"


def need_schema(kind: NeedType) -> dict[str, Any]:
    """A type's schema with the keys every need carries."""
    schema = dict(kind.schema)
    schema["x-name"] = kind.type
    schema["properties"] = {**COMMON, **schema.get("properties", {})}
    schema["required"] = ["type", *[k for k in schema.get("required", []) if k != "type"]]
    return schema


def need_problems(entry: Any, i: int, types: dict[str, NeedType], root: Path,
                  providers: list[str] | None = None) -> list[str]:
    """What is wrong with one need as written."""
    kind = types.get(entry["type"])
    if kind is None:
        if providers:
            return [f"deploy: needs[{i}]: {entry['type']} is no type of need pubkit or an installed package "
                    f"offers; it would be {listed(providers, 'or')}'s, of deploy.providers, which ./pubkit runs "
                    f"with: run ./pubkit, or install {listed(providers, 'or')}"]
        return [f"deploy: needs[{i}]: {entry['type']} is no type of need pubkit knows; "
                f"it knows {listed(sorted(types))}; a package's types are named in deploy.providers"]
    name = entry.get("name") if isinstance(entry.get("name"), str) else None
    label = f"deploy: needs[{i}] ({entry['type']}:{name})" if name else f"deploy: needs[{i}] ({entry['type']})"
    out = [phrase(label, *found) for found in validate(entry, need_schema(kind))]
    if out:
        return out
    if name is None and kind.default_name(entry) is None and not getattr(kind, "singleton", False):
        out.append(f"{label}: it needs a name")
    for path in getattr(kind, "files", lambda _params: [])(entry):
        if not (root / path).is_file():
            out.append(f"{label}: {path} is not a file in the repository")
    return out


def problems(config: dict[str, Any], root: Path, types: dict[str, NeedType] | None = None) -> list[str]:
    """What is wrong with pubkit.json's deploy block: phrases, none when it is sound.
    It opens no file but to see that the workflow, the overlay and each
    k8s.manifests path are there."""
    if types is None:
        from .registry import types as registered  # noqa: PLC0415
        types = registered()
    block = config.get(BLOCK)
    out = [phrase(BLOCK, *found) for found in validate(block, block_schema())]
    if out or not isinstance(block, dict):
        return out
    from .units import names, unit_block  # noqa: PLC0415
    workflow = block.get("workflow", DEFAULT_WORKFLOW)
    if not (root / WORKFLOWS / workflow).is_file():
        out.append(f"deploy: workflow {workflow} is not in {WORKFLOWS}")
    units = names(block)
    if block["namespace"] in (block.get("units") or {}):
        out.append(f"deploy: units: {block['namespace']} is the top level's unit, named by its namespace")
    for unit in units:
        overlay = unit_block(block, unit).get("overlay", DEFAULT_OVERLAY)
        if not any((root / overlay / k).is_file() for k in KUSTOMIZATIONS):
            of = "" if unit == block["namespace"] else f" (unit {unit})"
            out.append(f"deploy: overlay {overlay}{of} has no kustomization.yaml")
    offered = [p["distribution"] for p in block.get("providers", [])]
    for i, entry in enumerate(block.get("needs", [])):
        out += need_problems(entry, i, types, root, offered)
        if "unit" in entry and entry["unit"] not in units:
            out.append(f"deploy: needs[{i}]: unit {entry['unit']} is no unit of the block; it has {listed(units)}")
    if out:
        return out
    from .implied import build  # noqa: PLC0415
    from .order import cycle_problems  # noqa: PLC0415
    for unit in units:
        plan, bad = build(config, types, root=root, unit=unit)
        bad = bad or cycle_problems(plan, types)
        if bad:
            return bad
    return []
