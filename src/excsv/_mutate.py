"""File-level mutation: #@ meta, #$ SQL, #% aggregations, ## comments, and
formula materialize/dematerialize."""

from __future__ import annotations

from ._document import Aggregation, Document
from ._errors import ErrorKind, fail
from ._formula import (
    FormulaError,
    format_formula_result,
    formula_referenced_names,
    formula_value_from_cell,
    fv_null,
)

def set_file_meta(doc: Document, key: str, value: str) -> None:
    from ._parse import _upsert_kv

    _upsert_kv(doc.meta.file_meta, key, value)


def remove_file_meta(doc: Document, key: str) -> bool:
    found = False
    out = []
    for kv in doc.meta.file_meta:
        if kv.key == key:
            found = True
            continue
        out.append(kv)
    doc.meta.file_meta = out
    return found


def set_sql(doc: Document, raw_key: str, payload: str) -> None:
    from ._sql import parse_sql_line

    raw_key = raw_key.strip()
    if not raw_key:
        raise fail(ErrorKind.SQL_MISSING_COLON, 0, "empty SQL key")
    stmt = parse_sql_line("#$" + raw_key + ": " + payload, 0)
    found = False
    for i, s in enumerate(doc.meta.sql):
        if s.raw_key == raw_key:
            doc.meta.sql[i] = stmt
            found = True
    if not found:
        doc.meta.sql.append(stmt)


def remove_sql(doc: Document, raw_key: str) -> bool:
    found = False
    out = []
    for s in doc.meta.sql:
        if s.raw_key == raw_key:
            found = True
            continue
        out.append(s)
    doc.meta.sql = out
    return found


def aggregation_by_name(doc: Document, name: str):
    for a in doc.meta.aggregations:
        if a.name == name:
            return a
    return None


def add_aggregation(doc: Document, name: str) -> bool:
    from ._agg import compute_aggregation_values

    if aggregation_by_name(doc, name) is not None:
        return False
    vals = compute_aggregation_values(doc, name)
    doc.meta.aggregations.append(Aggregation(name=name, values=vals))
    return True


def update_aggregation(doc: Document, name: str) -> None:
    from ._agg import compute_aggregation_values

    vals = compute_aggregation_values(doc, name)
    for a in doc.meta.aggregations:
        if a.name == name:
            a.values = vals
            return
    doc.meta.aggregations.append(Aggregation(name=name, values=vals))


def add_human_comment(doc: Document, text: str) -> None:
    text = text.strip()
    if not text:
        return
    if not text.startswith("##"):
        text = "## " + text
    doc.meta.human_comments.append(text)


def remove_human_comment(doc: Document, index: int) -> bool:
    if index < 0 or index >= len(doc.meta.human_comments):
        return False
    del doc.meta.human_comments[index]
    return True


def remove_aggregation(doc: Document, name: str) -> bool:
    found = False
    out = []
    for a in doc.meta.aggregations:
        if a.name == name:
            found = True
            continue
        out.append(a)
    doc.meta.aggregations = out
    return found


def materialize_column(doc: Document, name: str) -> None:
    """Writes a virtual computed column's formula output into the data as an
    ordinary trailing column (and header cell), and sets materialized=1.
    formula= is kept either way."""
    from ._agg import cell_at, is_null_cell
    from ._formula import parse_formula

    idx, col, ok = doc.column_by_name(name)
    if not ok:
        raise ValueError(f"unknown column: {name}")
    expr = col.attrs.get("formula", "")
    if not expr:
        raise ValueError(f"column {name} has no formula=, nothing to materialize")
    if col.attrs.get("materialized") == "1":
        raise ValueError(f"column {name} is already materialized")
    if not doc.data.has_header_row:
        raise fail(ErrorKind.FORMULA_REQUIRES_HEADER, col.line, "formula= requires header=1")

    try:
        node = parse_formula(expr)
    except FormulaError as e:
        raise fail(ErrorKind.FORMULA_PARSE_ERROR, col.line, str(e))
    refs = formula_referenced_names(node)
    ref_idx: dict[str, int] = {}
    ref_type: dict[str, str] = {}
    for ref in refs:
        _, ref_def, ok2 = doc.column_by_name(ref)
        if not ok2:
            raise fail(ErrorKind.FORMULA_UNKNOWN_REFERENCE, col.line, "formula references unknown column " + ref)
        if ref_def.attrs.get("formula"):
            raise fail(ErrorKind.FORMULA_REFERENCES_COMPUTED, col.line,
                       "formula references computed column " + ref + " (chaining is not supported)")
        ref_idx[ref] = doc.column_index(ref)
        ref_type[ref] = ref_def.attrs.get("type", "")

    results: list[str] = []
    for r, row in enumerate(doc.data.rows):
        env = {}
        for ref in refs:
            raw = cell_at(doc, row, ref_idx[ref])
            env[ref] = fv_null() if is_null_cell(doc, raw) else formula_value_from_cell(raw, ref_type[ref])
        try:
            v = node.eval(env)
        except FormulaError as e:
            raise ValueError(f"materialize {name}: row {r + 1}: {e}")
        try:
            cell = format_formula_result(col.attrs.get("type", ""), col.attrs.get("format", ""), v, doc.header.null)
        except FormulaError as e:
            raise ValueError(f"materialize {name}: row {r + 1}: {e}")
        results.append(cell)

    for r in range(len(doc.data.rows)):
        doc.data.rows[r].append(results[r])
    header_cell = col.attrs.get("title") or col.attrs.get("name", "")
    doc.data.header_row.append(header_cell)

    doc.meta.columns[idx].attrs["materialized"] = "1"
    moved = doc.meta.columns.pop(idx)
    doc.meta.columns.append(moved)

    doc.sync_derived()


def dematerialize_column(doc: Document, name: str) -> None:
    """Removes a materialized computed column's physical data and clears
    materialized= back to absent. formula= is kept."""
    idx, col, ok = doc.column_by_name(name)
    if not ok:
        raise ValueError(f"unknown column: {name}")
    if not col.attrs.get("formula"):
        raise ValueError(f"column {name} has no formula=, nothing to dematerialize")
    if col.attrs.get("materialized") != "1":
        raise ValueError(f"column {name} is not materialized")
    phys_idx = doc.column_index(name)
    if doc.data.has_header_row and phys_idx < len(doc.data.header_row):
        del doc.data.header_row[phys_idx]
    for row in doc.data.rows:
        if phys_idx < len(row):
            del row[phys_idx]
    doc.meta.columns[idx].attrs.pop("materialized", None)
    doc.sync_derived()


Document.set_file_meta = set_file_meta
Document.remove_file_meta = remove_file_meta
Document.set_sql = set_sql
Document.remove_sql = remove_sql
Document.aggregation_by_name = aggregation_by_name
Document.add_aggregation = add_aggregation
Document.update_aggregation = update_aggregation
Document.add_human_comment = add_human_comment
Document.remove_human_comment = remove_human_comment
Document.remove_aggregation = remove_aggregation
Document.materialize_column = materialize_column
Document.dematerialize_column = dematerialize_column
