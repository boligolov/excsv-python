"""Document.validate: every conformance check in one pass, findings + repair hints."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ._agg import cell_at, compute_aggregation_values, data_rows, is_null_cell
from ._checksum import verify_checksum
from ._column import parse_attr_int
from ._dialect import classify_checksum_field
from ._document import ColumnDef, Document
from ._errors import ErrorKind, Issue, ParseError, new_issue
from ._formula import FormulaError, formula_referenced_names, parse_formula
from ._schema import check_column_values, compare_numeric, check_typed_value
from ._sidecar_util import header_reference
from ._sql import KNOWN_SQL_VERBS, is_known_dialect

KNOWN_COLUMN_TYPES = {
    "string", "int", "long", "float", "double",
    "decimal", "boolean", "date", "time",
    "datetime", "uuid", "binary",
}

IMPLEMENTED_VERSIONS = {"0.2", "0.3", "0.4", "0.5"}


@dataclass
class ValidateOptions:
    with_data: bool = False
    columns: list[str] = field(default_factory=list)


@dataclass
class Finding:
    issue: Issue
    repair: str = ""


@dataclass
class ValidateReport:
    findings: list[Finding] = field(default_factory=list)
    table: str = ""
    with_data: bool = False

    def ok(self) -> bool:
        return not self.findings

    def repair_command(self) -> str:
        seen = set()
        targets = []
        for f in self.findings:
            if not f.repair or f.repair in seen:
                continue
            seen.add(f.repair)
            targets.append(f.repair)
        if not targets:
            return ""
        return "fix --only " + ",".join(sorted(targets))


def is_data_level_warning(kind: ErrorKind) -> bool:
    return kind in (
        ErrorKind.ROWS_MISMATCH,
        ErrorKind.CHECKSUM_MISMATCH,
        ErrorKind.SIDECAR_CHECKSUM_MISMATCH,
        ErrorKind.DEFAULT_WITH_NULLS,
    )


def validate(doc: Document, opts: ValidateOptions | None = None) -> ValidateReport:
    opts = opts or ValidateOptions()
    report = ValidateReport(with_data=opts.with_data or bool(opts.columns))
    if doc is None:
        return report

    def add(issue: Issue, repair: str = "") -> None:
        report.findings.append(Finding(issue=issue, repair=repair))

    for iss in _check_declarations(doc):
        add(iss)
    for u in doc.meta.unknown:
        add(new_issue(ErrorKind.UNKNOWN_META_LINE, u.line,
                       "unrecognized meta line carried through verbatim: " + u.text))
    if not report.with_data:
        return report

    try:
        scope = _resolve_column_scope(doc, opts.columns)
    except ValueError as e:
        add(new_issue(ErrorKind.COLUMN_VALUE_INVALID, 0, str(e)))
        return report

    for iss in _check_schema_scoped(doc, scope):
        add(iss)
    for iss in _check_unique(doc, scope):
        add(iss)
    if doc.header.rows is not None and doc.row_count() != doc.header.rows:
        add(new_issue(ErrorKind.ROWS_MISMATCH, 1,
                       f"rows={doc.header.rows} but the data section has {doc.row_count()} rows"), "rows")
    if doc.header.checksum is not None:
        try:
            verify_checksum(doc.serialize_data_section(), doc.header.checksum)
        except ParseError as e:
            kind = ErrorKind.CHECKSUM_MISMATCH
            if e.issue.kind == ErrorKind.CHECKSUM_UNKNOWN_ALGORITHM:
                kind = ErrorKind.CHECKSUM_UNKNOWN_ALGORITHM
            add(new_issue(kind, 1, str(e.issue)), "checksum")
            if kind == ErrorKind.CHECKSUM_MISMATCH:
                for col in doc.meta.columns:
                    if col.attrs.get("formula") and col.attrs.get("materialized") == "1":
                        add(new_issue(ErrorKind.COMPUTED_STALE, col.line,
                                       "column " + col.attrs.get("name", "") +
                                       ": materialized value may not reflect current formula output"))
    for iss in _check_aggregations(doc):
        add(iss, "agg")
    for iss in _check_computed_materialization(doc):
        add(iss)
    return report


def _check_declarations(doc: Document) -> list[Issue]:
    issues: list[Issue] = []
    if doc.header.has_magic_line and doc.header.version and doc.header.version not in IMPLEMENTED_VERSIONS:
        issues.append(new_issue(ErrorKind.UNKNOWN_VERSION, 1, "unknown version=" + doc.header.version))
    from ._document import Profile

    ref = header_reference(doc.header)
    if ref and doc.source.profile == Profile.INLINE:
        issues.append(new_issue(ErrorKind.REFERENCE_ON_INLINE, 1, "inline document must not set reference="))
    cs = doc.header.fields.get("checksum", "")
    if cs:
        _, kind = classify_checksum_field(cs)
        if kind is not None:
            issues.append(new_issue(kind, 1, "checksum=" + cs))

    seen_name: dict[str, int] = {}
    seen_index: dict[str, int] = {}
    for col in doc.meta.columns:
        issues.extend(_check_column_declaration(col))
        name = col.attrs.get("name", "")
        if name:
            if name in seen_name:
                issues.append(new_issue(ErrorKind.DUPLICATE_COLUMN, col.line,
                                          f"duplicate #column name={name} (also at line {seen_name[name]})"))
            else:
                seen_name[name] = col.line
        idx = col.attrs.get("index", "")
        if idx:
            if idx in seen_index:
                issues.append(new_issue(ErrorKind.DUPLICATE_COLUMN, col.line,
                                          f"colliding #column index={idx} (also at line {seen_index[idx]})"))
            else:
                seen_index[idx] = col.line
    for stmt in doc.meta.sql:
        if stmt.verb not in KNOWN_SQL_VERBS:
            issues.append(new_issue(ErrorKind.SQL_UNKNOWN_VERB, stmt.line, "unknown #$ verb " + stmt.verb))
        if stmt.qualified and stmt.dialect and not is_known_dialect(stmt.dialect):
            issues.append(new_issue(ErrorKind.SQL_UNKNOWN_DIALECT, stmt.line, "unknown SQL dialect " + stmt.dialect))
    issues.extend(_check_computed_columns(doc))
    return issues


def _check_computed_columns(doc: Document) -> list[Issue]:
    issues: list[Issue] = []
    by_name = {col.attrs["name"]: col for col in doc.meta.columns if col.attrs.get("name")}
    for col in doc.meta.columns:
        expr = col.attrs.get("formula", "")
        if not expr:
            continue
        name = col.attrs.get("name", "")
        label = f"column {name}: "

        if col.attrs.get("default") or col.attrs.get("required"):
            issues.append(new_issue(ErrorKind.COMPUTED_DEFAULT_IGNORED, col.line,
                                      label + "default=/required= is ignored on a computed column"))

        try:
            node = parse_formula(expr)
        except FormulaError as e:
            issues.append(new_issue(ErrorKind.FORMULA_PARSE_ERROR, col.line, label + str(e)))
            continue
        for ref in formula_referenced_names(node):
            target = by_name.get(ref)
            if target is None:
                issues.append(new_issue(ErrorKind.FORMULA_UNKNOWN_REFERENCE, col.line,
                                          label + "formula references unknown column " + ref))
                continue
            if target.attrs.get("formula"):
                issues.append(new_issue(ErrorKind.FORMULA_REFERENCES_COMPUTED, col.line,
                                          label + "formula references computed column " + ref +
                                          " (chaining is not supported)"))
    return issues


def _check_computed_materialization(doc: Document) -> list[Issue]:
    issues: list[Issue] = []
    for col in doc.meta.columns:
        if not col.attrs.get("formula"):
            continue
        name = col.attrs.get("name", "")
        materialized = col.attrs.get("materialized") == "1"
        has_data = _column_has_physical_data(doc, name)
        if materialized != has_data:
            issues.append(new_issue(ErrorKind.COMPUTED_MATERIALIZED_MISMATCH, col.line,
                                      f"column {name}: materialized= disagrees with whether physical data is present"))
    return issues


def _column_has_physical_data(doc: Document, name: str) -> bool:
    if not name or not doc.data.has_header_row:
        return False
    return name in doc.data.header_row


def _check_column_declaration(col: ColumnDef) -> list[Issue]:
    from ._warnings import KNOWN_COLUMN_ATTRS

    issues: list[Issue] = []
    name = col.attrs.get("name") or col.attrs.get("index", "")
    label = f"column {name}: "

    for k in col.attrs:
        if not k.startswith("x-") and k not in KNOWN_COLUMN_ATTRS:
            issues.append(new_issue(ErrorKind.COLUMN_UNKNOWN_ATTRIBUTE, col.line, label + "unknown attribute " + k))
    ct = col.attrs.get("type", "").strip().lower()
    if ct and ct not in KNOWN_COLUMN_TYPES:
        issues.append(new_issue(ErrorKind.COLUMN_UNKNOWN_TYPE, col.line, label + "unknown type=" + ct))
    pat = col.attrs.get("pattern", "")
    if pat:
        try:
            re.compile(pat)
        except re.error as e:
            issues.append(new_issue(ErrorKind.COLUMN_MALFORMED_ATTRIBUTE, col.line,
                                      label + "pattern does not compile: " + str(e)))
    min_v = col.attrs.get("min")
    max_v = col.attrs.get("max")
    if min_v is not None and max_v is not None and _compare_bound_values(ct, min_v, max_v) > 0:
        issues.append(new_issue(ErrorKind.COLUMN_MALFORMED_ATTRIBUTE, col.line, label + "min= is greater than max="))
    len_min, ok_min = parse_attr_int(col.attrs.get("len_min"))
    len_max, ok_max = parse_attr_int(col.attrs.get("len_max"))
    if ok_min and ok_max and len_min > len_max:
        issues.append(new_issue(ErrorKind.COLUMN_MALFORMED_ATTRIBUTE, col.line,
                                  label + "len_min= is greater than len_max="))
    default = col.attrs.get("default", "")
    if default:
        msg = check_typed_value(col, default)
        if msg:
            issues.append(new_issue(ErrorKind.COLUMN_MALFORMED_ATTRIBUTE, col.line, label + "default= is " + msg))
    enum = col.attrs.get("enum", "")
    if enum:
        for part in enum.split("|"):
            msg = check_typed_value(col, part)
            if msg:
                issues.append(new_issue(ErrorKind.COLUMN_MALFORMED_ATTRIBUTE, col.line,
                                          label + "enum value " + part + " is " + msg))
    return issues


def _compare_bound_values(column_type: str, a: str, b: str) -> int:
    if column_type in ("int", "long", "float", "double", "decimal", "number"):
        return compare_numeric(a, b)
    return -1 if a < b else (1 if a > b else 0)


def _resolve_column_scope(doc: Document, refs: list[str]):
    if not refs:
        return None
    scope = set()
    for ref in refs:
        scope.add(doc.column_index(ref))
    return scope


def _check_schema_scoped(doc: Document, scope) -> list[Issue]:
    if not doc.meta.columns:
        return []
    issues: list[Issue] = []
    width = doc.column_width()
    for col in range(width):
        if scope is not None and col not in scope:
            continue
        d, ok = doc.column_def_at(col)
        if not ok:
            continue
        issues.extend(check_column_values(doc, col, d))
    return issues


def _check_unique(doc: Document, scope) -> list[Issue]:
    issues: list[Issue] = []
    width = doc.column_width()
    for col in range(width):
        if scope is not None and col not in scope:
            continue
        d, ok = doc.column_def_at(col)
        if not ok or d.attrs.get("unique") != "1":
            continue
        name = d.attrs.get("name") or str(col)
        seen: dict[str, int] = {}
        for r, row in enumerate(data_rows(doc)):
            v = cell_at(doc, row, col)
            if is_null_cell(doc, v):
                continue
            line = r + 1
            if doc.data.has_header_row:
                line += 1
            if v in seen:
                issues.append(new_issue(ErrorKind.COLUMN_NOT_UNIQUE, line,
                                          f"column {name}: unique=1 but value {v!r} repeats line {seen[v]}"))
                continue
            seen[v] = line
    return issues


def _check_aggregations(doc: Document) -> list[Issue]:
    issues: list[Issue] = []
    for agg in doc.meta.aggregations:
        try:
            want = compute_aggregation_values(doc, agg.name)
        except ValueError:
            continue
        if len(want) != len(agg.values):
            issues.append(new_issue(ErrorKind.AGG_STALE, agg.line,
                                      f"#%{agg.name} has {len(agg.values)} values, recomputation yields {len(want)}"))
            continue
        for i in range(len(want)):
            if want[i] != agg.values[i]:
                issues.append(new_issue(ErrorKind.AGG_STALE, agg.line,
                                          f"#%{agg.name} column {i} is {agg.values[i]!r}, recomputation yields {want[i]!r}"))
    return issues


Document.validate = validate
