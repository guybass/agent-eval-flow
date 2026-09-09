"""Typed, lossless JSON envelopes derived from the public Pydantic records.

Only declared record types are reconstructed. Arbitrary agent JSON is opaque;
in particular a JSON object resembling a metric tag is never interpreted there.
"""
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
import json
import types
from typing import Annotated, Any, Literal, TypeVar, Union, get_args, get_origin, get_type_hints

from pydantic import (BaseModel, ConfigDict, Field, JsonValue, StrictBool,
                      StrictFloat, StrictInt, StrictStr, create_model)

from .. import objects as o


_CONFIG = ConfigDict(extra="forbid", allow_inf_nan=False)


class _BoolMetric(BaseModel):
    model_config = _CONFIG
    type: Literal["bool"]
    value: StrictBool


class _IntMetric(BaseModel):
    model_config = _CONFIG
    type: Literal["int"]
    value: StrictInt


class _FloatMetric(BaseModel):
    model_config = _CONFIG
    type: Literal["float"]
    value: StrictFloat


class _DecimalMetric(BaseModel):
    model_config = _CONFIG
    type: Literal["decimal"]
    value: StrictStr


class _TextMetric(BaseModel):
    model_config = _CONFIG
    type: Literal["text"]
    value: StrictStr


_METRIC = Annotated[Union[_BoolMetric, _IntMetric, _FloatMetric, _DecimalMetric, _TextMetric], Field(discriminator="type")]
_ROOTS = {"study": "Study", "dataset": "EvalDataset", "run_set": "RunSet", "evaluation_result": "EvaluationResult"}
_MODEL_CACHE = {}


def _kind(expected):
    if isinstance(expected, str):
        if expected in _ROOTS:
            return expected
        raise o.StorageError(f"Unsupported root kind: {expected}")
    for kind, name in _ROOTS.items():
        if expected is getattr(o, name):
            return kind
    raise o.StorageError(f"Unsupported root type: {expected}")


def _unroll(annotation):
    # Named recursive JSON remains opaque. Other named aliases can be resolved.
    name = getattr(annotation, "__name__", "")
    if name in ("JSONValue", "JSONScalar"):
        return JsonValue
    if hasattr(annotation, "__value__"):
        return _unroll(annotation.__value__)
    return annotation


def _metric(annotation):
    annotation = _unroll(annotation)
    return get_origin(annotation) in (Union, types.UnionType) and set(get_args(annotation)) == {bool, int, float, Decimal, str}


def _substitute(annotation, substitutions):
    if isinstance(annotation, TypeVar):
        return substitutions.get(annotation, Any)
    origin, args = get_origin(annotation), get_args(annotation)
    if not args:
        return annotation
    replaced = tuple(_substitute(arg, substitutions) for arg in args)
    if replaced == args:
        return annotation
    if origin in (types.UnionType, Union):
        return Union[replaced]
    try:
        return origin[replaced[0] if len(replaced) == 1 else replaced]
    except TypeError:
        return annotation


def _record_info(annotation):
    origin = get_origin(annotation) or annotation
    if not isinstance(origin, type) or not is_dataclass(origin):
        return None
    # No import from an on-disk class name: this identity must already be one of
    # the library's explicitly exported record classes.
    if not any(origin is value for value in vars(o).values()):
        raise o.StorageError(f"Unregistered record type: {origin}")
    substitutions = dict(zip(getattr(origin, "__parameters__", ()), get_args(annotation)))
    hints = get_type_hints(origin)
    return origin, {f.name: _substitute(hints[f.name], substitutions) for f in fields(origin)}


def _wire_type(annotation):
    annotation = _unroll(annotation)
    if _metric(annotation):
        return _METRIC
    if annotation is JsonValue or annotation is Any or isinstance(annotation, TypeVar):
        return JsonValue
    record = _record_info(annotation)
    if record:
        if annotation not in _MODEL_CACHE:
            origin, hints = record
            name = "Wire" + origin.__name__ + str(len(_MODEL_CACHE))
            model_fields = {_wire_field_name(key): (_wire_type(hint), Field(alias=key))
                            for key, hint in hints.items()}
            _MODEL_CACHE[annotation] = create_model(name, __config__=_CONFIG, **model_fields)
        return _MODEL_CACHE[annotation]
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (types.UnionType, Union):
        if type(None) in args and set(args) - {type(None)} == {bool, int, float, Decimal, str}:
            return Union[_METRIC, None]
        return Union[tuple(_wire_type(arg) for arg in args)]
    if origin in (dict, Mapping):
        return dict[_wire_type(args[0]), _wire_type(args[1])]
    if origin in (tuple, list, Sequence):
        if origin is tuple and len(args) > 1 and args[-1] is not Ellipsis:
            return tuple[tuple(_wire_type(arg) for arg in args)]
        return tuple[_wire_type(args[0]), ...]
    if annotation is Decimal:
        return StrictStr
    return {str: StrictStr, int: StrictInt, float: StrictFloat, bool: StrictBool}.get(annotation, annotation)


