"""Typed table joins with explicit public and private projections."""
from collections.abc import Mapping
from dataclasses import replace
from .identity import typed_key
from .errors import ValidationError


def validate_table(table):
    from .records import ValidationIssue, ValidationReport
    issues = []
    def error(path, message):
        issues.append(ValidationIssue(path=path, message=message, severity="error"))
    if not table.key or len(set(table.key)) != len(table.key):
        error("/key", "Table key must contain distinct columns")
    if not set(table.key) <= table.schema.keys():
        error("/key", "Key columns are absent from the table schema")
    seen = set()
    for index, row in enumerate(table.rows):
        if set(row) != set(table.schema):
            error(f"/rows/{index}", "Row columns must match the declared schema")
        for column, kind in table.schema.items():
            if column not in row:
                continue
            value = row[column]
            scalar = {"str": str, "int": int, "float": float, "bool": bool}.get(kind)
            if scalar is not None and type(value) is not scalar:
                error(f"/rows/{index}/{column}", f"Expected strict {kind}, received {type(value).__name__}")
        try:
            key = typed_key(row, table.key)
            if key in seen:
                error(f"/rows/{index}", "Duplicate typed table key")
            seen.add(key)
        except (KeyError, ValueError) as exc:
            error(f"/rows/{index}", str(exc))
    return ValidationReport(issues=tuple(issues))


def validate_dataset(dataset):
    from .records import ValidationIssue, ValidationReport
    issues = []
    def error(path, message):
        issues.append(ValidationIssue(path=path, message=message, severity="error"))
    tables = {"units": dataset.units, **dataset.records, **dataset.references}
    if "units" in dataset.records or "units" in dataset.references or set(dataset.records) & set(dataset.references):
        error("/records", "Public/private table names must be distinct and cannot shadow units")
    for name, table in tables.items():
        issues.extend(replace(issue, path=f"/{name}" + issue.path) for issue in validate_table(table).issues)
    if not dataset.unit_key or dataset.unit_key != dataset.units.key:
        error("/unit_key", "Root unit key must equal the units table key")
    if not set(dataset.cluster_by or dataset.unit_key) <= dataset.units.schema.keys():
        error("/cluster_by", "Clustering columns are absent from root units")
    roots = set()
    for row in dataset.units.rows:
        try:
            roots.add(typed_key(row, dataset.unit_key))
        except (KeyError, ValueError):
            pass  # The table validator already records the exact failing path.
    for name, table in {**dataset.records, **dataset.references}.items():
        if not set(dataset.unit_key) <= set(table.key):
            error(f"/{name}/key", "Related table keys must include every root unit key")
        for index, row in enumerate(table.rows):
            try:
                if typed_key(row, dataset.unit_key) not in roots:
                    error(f"/{name}/rows/{index}", "Related row does not resolve to a typed root unit")
            except (ValueError, KeyError):
                error(f"/{name}/rows/{index}", "Related row lacks a valid root key")
    public = {"units": dataset.units, **dataset.records}
    for name, columns in dataset.input_columns.items():
        if name not in public:
            error(f"/input_columns/{name}", "Only declared public tables may be sent to agents")
        elif len(set(columns)) != len(columns) or not set(columns) <= public[name].schema.keys():
            error(f"/input_columns/{name}", "Unknown or duplicated public input columns")
    return ValidationReport(issues=tuple(issues))


def from_records(*, id, units, unit_key, input_columns, records=None, references=None, cluster_by=None):
    from .records import DataTable, EvalDataset
    units = tuple(units)
    if not units:
        raise ValidationError("A dataset requires at least one root task")
    columns = tuple(units[0])
    schema = {}
    for column in columns:
        kinds = {type(row.get(column)) for row in units}
        schema[column] = {str: "str", int: "int", float: "float", bool: "bool"}.get(next(iter(kinds)), "json") if len(kinds) == 1 else "json"
    dataset = EvalDataset(id=id, units=DataTable(rows=units, key=unit_key, schema=schema), unit_key=unit_key,
        input_columns=input_columns, records=records or {}, references=references or {}, cluster_by=cluster_by)
    dataset.validate().raise_for_errors()
    return dataset


def _key(dataset, unit):
    if not isinstance(unit, Mapping) or set(unit) != set(dataset.unit_key):
        raise ValidationError("A unit selector must contain exactly the declared root keys")
    try:
        key = typed_key(unit, dataset.unit_key)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    if key not in {typed_key(row, dataset.unit_key) for row in dataset.units.rows}:
        raise ValidationError("The requested typed unit key is not in the dataset")
    return key


def agent_input(dataset, unit):
    from .records import AgentInput
    dataset.validate().raise_for_errors()
    key = _key(dataset, unit)
    tables = {"units": dataset.units, **dataset.records}
    projected = {name: tuple({column: row[column] for column in columns}
        for row in tables[name].rows if typed_key(row, dataset.unit_key) == key)
        for name, columns in dataset.input_columns.items()}
    return AgentInput(unit=unit, tables=projected)


def select(dataset, units):
    dataset.validate().raise_for_errors()
    keys = {_key(dataset, unit) for unit in units}
    def subset(table):
        return replace(table, rows=tuple(row for row in table.rows if typed_key(row, dataset.unit_key) in keys))
    return replace(dataset, units=subset(dataset.units), records={name: subset(table) for name, table in dataset.records.items()},
                   references={name: subset(table) for name, table in dataset.references.items()})
