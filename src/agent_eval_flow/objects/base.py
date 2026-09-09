"""Shared record behavior; no runtime bindings or mutable caches are retained."""
from dataclasses import fields
from datetime import datetime, timezone
from decimal import Decimal
import math

from .identity import freeze_json, semantic_fingerprint


class RecordBase:
    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, datetime):
                if value.tzinfo is None or value.utcoffset() is None:
                    raise ValueError(f"{field.name} must have a timezone")
                value = value.astimezone(timezone.utc)
            object.__setattr__(self, field.name, freeze_json(value))
        name = type(self).__name__
        for field in ("id", "name"):
            if hasattr(self, field) and not getattr(self, field).strip():
                raise ValueError(f"{field} must not be empty")
        if name == "Observation":
            if self.status == "unknown":
                if self.value is not None or not self.reason:
                    raise ValueError("Unknown observation requires null and a reason")
            elif self.value is None:
                raise ValueError("Observed/estimated observation requires a value")
            if self.status == "estimated" and not self.reason:
                raise ValueError("An estimate requires a reason")
        if name == "NativeConfig" and not self.schema_ref.revision:
            raise ValueError("A native dialect requires a resolved, nonempty revision")
        if name == "Budget":
            if not math.isfinite(self.wall_time_s) or self.wall_time_s <= 0:
                raise ValueError("wall_time_s must be finite and positive")
            if self.max_tokens is not None and (type(self.max_tokens) is not int or self.max_tokens <= 0):
                raise ValueError("max_tokens must be a positive integer")
            if self.max_cost_usd is not None and (not self.max_cost_usd.is_finite() or self.max_cost_usd <= 0):
                raise ValueError("max_cost_usd must be finite and positive")
        if name == "ExecutionPolicy":
            if self.repetitions <= 0 or self.max_concurrency <= 0 or self.infrastructure_retries < 0:
                raise ValueError("Repetitions/concurrency must be positive and retries nonnegative")
        if name == "Execution" and self.retry_index < 0:
            raise ValueError("retry_index must be nonnegative")
        if name == "Assignment" and self.repetition < 0:
            raise ValueError("repetition must be nonnegative")
        if name == "Resources":
            for field in ("cost_usd", "input_tokens", "output_tokens", "human_minutes"):
                value = getattr(self, field).value
                if value is not None:
                    if isinstance(value, bool) or value < 0 or (isinstance(value, Decimal) and not value.is_finite()) or (isinstance(value, float) and not math.isfinite(value)):
                        raise ValueError(f"{field} must be finite and nonnegative")
        for field in ("cost_scope", "evaluation_cost_scope"):
            if hasattr(self, field):
                scope = getattr(self, field)
                if not scope or len(set(scope)) != len(scope) or not all(part.strip() for part in scope):
                    raise ValueError(f"{field} must contain distinct, nonempty categories")
        if hasattr(self, "started_at") and hasattr(self, "ended_at"):
            if self.started_at is not None and self.ended_at is not None and self.ended_at < self.started_at:
                raise ValueError("End time precedes start time")
        if name == "Run" and self.output is not None and self.output_state != "available":
            raise ValueError("A non-null captured output must be available")

    def validate(self):
        from .validation import validate
        return validate(self)

    def fingerprint(self):
        from .identity import definition_payload
        return semantic_fingerprint(type(self).__name__, definition_payload(self))

    def save(self, path):
        from ..storage.manifests import save
        return save(self, path)

    @classmethod
    def load(cls, path):
        from ..storage.manifests import load
        return load(path, expected_type=cls)