def _wire_field_name(name):
    # Public DataTable.schema is legitimate; alias its Pydantic wire field so
    # it does not shadow BaseModel's legacy schema() API.
    return "record_schema" if name == "schema" else name


def _tag(value):
    names = {bool: "bool", int: "int", float: "float", Decimal: "decimal", str: "text"}
    kind = names.get(type(value))
    if kind is None:
        raise o.StorageError(f"Invalid metric scalar type: {type(value).__name__}")
    return {"type": kind, "value": str(value) if isinstance(value, Decimal) else value}


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _pick_union(annotation, value):
    options = get_args(annotation)
    if value is None and type(None) in options:
        return type(None)
    # Optional MetricValue flattens into six scalar members. Handle this union
    # as one typed metric channel, not as five coercible alternatives.
    non_null = tuple(arg for arg in options if arg is not type(None))
    if set(non_null) == {bool, int, float, Decimal, str}:
        return Union[non_null]
    for arg in options:
        origin = get_origin(arg) or arg
        if origin is Literal and value in get_args(arg):
            return arg
        if isinstance(origin, type) and isinstance(value, origin):
            return arg
        wire = _wire_type(arg)
        if get_origin(wire) is Annotated:
            wire = get_args(wire)[0]
        if isinstance(wire, type) and isinstance(value, wire):
            return arg
    raise o.StorageError(f"Value does not match declared union: {annotation}")


def _encode(value, annotation):
    annotation = _unroll(annotation)
    if _metric(annotation):
        return _tag(value)
    if annotation is JsonValue or annotation is Any or isinstance(annotation, TypeVar):
        return _plain(value)
    record = _record_info(annotation)
    if record:
        _, hints = record
        return {key: _encode(getattr(value, key), hint) for key, hint in hints.items()}
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (types.UnionType, Union):
        if value is None:
            return None
        return _encode(value, _pick_union(annotation, value))
    if origin in (dict, Mapping):
        return {key: _encode(item, args[1]) for key, item in value.items()}
    if origin in (tuple, list, Sequence):
        if origin is tuple and len(args) > 1 and args[-1] is not Ellipsis:
            return [_encode(item, hint) for item, hint in zip(value, args)]
        return [_encode(item, args[0]) for item in value]
    if annotation is Decimal:
        return str(value)
    if annotation is datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise o.StorageError("Datetime must include a timezone")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return value


def _decode(value, annotation):
    annotation = _unroll(annotation)
    if _metric(annotation):
        return Decimal(value.value) if value.type == "decimal" else value.value
    if annotation is JsonValue or annotation is Any or isinstance(annotation, TypeVar):
        return _plain(value)
    record = _record_info(annotation)
    if record:
        cls, hints = record
        return cls(**{key: _decode(getattr(value, _wire_field_name(key)), hint) for key, hint in hints.items()})
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (types.UnionType, Union):
        if value is None:
            return None
        return _decode(value, _pick_union(annotation, value))
    if origin in (dict, Mapping):
        return {key: _decode(item, args[1]) for key, item in value.items()}
    if origin in (tuple, list, Sequence):
        if origin is tuple and len(args) > 1 and args[-1] is not Ellipsis:
            return tuple(_decode(item, hint) for item, hint in zip(value, args))
        return tuple(_decode(item, args[0]) for item in value)
    if annotation is Decimal:
        return Decimal(value)
    return value


@lru_cache(maxsize=4)
def _envelope(kind):
    cls = getattr(o, _ROOTS[kind])
    return create_model("AgentEvalFlow" + cls.__name__ + "Envelope", __config__=_CONFIG,
        format=(Literal["agent-eval-flow"], ...), schema_version=(Literal["0.4"], ...),
        kind=(Literal[kind], ...), data=(_wire_type(cls), ...))


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise o.StorageError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def encode_root(root):
    from .manifests import validate_root
    validate_root(root)
    kind = _kind(type(root))
    document = {"format": "agent-eval-flow", "schema_version": "0.4", "kind": kind,
                "data": _encode(root, type(root))}
    try:
        payload = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        return _envelope(kind).model_validate_json(payload, strict=True).model_dump_json(indent=2, by_alias=True).encode("utf-8")
    except Exception as exc:
        if isinstance(exc, o.AgentEvalFlowError):
            raise
        raise o.StorageError(f"Cannot encode {kind} manifest: {exc}") from exc


def decode_root(payload, expected_kind):
    kind = _kind(expected_kind)
    try:
        json.loads(payload, object_pairs_hook=_unique_pairs,
                   parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"Nonfinite JSON value: {value}")))
        envelope = _envelope(kind).model_validate_json(payload, strict=True)
        root = _decode(envelope.data, getattr(o, _ROOTS[kind]))
        from .manifests import validate_root
        validate_root(root)
        return root
    except Exception as exc:
        if isinstance(exc, o.AgentEvalFlowError):
            raise
        raise o.StorageError(f"Invalid {kind} manifest: {exc}") from exc


def json_schema(kind):
    return _envelope(_kind(kind)).model_json_schema()
