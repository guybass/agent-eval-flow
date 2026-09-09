"""Immutable snapshots and deterministic, typed semantic identities."""
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math


class FrozenDict(dict):
    """A detached dictionary snapshot compatible with ordinary Mapping consumers."""

    def _immutable(self, *args, **kwargs):
        raise TypeError("Evaluation records are immutable; construct a new record")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _immutable

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


def freeze_json(value):
    if isinstance(value, Mapping):
        return FrozenDict({key: freeze_json(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(freeze_json(item) for item in value)
    return value


def plain(value):
    if is_dataclass(value):
        return {field.name: plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    return value


def _canonical(value):
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Canonical values must be finite")
        return ["float", value.hex()]
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Canonical decimals must be finite")
        sign, digits, exponent = value.as_tuple()
        digits = list(digits)
        if not value:
            return ["decimal", 0, "0", 0]
        while digits[-1] == 0:
            digits.pop()
            exponent += 1
        return ["decimal", sign, "".join(map(str, digits)), exponent]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Naive timestamps have no stable identity")
        return ["datetime", value.astimezone(timezone.utc).isoformat()]
    if is_dataclass(value):
        return _canonical({f.name: getattr(value, f.name) for f in fields(value)})
    if isinstance(value, Mapping):
        if not all(isinstance(k, str) for k in value):
            raise ValueError("Canonical mapping keys must be strings")
        return ["map", [[k, _canonical(value[k])] for k in sorted(value)]]
    if isinstance(value, (tuple, list)):
        return ["array", [_canonical(item) for item in value]]
    raise TypeError(f"Unsupported canonical value {type(value).__name__}")


def canonical_bytes(value):
    return json.dumps(_canonical(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def semantic_fingerprint(kind, payload):
    return hashlib.sha256(b"agent-eval-flow:0.4:" + kind.encode() + b"\n" + canonical_bytes(payload)).hexdigest()


def typed_key(row, columns):
    result = []
    for column in columns:
        value = row[column]
        if type(value) not in (str, int):
            raise ValueError(f"Key {column} must be a string or integer, never bool")
        result.append((type(value).__name__, value))
    return tuple(result)


def definition_payload(record):
    name = type(record).__name__
    values = {field.name: getattr(record, field.name) for field in fields(record)}
    if name == "Candidate":
        return {key: value for key, value in values.items() if key not in ("id", "description")}
    if name == "EvalDataset":
        values.pop("id")
        values.pop("description")
        values["cluster_by"] = record.cluster_by or record.unit_key
    elif name == "EvalSuite":
        values.pop("id")
    elif name == "Study":
        values.pop("id")
        values.pop("question")
        values["dataset"] = record.dataset.fingerprint()
        values["candidates"] = {key: value.fingerprint() for key, value in record.candidates.items()}
        values["suite"] = record.suite.fingerprint()
    return values
