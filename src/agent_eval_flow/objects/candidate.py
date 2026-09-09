"""Explicit candidate revisions and behavior-only differences."""
from dataclasses import replace
from .identity import definition_payload, plain, canonical_bytes


def derive(candidate, *, id, components=None, settings=None, native=...):
    merged = dict(candidate.components)
    for key, component in (components or {}).items():
        if component is None:
            merged.pop(key, None)
        else:
            merged[key] = component
    return replace(candidate, id=id, components=merged, settings={**candidate.settings, **(settings or {})},
                   native=candidate.native if native is ... else native)


def diff(candidate, other):
    from .records import ConfigChange
    changes = []
    def visit(before, after, path):
        if isinstance(before, dict) and isinstance(after, dict):
            for key in sorted(before.keys() | after.keys()):
                nested = path + "/" + key.replace("~", "~0").replace("/", "~1")
                if key not in before:
                    changes.append(ConfigChange(op="add", path=nested, before=None, after=after[key]))
                elif key not in after:
                    changes.append(ConfigChange(op="remove", path=nested, before=before[key], after=None))
                else:
                    visit(before[key], after[key], nested)
        elif canonical_bytes(before) != canonical_bytes(after):
            changes.append(ConfigChange(op="replace", path=path, before=before, after=after))
    visit(plain(definition_payload(candidate)), plain(definition_payload(other)), "")
    return tuple(changes)
