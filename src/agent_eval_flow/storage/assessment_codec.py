"""Separate assessment envelopes; nested behavioral data keeps its v0.4 envelope."""
from dataclasses import fields
from functools import lru_cache
import json
from typing import Literal, get_type_hints

from pydantic import create_model

from .. import objects as o
from ..objects import assessment as a
from . import codec


def _kind(expected):
    roots = {"assessment_plan": a.AssessmentPlan, "assessment_result": a.AssessmentResult}
    for kind, cls in roots.items():
        if expected == kind or expected is cls:
            return kind, cls
    raise o.StorageError(f"Unsupported assessment root: {expected}")


@lru_cache(maxsize=2)
def _wire(cls):
    hints = get_type_hints(cls)
    definitions = {}
    for field in fields(cls):
        name, hint = field.name, hints[field.name]
        if cls is a.AssessmentPlan and name == "behavior":
            value_type = codec._envelope("study") | None
        elif cls is a.AssessmentResult and name == "behavior_result":
            value_type = codec._envelope("evaluation_result") | None
        elif cls is a.AssessmentResult and name == "plan":
            value_type = _wire(a.AssessmentPlan)
        else:
            value_type = codec._wire_type(hint)
        definitions[name] = (value_type, ...)
    return create_model("AssessmentWire" + cls.__name__, __config__=codec._CONFIG, **definitions)


@lru_cache(maxsize=2)
def _envelope(kind):
    _, cls = _kind(kind)
    return create_model("Assessment" + cls.__name__ + "Envelope", __config__=codec._CONFIG,
        format=(Literal["agent-eval-flow-assessment"], ...),
        schema_version=(Literal["0.1"], ...), kind=(Literal[kind], ...), data=(_wire(cls), ...))


def _encode_record(root):
    hints = get_type_hints(type(root))
    output = {}
    for field in fields(root):
        name, value = field.name, getattr(root, field.name)
        if name in ("behavior", "behavior_result") and value is not None:
            output[name] = json.loads(codec.encode_root(value))
        elif name == "plan" and isinstance(value, a.AssessmentPlan):
            output[name] = _encode_record(value)
        else:
            output[name] = codec._encode(value, hints[name])
    return output


def _decode_record(value, cls):
    hints, output = get_type_hints(cls), {}
    for field in fields(cls):
        name, item = field.name, getattr(value, field.name)
        if name in ("behavior", "behavior_result") and item is not None:
            output[name] = codec.decode_root(item.model_dump_json(by_alias=True),
                "study" if name == "behavior" else "evaluation_result")
        elif name == "plan" and cls is a.AssessmentResult:
            output[name] = _decode_record(item, a.AssessmentPlan)
        else:
            output[name] = codec._decode(item, hints[name])
    return cls(**output)


def encode_assessment_root(root):
    kind, _ = _kind(type(root))
    root.validate().raise_for_errors()
    try:
        document = {"format": "agent-eval-flow-assessment", "schema_version": "0.1",
                    "kind": kind, "data": _encode_record(root)}
        payload = json.dumps(document, ensure_ascii=False, allow_nan=False)
        return _envelope(kind).model_validate_json(payload, strict=True).model_dump_json(
            indent=2, by_alias=True).encode("utf-8")
    except o.AgentEvalFlowError:
        raise
    except Exception as exc:
        raise o.StorageError(f"Cannot encode {kind}: {exc}") from exc


def decode_assessment_root(payload, expected_kind):
    kind, cls = _kind(expected_kind)
    try:
        json.loads(payload, object_pairs_hook=codec._unique_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"Nonfinite JSON: {value}")))
        envelope = _envelope(kind).model_validate_json(payload, strict=True)
        root = _decode_record(envelope.data, cls)
        root.validate().raise_for_errors()
        return root
    except o.AgentEvalFlowError:
        raise
    except Exception as exc:
        raise o.StorageError(f"Invalid {kind}: {exc}") from exc


def assessment_json_schema(kind):
    return _envelope(_kind(kind)[0]).model_json_schema()
