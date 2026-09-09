"""Inventory-aware aggregation of exclusive resource observations."""
from decimal import Decimal, localcontext


def unknown(reason):
    from .records import Observation
    return Observation(value=None, status="unknown", reason=reason)


def observed(value, reason=None, evidence=()):
    from .records import Observation
    return Observation(value=value, status="observed", reason=reason, evidence=tuple(evidence))


def combine_inventory(observations):
    observations = tuple(observations)
    if any(item.status == "observed" and item.value is False for item in observations):
        return observed(False, "A captured inventory is incomplete")
    if any(item.status != "observed" or item.value is not True for item in observations):
        return unknown("The complete inventory is not established")
    return observed(True, "All contributing inventories are complete")


def aggregate_resources(items, *, inventory_complete, cost_scope):
    from .records import Observation, Resources
    items = tuple(items)
    fields = ("cost_usd", "input_tokens", "output_tokens", "human_minutes")
    if inventory_complete.status != "observed" or inventory_complete.value is not True:
        return Resources(cost_scope=cost_scope, **{name: unknown("The complete execution/grading inventory is not established") for name in fields})
    values = {}
    for name in fields:
        observations = [getattr(item, name) for item in items]
        if name == "cost_usd" and any(item.cost_scope != cost_scope for item in items):
            values[name] = unknown("Contributing cost scopes differ from the requested scope")
            continue
        if any(item.status == "unknown" or item.value is None for item in observations):
            values[name] = unknown(f"At least one {name} observation is unknown")
            continue
        if name == "cost_usd":
            coefficients = [item.value.as_tuple() for item in observations]
            precision = sum(len(t.digits) + abs(t.exponent) for t in coefficients) + len(str(len(items))) + 10
            with localcontext() as context:
                context.prec = max(28, precision)
                value = sum((item.value for item in observations), Decimal("0"))
        else:
            value = sum((item.value for item in observations), 0.0 if name == "human_minutes" else 0)
        estimated = any(item.status == "estimated" for item in observations)
        values[name] = Observation(value=value, status="estimated" if estimated else "observed",
            reason="Includes estimated observations" if estimated else "Sum of exclusive recorded quantities",
            evidence=tuple(e for item in observations for e in item.evidence))
    return Resources(cost_scope=cost_scope, **values)


def zero_resources(cost_scope=("model",)):
    from .records import Resources
    return Resources(cost_usd=observed(Decimal("0")), cost_scope=cost_scope,
                     input_tokens=observed(0), output_tokens=observed(0), human_minutes=observed(0.0))
